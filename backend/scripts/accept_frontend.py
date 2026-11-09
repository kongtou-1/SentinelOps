"""Run the real local operations loop in a fresh database, then restore normal services.

Uses the existing local admin login and mock Provider/Mailpit fixtures. It leaves the
test database and evidence for inspection; it never rewrites business records.
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import accept_executions
import accept_reports
from accept_monitoring import request, wait_for
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]


def main():
    sys.path.insert(0, str(ROOT / "backend"))
    from app.core.config import get_settings
    from app.models import Role, User

    browser = "--browser" in sys.argv
    database = "ops_frontend_" + uuid4().hex[:8] + "_test"
    settings = get_settings()
    source = create_engine(settings.database_url)
    credentials = (ROOT / ".local/admin-credentials.txt").read_text(encoding="utf-8")
    username = re.search(r"username\s*[:=]\s*(.+)", credentials).group(1).strip()
    names = [username]
    if browser:
        names += [
            r["username"]
            for r in json.loads((ROOT / ".local/frontend-browser-credentials.json").read_text())
        ]
    with Session(source) as db:
        users = [
            (u.id, u.username, u.password_hash, role)
            for u, role in db.execute(
                select(User, Role.code)
                .join(Role, User.role_id == Role.id)
                .where(User.username.in_(names), User.deleted_at.is_(None))
            )
        ]
    source.dispose()
    connection = create_engine(
        settings.database_url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    with connection.connect() as db:
        db.execute(text('CREATE DATABASE "' + database + '"'))
    connection.dispose()
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT / "backend",
        env={**os.environ, "POSTGRES_DB": database},
        check=True,
        capture_output=True,
    )
    target = create_engine(settings.database_url.set(database=database))
    with Session(target) as db:
        roles = {r.code: r.id for r in db.scalars(select(Role))}
        for identifier, name, hashed, role in users:
            db.add(User(id=identifier, username=name, password_hash=hashed, role_id=roles[role]))
        db.commit()
    target.dispose()
    override = ROOT / ".local/frontend-acceptance-compose.yaml"
    override.write_text(
        "services:\n"
        + "".join(
            f"  {service}:\n    environment:\n      POSTGRES_DB: {database}\n"
            for service in (
                "backend",
                "ai-worker",
                "prediction-worker",
                "execution-worker",
                "report-worker",
            )
        ),
        encoding="utf-8",
    )
    original = accept_executions.compose

    def isolated_compose(*args, test=False):
        if not test:
            return original(*args, test=False)
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                "compose.yaml",
                "-f",
                "compose.workers-standalone.yaml",
                "-f",
                "backend/ai-test-compose.yaml",
                "-f",
                "backend/execution-test-compose.yaml",
                "-f",
                str(override),
                "--profile",
                "monitoring",
                "--profile",
                "local-test",
                "--profile",
                "ai",
                "--profile",
                "execution",
                "--profile",
                "reports",
                *args,
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    accept_executions.compose = accept_reports.compose = isolated_compose
    finished = ROOT / ".local/frontend-browser-workflow.done"
    finished.unlink(missing_ok=True)

    def reports(evidence, token):
        accept_reports.report_steps(evidence, token)
        evidence["isolated_database"] = database
        if not browser:
            return
        row = request(
            f"/api/v1/incidents/{evidence['incident_id']}/reports", "POST", {"force": True}, token
        )
        path = "/api/v1/reports/" + row["id"]
        wait_for(
            "browser report draft",
            lambda: request(path, token=token)["status"] == "draft",
            seconds=90,
        )
        evidence["browser_report_id"] = row["id"]
        (ROOT / ".local/frontend-browser-workflow.json").write_text(
            json.dumps(
                {
                    "database": database,
                    "report_id": row["id"],
                    "incident_id": evidence["incident_id"],
                }
            ),
            encoding="utf-8",
        )
        print("Browser report fixture ready; awaiting local verification marker", flush=True)
        deadline = time.monotonic() + 600
        while not finished.exists() and time.monotonic() < deadline:
            time.sleep(2)
        if not finished.exists():
            raise RuntimeError("Browser verification marker timed out")
        evidence["browser_workflow"] = json.loads(finished.read_text(encoding="utf-8"))

    print("Fresh isolated acceptance database: " + database, flush=True)
    try:
        isolated_compose("up", "-d", "--no-build", "--wait", "prediction-worker", test=True)
        accept_executions.main(after_execution=reports, batch=11)
    finally:
        original(
            "up",
            "-d",
            "--no-build",
            "--wait",
            "backend",
            "ai-worker",
            "prediction-worker",
            "execution-worker",
            "report-worker",
        )
        login = dict(re.findall(r"(username|password)\s*[:=]\s*(.+)", credentials))
        token = request("/api/v1/auth/login", "POST", login)["access_token"]
        applied = request("/api/v1/monitoring/apply-rules", "POST", token=token)
        evidence_path = ROOT / ".local/batch11-acceptance.json"
        if evidence_path.exists():
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["normal_services_restored"] = True
            evidence["normal_rules_applied"] = applied["applied"]
            evidence_path.write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    print("Frontend integration loop passed; normal services restored", flush=True)


if __name__ == "__main__":
    main()
