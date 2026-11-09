"""Loopback-only HTTPS smart Git fixture, with an independent local CA."""

import base64
import ipaddress
import os
import ssl
import subprocess
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


class GitServer:
    def __init__(self, root, credentials=None):
        self.root, self.credentials, self.attempts = root, credentials, []
        self.credential_checks = []
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        now = datetime.now(UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName("localhost"),
                        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    ]
                ),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        self.ca_file = root / "test-ca.pem"
        self.ca_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_file = root / "test-key.pem"
        key_file.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.handle_git()

            def do_POST(self):
                self.handle_git()

            def handle_git(self):
                if fixture.credentials:
                    authorization = self.headers.get("Authorization", "")
                    if authorization.startswith("Basic "):
                        supplied = base64.b64decode(authorization[6:]).decode().split(":", 1)
                        fixture.credential_checks.append(
                            [
                                supplied[0] == fixture.credentials[0],
                                len(supplied) == 2 and supplied[1] == fixture.credentials[1],
                            ]
                        )
                    expected = (
                        "Basic " + base64.b64encode(":".join(fixture.credentials).encode()).decode()
                    )
                    fixture.attempts.append(self.headers.get("Authorization") == expected)
                    if self.headers.get("Authorization") != expected:
                        self.send_response(401)
                        self.send_header("WWW-Authenticate", 'Basic realm="source-fixture"')
                        self.end_headers()
                        return
                url = urlsplit(self.path)
                length = int(self.headers.get("Content-Length", "0"))
                env = {
                    **os.environ,
                    "GIT_PROJECT_ROOT": str(fixture.root),
                    "GIT_HTTP_EXPORT_ALL": "1",
                    "PATH_INFO": url.path,
                    "QUERY_STRING": url.query,
                    "REQUEST_METHOD": self.command,
                    "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                    "CONTENT_LENGTH": str(length),
                    "REMOTE_USER": "fixture-reader",
                }
                result = subprocess.run(
                    ["git", "http-backend"],
                    input=self.rfile.read(length),
                    capture_output=True,
                    env=env,
                    timeout=20,
                )
                if result.returncode:
                    self.send_error(500)
                    return
                raw = result.stdout
                split = b"\r\n\r\n" if b"\r\n\r\n" in raw else b"\n\n"
                headers, body = raw.split(split, 1)
                lines = headers.decode().splitlines()
                status = next(
                    (int(line.split()[1]) for line in lines if line.startswith("Status:")), 200
                )
                self.send_response(status)
                for line in lines:
                    key, value = line.split(":", 1)
                    if key != "Status":
                        self.send_header(key, value.strip())
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.ca_file, key_file)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.url = f"https://localhost:{self.server.server_port}/repo.git"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
