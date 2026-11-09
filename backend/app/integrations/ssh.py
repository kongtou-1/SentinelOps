import base64
import io
import json
import re
import time

import paramiko
from cryptography.fernet import InvalidToken
from fastapi import HTTPException

from app.core.secrets import cipher


def private_key(secret, passphrase):
    for key_type in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
        try:
            return key_type.from_private_key(io.StringIO(secret), password=passphrase)
        except (paramiko.SSHException, ValueError):
            continue
    raise HTTPException(422, "Unsupported or invalid private key/passphrase")


def encrypt_credential(body):
    secret = body.secret.get_secret_value()
    passphrase = body.passphrase.get_secret_value() if body.passphrase else None
    if body.auth_type == "private_key":
        private_key(secret, passphrase)
    return (
        cipher().encrypt(json.dumps({"secret": secret, "passphrase": passphrase}).encode()).decode()
    )


def test_connection(server, credential):
    start = time.monotonic()
    try:
        data = json.loads(cipher().decrypt(credential.encrypted_secret.encode()))
    except (InvalidToken, ValueError):
        raise HTTPException(503, "Credential could not be decrypted") from None
    try:
        key_type, encoded, *_ = server.ssh_host_key.split()
        host_key = paramiko.PKey.from_type_string(key_type, base64.b64decode(encoded))
    except (ValueError, paramiko.SSHException, TypeError):
        raise HTTPException(422, "Invalid pinned host key") from None
    host_name = server.host if server.ssh_port == 22 else f"[{server.host}]:{server.ssh_port}"
    try:
        with paramiko.SSHClient() as client:
            client.get_host_keys().add(host_name, host_key.get_name(), host_key)
            kwargs = (
                {"password": data["secret"]}
                if credential.auth_type == "password"
                else {"pkey": private_key(data["secret"], data["passphrase"])}
            )
            client.connect(
                server.host,
                port=server.ssh_port,
                username=server.ssh_user,
                timeout=10,
                banner_timeout=10,
                auth_timeout=10,
                look_for_keys=False,
                allow_agent=False,
                **kwargs,
            )
        status = "ok"
    except paramiko.BadHostKeyException:
        status = "host_key_mismatch"
    except paramiko.AuthenticationException:
        status = "authentication_failed"
    except (paramiko.SSHException, OSError, EOFError):
        status = "connection_failed"
    return {
        "success": status == "ok",
        "status": status,
        "latency_ms": round((time.monotonic() - start) * 1000, 2),
    }


def read_container(server, credential, docker_id, logs=False):
    """Two fixed commands only; never accepts model supplied command fragments."""
    from app.utils.redaction import redact

    match = re.fullmatch(
        r"(?:/docker/|/system.slice/docker-)?([a-f0-9]{64})(?:\.scope)?", docker_id
    )
    if not match or not server.ssh_host_key or not credential:
        return {"error_code": "ssh_unavailable"}
    identifier = match[1]
    command = (
        f"docker logs --since 15m --tail 200 --timestamps {identifier}"
        if logs
        else "docker inspect --format '{{json .State}}' " + identifier
    )
    try:
        data = json.loads(cipher().decrypt(credential.encrypted_secret.encode()))
        kind, encoded, *_ = server.ssh_host_key.split()
        host_key = paramiko.PKey.from_type_string(kind, base64.b64decode(encoded))
        host = server.host if server.ssh_port == 22 else f"[{server.host}]:{server.ssh_port}"
        with paramiko.SSHClient() as client:
            client.get_host_keys().add(host, host_key.get_name(), host_key)
            kwargs = (
                {"password": data["secret"]}
                if credential.auth_type == "password"
                else {"pkey": private_key(data["secret"], data["passphrase"])}
            )
            client.connect(
                server.host,
                port=server.ssh_port,
                username=server.ssh_user,
                timeout=10,
                banner_timeout=10,
                auth_timeout=10,
                look_for_keys=False,
                allow_agent=False,
                **kwargs,
            )
            _, stdout, stderr = client.exec_command(command, timeout=10)
            channel = stdout.channel
            channel.settimeout(1)
            raw = bytearray()
            deadline = time.monotonic() + 10
            truncated = False
            while time.monotonic() < deadline:
                if channel.recv_ready():
                    raw.extend(channel.recv(min(4096, 16385 - len(raw))))
                if channel.recv_stderr_ready():
                    raw.extend(channel.recv_stderr(min(4096, 16385 - len(raw))))
                if len(raw) > 16384:
                    truncated = True
                    break
                if (
                    channel.exit_status_ready()
                    and not channel.recv_ready()
                    and not channel.recv_stderr_ready()
                ):
                    break
                time.sleep(0.01)
            else:
                channel.close()
                return {"error_code": "ssh_timeout"}
            if not truncated and channel.recv_exit_status() != 0:
                return {"error_code": "ssh_read_failed"}
            channel.close()
        text = raw[:16384].decode("utf-8", errors="replace")
        for secret in (data["secret"], data.get("passphrase")):
            if secret:
                text = text.replace(secret, "[REDACTED]")
        return {"logs" if logs else "state": redact(text), "truncated": truncated}
    except paramiko.BadHostKeyException:
        return {"error_code": "host_key_mismatch"}
    except (paramiko.SSHException, OSError, EOFError, InvalidToken, ValueError, HTTPException):
        return {"error_code": "ssh_unavailable"}
