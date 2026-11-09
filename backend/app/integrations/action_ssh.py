"""Fixed, bounded Docker operations over pinned SSH; no general command API."""

import base64
import json
import time

import paramiko

from app.core.secrets import cipher
from app.integrations.ssh import private_key
from app.services.executions.skills import docker_identifier
from app.utils.redaction import redact

INSPECT = "docker inspect --format '{{json .Id}} {{json .State}}' "


class ActionSSH:
    def __init__(self, server, credential, identifier):
        self.identifier = docker_identifier(identifier)
        self.client = paramiko.SSHClient()
        self.data = json.loads(cipher().decrypt(credential.encrypted_secret.encode()))
        kind, encoded, *_ = server.ssh_host_key.split()
        key = paramiko.PKey.from_type_string(kind, base64.b64decode(encoded, validate=True))
        host = server.host if server.ssh_port == 22 else f"[{server.host}]:{server.ssh_port}"
        self.client.get_host_keys().add(host, key.get_name(), key)
        kwargs = (
            {"password": self.data["secret"]}
            if credential.auth_type == "password"
            else {"pkey": private_key(self.data["secret"], self.data.get("passphrase"))}
        )
        try:
            self.client.connect(
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
        except Exception:
            self.client.close()
            raise

    def close(self):
        self.client.close()

    def _run(self, command, timeout):
        deadline = time.monotonic() + timeout
        _, stdout, _ = self.client.exec_command(command, timeout=timeout)
        channel = stdout.channel
        output, errors = bytearray(), bytearray()
        truncated = False
        try:
            while time.monotonic() < deadline:
                for ready, receive, buffer in (
                    (channel.recv_ready, channel.recv, output),
                    (channel.recv_stderr_ready, channel.recv_stderr, errors),
                ):
                    if ready():
                        chunk = receive(4096)
                        available = max(0, 16384 - len(buffer))
                        buffer.extend(chunk[:available])
                        truncated = truncated or len(chunk) > available
                if (
                    channel.exit_status_ready()
                    and not channel.recv_ready()
                    and not channel.recv_stderr_ready()
                ):
                    code = channel.recv_exit_status()
                    if code < 0:
                        raise EOFError("Missing exit status")
                    break
                time.sleep(0.01)
            else:
                raise TimeoutError("SSH deadline exceeded")
        finally:
            channel.close()
        result = {
            "exit_code": code,
            "stdout": output.decode("utf-8", errors="replace"),
            "stderr": errors.decode("utf-8", errors="replace"),
            "truncated": truncated,
        }
        for secret in (self.data["secret"], self.data.get("passphrase")):
            if secret:
                result["stdout"] = result["stdout"].replace(secret, "[REDACTED]")
                result["stderr"] = result["stderr"].replace(secret, "[REDACTED]")
        result = redact(result)
        for field in ("stdout", "stderr"):
            encoded = result[field].encode("utf-8")
            if len(encoded) > 16384:
                result["truncated"] = True
                result[field] = encoded[:16384].decode("utf-8", errors="ignore")
        return result

    def inspect(self, timeout=10):
        output = self._run(INSPECT + self.identifier, timeout)
        if output["exit_code"] or output["truncated"]:
            raise ValueError("inspect_failed")
        identifier, state = output["stdout"].strip().split(" ", 1)
        identifier, state = json.loads(identifier), json.loads(state)
        if identifier != self.identifier:
            raise ValueError("container_identity_changed")
        # Never retain Health.Log output: it may contain application credentials.
        return {
            "id": identifier,
            "running": state["Running"],
            "paused": state["Paused"],
            "restarting": state["Restarting"],
            "status": state["Status"],
            "health": state.get("Health", {}).get("Status"),
        }

    def restart(self):
        return self._run("docker restart -t 10 " + self.identifier, 30)
