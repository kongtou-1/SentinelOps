"""Loopback HTTPS SVN fixture. Requires Apache with mod_dav_svn and the SVN CLI."""

import ipaddress
import os
import shutil
import socket
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


class SvnServer:
    def __init__(self, root, credentials=None):
        modules = Path("/usr/lib/apache2/modules")
        if not all(shutil.which(name) for name in ("svn", "svnadmin", "apache2", "htpasswd")):
            pytest.skip("HTTPS SVN integration requires svn, svnadmin, apache2 and htpasswd")
        if not (modules / "mod_dav_svn.so").exists():
            pytest.skip("HTTPS SVN integration requires Apache mod_dav_svn")
        self.root, self.credentials = root, credentials
        self.repository, self.work = root / "repository", root / "work"
        subprocess.run(
            ["svnadmin", "create", str(self.repository)], check=True, capture_output=True
        )
        self.svn("checkout", self.repository.as_uri(), str(self.work))
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
                    [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
                ),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        self.ca_file, key_file = root / "ca.pem", root / "key.pem"
        self.ca_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_file.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        key_file.chmod(0o600)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.url = f"https://localhost:{self.port}/repo"
        auth = "Require all granted"
        if credentials:
            password_file = root / "passwords"
            subprocess.run(
                ["htpasswd", "-i", "-c", "-B", str(password_file), credentials[0]],
                input=(credentials[1] + "\n").encode(),
                check=True,
                capture_output=True,
            )
            auth = (
                'AuthType Basic\nAuthName "source-fixture"\nAuthBasicProvider file\n'
                f'AuthUserFile "{password_file}"\nRequire valid-user'
            )
        self.config = root / "httpd.conf"
        loaded = "\n".join(
            f'LoadModule {name}_module "{modules}/mod_{name}.so"'
            for name in (
                "mpm_event",
                "authz_core",
                "authn_core",
                "authn_file",
                "auth_basic",
                "authz_user",
                "dav",
                "dav_svn",
                "ssl",
            )
        )
        self.config.write_text(
            f'''ServerRoot "{root}"
DefaultRuntimeDir "{root}"
PidFile "{root}/httpd.pid"
ErrorLog "{root}/error.log"
ServerName localhost
Listen 127.0.0.1:{self.port}
User #{os.getuid()}
Group #{os.getgid()}
{loaded}
SSLSessionCache none
<VirtualHost 127.0.0.1:{self.port}>
SSLEngine on
SSLCertificateFile "{self.ca_file}"
SSLCertificateKeyFile "{key_file}"
<Location /repo>
DAV svn
SVNPath "{self.repository}"
{auth}
</Location>
</VirtualHost>
''',
            encoding="utf-8",
        )

    def svn(self, *arguments):
        return subprocess.run(
            ["svn", "--non-interactive", "--no-auth-cache", *arguments],
            cwd=self.work if self.work.exists() else self.root,
            check=True,
            capture_output=True,
            timeout=15,
        ).stdout

    def commit(self, message):
        self.svn("add", "--force", ".")
        self.svn("commit", "-m", message)
        self.svn("update", "--ignore-externals")

    def __enter__(self):
        self.process = subprocess.Popen(
            ["apache2", "-f", str(self.config), "-X"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(100):
            if self.process.poll() is not None:
                raise RuntimeError((self.root / "error.log").read_text())
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.1):
                    return self
            except OSError:
                time.sleep(0.05)
        self.__exit__()
        raise RuntimeError("HTTPS SVN fixture did not start")

    def __exit__(self, *_args):
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
