"""Trusted broker. Generated code runs only in separate, networkless containers."""

import base64
import hashlib
import http.client
import json
import os
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

IMAGE = os.environ.get("SANDBOX_IMAGE", "sentinelops-inspection-sandbox:local")
TOKEN = (
    Path(os.environ.get("SANDBOX_TOKEN_FILE", "/run/secrets/sandbox_token"))
    .read_text()
    .strip()
)
SLOTS = threading.BoundedSemaphore(2)
MAX_INPUT = 256 * 1024
MAX_OUTPUT = 64 * 1024
WRAPPER = r"""
import base64, contextlib, io, json, os, resource
resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
payload = json.loads(base64.b64decode("".join(os.environ.pop("INPUT_%d" % i) for i in range(int(os.environ.pop("INPUT_COUNT"))))))
class Output(io.StringIO):
    def write(self, value):
        if self.tell() + len(value) > 16384:
            raise ValueError("stdout_limit")
        return super().write(value)
capture = Output()
namespace = {"data": payload["data"], "__name__": "__main__"}
try:
    with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
        exec(compile(payload["code"], "<inspection>", "exec"), namespace)
    reply = {"result": namespace.get("result"), "stdout": capture.getvalue()}
    encoded = json.dumps(reply, allow_nan=False)
    if len(encoded.encode()) > 32768:
        raise ValueError("result_limit")
    print(encoded)
except BaseException as exc:
    print(json.dumps({"error_code": "python_execution_failed", "error_type": type(exc).__name__}))
"""


class DockerConnection(http.client.HTTPConnection):
    def __init__(self, timeout=10):
        super().__init__("localhost", timeout=timeout)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect("/var/run/docker.sock")


def docker(method, path, body=None, limit=MAX_OUTPUT, timeout=10):
    connection = DockerConnection(timeout)
    try:
        connection.request(
            method,
            "/v1.45" + path,
            body=json.dumps(body) if body else None,
            headers={"Content-Type": "application/json"},
        )
        reply = connection.getresponse()
        raw = reply.read(limit + 1)
        if len(raw) > limit or reply.status >= 300:
            raise ValueError("sandbox_engine_error")
        return raw
    finally:
        connection.close()


def execute(payload):
    if set(payload) != {"code", "data"} or not isinstance(payload["code"], str):
        raise ValueError("sandbox_invalid_program")
    if len(payload["code"].encode()) > 32768:
        raise ValueError("sandbox_program_too_large")
    encoded = base64.b64encode(json.dumps(payload, allow_nan=False).encode()).decode()
    chunks = [encoded[i : i + 8192] for i in range(0, len(encoded), 8192)]
    configuration = {
        "Image": IMAGE,
        "User": "65534:65534",
        "WorkingDir": "/tmp",
        "Entrypoint": ["python", "-I", "-u", "-c", WRAPPER],
        "Cmd": [],
        "Env": ["INPUT_COUNT=" + str(len(chunks))]
        + [f"INPUT_{i}={chunk}" for i, chunk in enumerate(chunks)],
        "NetworkDisabled": True,
        "Tty": True,
        "Labels": {"sentinelops.inspection-sandbox": "true"},
        "HostConfig": {
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "Memory": 128 * 1024 * 1024,
            "MemorySwap": 128 * 1024 * 1024,
            "NanoCpus": 500000000,
            "PidsLimit": 16,
            "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=16m,mode=1777"},
            "Ulimits": [{"Name": "fsize", "Soft": 1048576, "Hard": 1048576}],
            "LogConfig": {
                "Type": "json-file",
                "Config": {"max-size": "64k", "max-file": "1"},
            },
        },
    }
    identifier = json.loads(docker("POST", "/containers/create", configuration))["Id"]
    try:
        docker("POST", f"/containers/{identifier}/start")
        try:
            state = json.loads(
                docker(
                    "POST",
                    f"/containers/{identifier}/wait?condition=not-running",
                    timeout=8,
                )
            )
        except TimeoutError:
            return {"error_code": "python_timeout"}
        if state["StatusCode"] != 0:
            return {"error_code": "python_process_failed"}
        raw = docker("GET", f"/containers/{identifier}/logs?stdout=1&stderr=1")
        try:
            result = json.loads(raw)
            if not isinstance(result, dict) or len(raw) > 32768:
                raise ValueError()
            return result
        except ValueError:
            return {"error_code": "python_invalid_output"}
    finally:
        docker("DELETE", f"/containers/{identifier}?force=1&v=1")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()

    def do_POST(self):
        if self.path != "/execute" or not secrets.compare_digest(
            self.headers.get("Authorization", ""), "Bearer " + TOKEN
        ):
            self.send_error(403)
            return
        if not SLOTS.acquire(blocking=False):
            self.send_error(429)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_INPUT:
                raise ValueError("sandbox_input_limit")
            payload = json.loads(self.rfile.read(length))
            result = execute(payload)
            result["code_sha256"] = hashlib.sha256(payload["code"].encode()).hexdigest()
            raw = json.dumps(result, allow_nan=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except (ValueError, OSError, KeyError, TypeError, http.client.HTTPException):
            self.send_error(400, "sandbox_execution_rejected")
        finally:
            SLOTS.release()


if __name__ == "__main__":
    service = ThreadingHTTPServer(("0.0.0.0", 8090), Handler)
    service.socket.settimeout(15)
    service.serve_forever()
