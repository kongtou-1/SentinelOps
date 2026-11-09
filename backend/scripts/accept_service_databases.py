"""Run database tests against the dedicated testlab Compose project, never the business DB."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def environment():
    import secrets

    from cryptography.fernet import Fernet

    env_file = ROOT / ".local/databases-acceptance.env"
    env_file.parent.mkdir(exist_ok=True)
    if not env_file.exists():
        env_file.write_text("DATABASE_ACCEPTANCE_PASSWORD=" + secrets.token_urlsafe(32) + "\n")
    contents = env_file.read_text().strip()
    password = contents.split("=", 1)[1]
    key = ROOT / ".local/database-acceptance.key"
    if not key.exists():
        key.write_bytes(Fernet.generate_key())
    os.environ.update(
        POSTGRES_HOST="127.0.0.1",
        POSTGRES_PORT="15483",
        POSTGRES_USER="db_test",
        POSTGRES_PASSWORD=password,
        POSTGRES_DB="ops_databases_test",
        CREDENTIAL_KEY_FILE=str(key),
        DATABASE_ACCEPTANCE_PASSWORD=password,
        SERVICE_PROBE_SCHEDULER_ENABLED="false",
        LLM_PROBE_SCHEDULER_ENABLED="false",
    )
    font = ROOT / ".local/report-font/wqy-zenhei.ttc"
    if font.is_file():
        os.environ["REPORT_FONT_FILE"] = str(font)
    return password


def prepare_tls():
    import ipaddress
    from datetime import UTC, datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    folder = ROOT / ".local/database-acceptance-tls"
    folder.mkdir(exist_ok=True)
    if (folder / "server.key").exists():
        return
    now = datetime.now(UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SentinelOps test CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=7))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=7))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    (folder / "ca.crt").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (folder / "server.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (folder / "server.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )


def prepare(password):
    import psycopg
    import pymysql
    from psycopg import sql

    with psycopg.connect(
        host="127.0.0.1",
        port=15483,
        user="db_test",
        password=password,
        dbname="postgres",
        autocommit=True,
        connect_timeout=3,
        options="-c statement_timeout=10000",
    ) as db:
        if not db.execute("SELECT 1 FROM pg_database WHERE datname='business_test'").fetchone():
            db.execute("CREATE DATABASE business_test")
        if not db.execute("SELECT 1 FROM pg_roles WHERE rolname='db_reader'").fetchone():
            db.execute(
                sql.SQL("CREATE ROLE db_reader LOGIN PASSWORD {}").format(sql.Literal(password))
            )
    with psycopg.connect(
        host="127.0.0.1",
        port=15483,
        user="db_test",
        password=password,
        dbname="business_test",
        autocommit=True,
        connect_timeout=3,
        options="-c statement_timeout=10000",
    ) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS public.jobs (id int PRIMARY KEY, status varchar(30), "
            "retries int, updated_at timestamptz, payload text, api_token text)"
        )
        db.execute("TRUNCATE public.jobs")
        db.execute(
            "INSERT INTO public.jobs SELECT i,'waiting',2,now(),'example','private-token' "
            "FROM generate_series(1,300) i"
        )
        db.execute("COMMENT ON TABLE public.jobs IS '异步任务状态'")
        db.execute("GRANT USAGE ON SCHEMA public TO db_reader")
        db.execute("GRANT SELECT ON public.jobs TO db_reader")
    with (
        pymysql.connect(
            host="127.0.0.1",
            port=13386,
            user="root",
            password=password,
            database="business_test",
            autocommit=True,
            connect_timeout=3,
            read_timeout=10,
            write_timeout=3,
        ) as db,
        db.cursor() as c,
    ):
        c.execute("CREATE USER IF NOT EXISTS 'db_reader'@'%%' IDENTIFIED BY %s", (password,))
        c.execute(
            "CREATE TABLE IF NOT EXISTS jobs (id int PRIMARY KEY, status varchar(30), "
            "retries int, updated_at timestamp, payload text, api_token text) "
            "COMMENT='异步任务状态'"
        )
        c.execute("TRUNCATE jobs")
        c.executemany(
            "INSERT INTO jobs VALUES (%s,'waiting',2,now(),'example','private-token')",
            [(i,) for i in range(1, 301)],
        )
        c.execute("GRANT SELECT ON business_test.jobs TO 'db_reader'@'%'")


if __name__ == "__main__":
    password = environment()
    prepare_tls()
    if "--start" in sys.argv:
        sys.argv.remove("--start")
        subprocess.run(
            [
                "docker",
                "compose",
                "-p",
                "sentinelops-database-acceptance",
                "--env-file",
                str(ROOT / ".local/databases-acceptance.env"),
                "-f",
                str(ROOT / "testlab/compose.databases.yaml"),
                "up",
                "-d",
                "--wait",
            ],
            check=True,
            cwd=ROOT,
        )
    prepare(password)
    tests = sys.argv[1:] or [
        "backend/tests/test_service_databases.py",
        "backend/tests/test_project_structure.py",
    ]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *tests,
            "-q",
            "--tb=short",
            "-p",
            "no:cacheprovider",
            "--basetemp=" + str(ROOT / ".local" / ("database-pytest-" + uuid4().hex)),
        ],
        cwd=ROOT,
    )
    sys.exit(result.returncode)
