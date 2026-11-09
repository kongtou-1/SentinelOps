"""Local API + read-only PostgreSQL demo. No model or real certificate data is used."""

import json
import secrets
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".local/testlab/inspection-multisource"
APP = "9501216c-a795-4220-b06e-69ab00fe5ac2"
API = "http://127.0.0.1:18088/api/v1"
TABLE, READER = "inspection_demo_pages", "inspection_demo_reader"


def write(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    settings = dict(
        line.split("=", 1)
        for line in (ROOT / ".local/inspections-test.env").read_text().splitlines()
    )
    assert settings["POSTGRES_DB"].endswith("_test"), "isolated_test_database_required"
    password_file = OUT / "reader.key"
    if not password_file.exists():
        password_file.write_text(secrets.token_urlsafe(32), encoding="utf-8")
    password = password_file.read_text(encoding="utf-8")
    at = datetime.now(UTC) - timedelta(minutes=1)
    covers = [
        {
            "id": f"DEMO-CERT-00{i}",
            "version": "cover-1",
            "updated_at": at.isoformat(),
            "original_verdict": "pass",
            "facts": {"instrument": "DEMO-A", "sample_kind": "constructed_acceptance"},
            "evidence": [{"source": "constructed_cover_fixture"}],
        }
        for i in (1, 2, 3)
    ]
    write("snapshot.json", {"observed": covers, "replays": []})
    with psycopg.connect(
        host=settings["POSTGRES_HOST"],
        port=settings["POSTGRES_PORT"],
        dbname=settings["POSTGRES_DB"],
        user=settings["POSTGRES_USER"],
        password=settings["POSTGRES_PASSWORD"],
        autocommit=True,
    ) as db:
        assert db.execute("SELECT current_database()").fetchone()[0].endswith("_test")
        if not db.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (READER,)).fetchone():
            db.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(READER), sql.Literal(password)
                )
            )
        db.execute(
            sql.SQL(
                "CREATE TABLE IF NOT EXISTS public.{} (id text PRIMARY KEY, version text NOT NULL, "
                "certificate_id text NOT NULL, instrument text NOT NULL, "
                "error numeric NOT NULL, unit text NOT NULL, "
                "created_at timestamptz NOT NULL DEFAULT now(), "
                "updated_at timestamptz NOT NULL DEFAULT now(), deleted_at timestamptz)"
            ).format(sql.Identifier(TABLE))
        )
        db.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(READER)))
        db.execute(
            sql.SQL("GRANT SELECT ON public.{} TO {}").format(
                sql.Identifier(TABLE), sql.Identifier(READER)
            )
        )
        for cert, errors in (
            ("DEMO-CERT-001", ("0.03", "0.04")),
            ("DEMO-CERT-002", ("0.03", "0.08")),
        ):
            for index, error in enumerate(errors):
                db.execute(
                    sql.SQL(
                        "INSERT INTO public.{} "
                        "(id,version,certificate_id,instrument,error,unit,created_at,updated_at) "
                        "VALUES (%s,'1',%s,'DEMO-A',%s,'mm',%s,%s) ON CONFLICT (id) DO NOTHING"
                    ).format(sql.Identifier(TABLE)),
                    (
                        f"{cert}-P{index + 1}",
                        cert,
                        error,
                        at - timedelta(days=60),
                        at - timedelta(days=60),
                    ),
                )
    subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(ROOT / "testlab/compose.inspections-multisource.yaml"),
            "up",
            "-d",
            "--wait",
        ],
        check=True,
        cwd=ROOT,
    )
    credentials = dict(
        line.split("=", 1)
        for line in (ROOT / ".local/admin-credentials.txt").read_text().splitlines()
        if "=" in line
    )
    with httpx.Client(timeout=20, trust_env=False) as client:

        def api(method, path, body=None):
            response = client.request(method, API + path, json=body)
            if response.status_code >= 400:
                raise RuntimeError(f"local_api_failed:{path}:{response.status_code}")
            return response.json() if response.content else None

        token = api(
            "POST", "/auth/login", {key: credentials[key] for key in ("username", "password")}
        )
        client.headers["Authorization"] = "Bearer " + token["access_token"]
        connection_body = {
            "name": "巡检验收内页库（构造数据）",
            "engine": "postgresql",
            "host": "host.docker.internal",
            "port": int(settings["POSTGRES_PORT"]),
            "database": settings["POSTGRES_DB"],
            "username": READER,
            "password": password,
            "tls_mode": "disable",
            "environment": "local-test",
            "enabled": True,
            "purpose": "仅供本机多源巡检软件验收，构造证书内页；独立只读账号",
        }
        connection = next(
            (
                r
                for r in api("GET", f"/business-services/{APP}/databases")
                if r["username"] == READER
            ),
            None,
        )
        if connection is None:
            connection = api("POST", f"/business-services/{APP}/databases", connection_body)
        api("POST", f"/service-databases/{connection['id']}/schema/refresh")
        columns = ["id", "version", "updated_at", "certificate_id", "instrument", "error", "unit"]
        connection = api(
            "PUT",
            f"/service-databases/{connection['id']}",
            {
                **{key: value for key, value in connection_body.items() if key != "password"},
                "query_enabled": True,
                "allowed_tables": {"public." + TABLE: columns},
            },
        )
        assert connection["permission_status"] == "verified", "readonly_permission_unverified"
        plan_body = {
            "name": "独立多源验收·证书样例（构造数据）",
            "application_id": None,
            "environment": "local-test",
            "enabled": True,
            "source_kind": "api",
            "source_config": {"url": "http://inspection-multisource-api:8090/records"},
            "interval_seconds": 3600,
            "lookback_seconds": 86400,
            "max_records": 10,
            "agent_enabled": False,
            "related_sources": [
                {
                    "name": "pages",
                    "source_kind": "database",
                    "source_config": {
                        "connection_id": connection["id"],
                        "table": "public." + TABLE,
                        "id_column": "id",
                        "version_column": "version",
                        "time_column": "updated_at",
                        "fact_columns": ["certificate_id", "instrument", "error", "unit"],
                    },
                    "match_field": "certificate_id",
                    "cardinality": "many",
                    "required": True,
                }
            ],
            "rules": [
                {
                    "code": "measurement",
                    "title": "逐项目误差检查",
                    "kind": "numeric_range",
                    "path": "pages.*.error",
                    "maximum": "0.05",
                    "unit": "mm",
                    "unit_path": "pages.*.unit",
                    "basis": "软件验收构造限值 v1，不代表实际计量规程",
                },
                {
                    "code": "instrument",
                    "title": "封面与内页仪器一致",
                    "kind": "field_equals",
                    "path": "pages.*.instrument",
                    "compare_path": "main.instrument",
                    "basis": "同一对象资料一致性",
                },
            ],
        }
        previous = next(
            (
                p
                for p in api("GET", "/inspection-plans?limit=100")
                if p["name"] == plan_body["name"]
            ),
            None,
        )
        if previous:
            plan_body["expected_version"] = previous["version"]
        plan = api(
            "PUT" if previous else "POST",
            "/inspection-plans" + ("/" + previous["id"] if previous else ""),
            plan_body,
        )
        evidence = {"plan_id": plan["id"], "connection_id": connection["id"], "model_calls": 0}
        try:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                runs = api("GET", f"/inspection-runs?plan_id={plan['id']}")
                matching = [r for r in runs if r["plan_version"] == plan["version"]]
                if matching and matching[0]["status"] not in {"queued", "collecting", "evaluating"}:
                    evidence["run"] = matching[0]
                    break
                time.sleep(5)
            assert "run" in evidence and evidence["run"]["status"] == "completed", (
                "automatic_run_not_completed"
            )
            items = api("GET", "/inspection-items?record_id=DEMO-CERT&plan_id=" + plan["id"])
            expected = {
                "DEMO-CERT-001": "supported",
                "DEMO-CERT-002": "contradicted",
                "DEMO-CERT-003": "unassessable",
            }
            assert {r["record_id"]: r["verdict"] for r in items} == expected
            assert all(r["agent_status"] == "disabled" for r in items)
            evidence["items"] = items
        finally:
            current = next(
                r for r in api("GET", "/inspection-plans?limit=100") if r["id"] == plan["id"]
            )
            if current["enabled"]:
                current = api(
                    "PUT",
                    f"/inspection-plans/{plan['id']}/state",
                    {"enabled": False, "expected_version": current["version"]},
                )
            evidence["paused"] = not current["enabled"]
            write("result.json", evidence)
        print(
            json.dumps(
                {
                    "plan_id": plan["id"],
                    "paused": evidence["paused"],
                    "results": {r["record_id"]: r["verdict"] for r in evidence["items"]},
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
