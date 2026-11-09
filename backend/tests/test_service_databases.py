import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from test_ai import ai_config as ai_config
from test_ai import local_provider as local_provider

from app.db.session import SessionLocal
from app.integrations import database_readonly as adapter
from app.integrations.database_sql import checked_query
from app.models import (
    AgentAnalysis,
    AlertEvent,
    AnalysisConversation,
    AnalysisRun,
    Container,
    Incident,
    IncidentAlert,
    Server,
    ServiceDatabase,
    ServiceMonitor,
    User,
)
from app.schemas.service_databases import DatabaseQueryInput
from app.services import service_databases as service
from app.services.database_diagnosis import (
    database_evidence_valid,
    dispatch_database,
    freeze_database_context,
)

ALLOWED = {"public.jobs": ["id", "status", "retries", "updated_at", "payload"]}
SCHEMA = {"public.jobs": {"columns": [{"name": name} for name in ALLOWED["public.jobs"]]}}


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id, status FROM public.jobs WHERE retries>1 ORDER BY id DESC",
        "SELECT status, COUNT(*) AS total FROM public.jobs GROUP BY status ORDER BY total",
        "SELECT a.id,b.status FROM public.jobs a JOIN public.jobs b ON a.id=b.id",
        "SELECT MAX(updated_at) AS last_seen FROM public.jobs",
        "SELECT id FROM public.jobs WHERE retries>1 AND (status='waiting' OR status='failed')",
    ],
)
def test_select_validation(sql):
    executed, summary, hashed = checked_query(sql, "postgresql", ALLOWED, SCHEMA)
    assert "LIMIT" in executed and len(hashed) == 64
    assert summary


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM public.jobs",
        "SELECT id FROM public.jobs; DELETE FROM public.jobs",
        "DELETE FROM public.jobs RETURNING id",
        "SELECT id INTO new_jobs FROM public.jobs",
        "SELECT id FROM public.jobs FOR UPDATE",
        "SELECT id FROM public.jobs FOR SHARE",
        "SELECT id FROM other.jobs",
        "SELECT id FROM x.public.jobs",
        "SELECT api_token FROM public.jobs",
        "SELECT id FROM public.jobs WHERE api_token='secret'",
        "SELECT id FROM public.jobs ORDER BY api_token",
        "SELECT api_token AS id FROM public.jobs",
        "SELECT id FROM public.jobs GROUP BY api_token",
        "SELECT pg_sleep(100) FROM public.jobs",
        "SELECT id FROM public.jobs WHERE retries>1 AND pg_sleep(100)=0",
        "SELECT pg_catalog.now() FROM public.jobs",
        "SELECT id FROM public.jobs LIMIT 2 OFFSET 1",
        "WITH x AS (DELETE FROM public.jobs RETURNING id) SELECT id FROM x",
        "WITH x AS (SELECT id FROM public.jobs) SELECT id FROM x",
        "SELECT id FROM public.jobs WHERE id IN (SELECT id FROM public.jobs)",
        "SELECT id FROM public.jobs UNION SELECT id FROM public.jobs",
        "CALL test()",
        "SELECT id FROM public.jobs INTO OUTFILE '/tmp/leak'",
        "SELECT @a FROM public.jobs",
        "SELECT id FROM public.jobs WHERE 1=1 /* comment */; SELECT 2",
    ],
)
def test_reject_unsafe_sql(sql):
    with pytest.raises(ValueError):
        checked_query(sql, "postgresql", ALLOWED, SCHEMA)


@pytest.fixture
def application(client, admin_headers):
    result = client.post(
        "/api/v1/applications",
        headers=admin_headers,
        json={"code": "database-test", "name": "数据库服务"},
    )
    assert result.status_code == 201, result.text
    return result.json()["id"]


@pytest.fixture(params=["postgresql", "mysql"])
def payload(request):
    password = os.environ.get("DATABASE_ACCEPTANCE_PASSWORD")
    if not password:
        pytest.skip("Run scripts/accept_service_databases.py for isolated real database tests")
    return {
        "name": "只读业务库",
        "engine": request.param,
        "host": "127.0.0.1",
        "port": 15483 if request.param == "postgresql" else 13386,
        "database": "business_test",
        "username": "db_reader",
        "password": password,
        "environment": "test",
        "tls_mode": "disable",
        "description": "用于任务排查",
        "table_notes": {
            "public.jobs"
            if request.param == "postgresql"
            else "business_test.jobs": "status=waiting 表示等待处理"
        },
    }


def create(client, headers, application, payload):
    result = client.post(
        f"/api/v1/business-services/{application}/databases", headers=headers, json=payload
    )
    assert result.status_code == 201, result.text
    return result.json()


def enable(client, headers, row, payload):
    base = f"/api/v1/service-databases/{row['id']}"
    schema = client.post(base + "/schema/refresh", headers=headers)
    assert schema.status_code == 200, schema.text
    table = "public.jobs" if payload["engine"] == "postgresql" else "business_test.jobs"
    body = {k: v for k, v in payload.items() if k != "password"}
    body.update(query_enabled=True, allowed_tables={table: ["id", "status", "retries", "payload"]})
    result = client.put(base, headers=headers, json=body)
    assert result.status_code == 200, result.text
    return result.json(), body, table


def test_lifecycle_real_databases(client, admin_headers, application, payload):
    if payload["engine"] == "mysql":
        # The first non-TLS login must work without the server's SHA2 auth cache.
        with owner_connection(payload) as owner, owner.cursor() as cursor:
            cursor.execute("FLUSH PRIVILEGES")
    draft = client.post("/api/v1/service-databases/test", headers=admin_headers, json=payload)
    assert draft.status_code == 200, draft.text
    assert draft.json()["success"] and draft.json()["permission_status"] == "verified", draft.text
    assert os.environ["DATABASE_ACCEPTANCE_PASSWORD"] not in draft.text
    assert (
        client.get(
            f"/api/v1/business-services/{application}/databases", headers=admin_headers
        ).json()
        == []
    )
    row = create(client, admin_headers, application, payload)
    assert row["password_configured"] and not row["query_enabled"]
    assert "password" not in row and "encrypted_config" not in row
    with SessionLocal() as db:
        assert payload["password"] not in db.get(ServiceDatabase, UUID(row["id"])).encrypted_config
    row, body, table = enable(client, admin_headers, row, payload)
    base = f"/api/v1/service-databases/{row['id']}"
    schema = client.post(base + "/schema/refresh", headers=admin_headers).json()
    assert schema["table_notes"] == payload["table_notes"] and table in schema["tables"]
    assert schema["tables"][table]["comment"] == "异步任务状态"
    tested = client.post(
        "/api/v1/service-databases/test",
        headers=admin_headers,
        json={**body, "connection_id": row["id"]},
    )
    assert tested.json()["success"]
    assert client.get(base, headers=admin_headers).json()["last_test_success"] is True
    # A failed changed draft cannot overwrite the successful saved connection test.
    bad = client.post(
        "/api/v1/service-databases/test",
        headers=admin_headers,
        json={**body, "connection_id": row["id"], "password": "wrong-password"},
    )
    assert not bad.json()["success"]
    assert client.get(base, headers=admin_headers).json()["last_test_success"] is True
    assert client.delete(base, headers=admin_headers).status_code == 204
    assert client.get(base, headers=admin_headers).status_code == 404


def test_composite_primary_and_foreign_keys(client, admin_headers, application, payload):
    assert payload["database"].endswith("_test")
    namespace = "public" if payload["engine"] == "postgresql" else "business_test"
    with owner_connection(payload) as owner, owner.cursor() as cursor:
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS askdata_parent "
            "(tenant_id int, id int, PRIMARY KEY(tenant_id,id))"
        )
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS askdata_child "
            "(tenant_id int, parent_id int, PRIMARY KEY(tenant_id,parent_id), "
            "CONSTRAINT askdata_child_parent_fk FOREIGN KEY(tenant_id,parent_id) "
            "REFERENCES askdata_parent(tenant_id,id))"
        )
        if payload["engine"] == "postgresql":
            cursor.execute("GRANT SELECT ON askdata_parent,askdata_child TO db_reader")
        else:
            cursor.execute("GRANT SELECT ON business_test.askdata_parent TO 'db_reader'@'%'")
            cursor.execute("GRANT SELECT ON business_test.askdata_child TO 'db_reader'@'%'")
    try:
        row = create(client, admin_headers, application, payload)
        response = client.post(
            f"/api/v1/service-databases/{row['id']}/schema/refresh", headers=admin_headers
        )
        assert response.status_code == 200, response.text
        tables = response.json()["tables"]
        assert tables[f"{namespace}.askdata_parent"]["primary_key"] == ["tenant_id", "id"]
        child = tables[f"{namespace}.askdata_child"]
        assert child["primary_key"] == ["tenant_id", "parent_id"]
        assert child["foreign_keys"] == [
            {
                "name": "askdata_child_parent_fk",
                "columns": ["tenant_id", "parent_id"],
                "referenced_table": f"{namespace}.askdata_parent",
                "referenced_columns": ["tenant_id", "id"],
            }
        ]
    finally:
        with owner_connection(payload) as owner, owner.cursor() as cursor:
            cursor.execute("DROP TABLE askdata_child")
            cursor.execute("DROP TABLE askdata_parent")


def test_platform_connector_with_upstream_catalog(client, admin_headers, application, payload):
    from auto_ontology.catalog.extract import extract_tabular_db_data

    from app.models import AskDataModelVersion
    from app.services.askdata import database_connector

    row = create(client, admin_headers, application, payload)
    row, _, table = enable(client, admin_headers, row, payload)
    with SessionLocal() as db:
        actor = db.scalar(select(User).where(User.username == "admin"))
        model = AskDataModelVersion(
            connection_id=UUID(row["id"]),
            created_by=actor.id,
            config_version=row["config_version"],
            schema_hash=row["schema_hash"],
            status="draft",
        )
        db.add(model)
        db.commit()
        actor_id, version_id = actor.id, model.id
    connector = database_connector(actor_id, UUID(row["id"]), version_id)
    catalog = extract_tabular_db_data(connector)
    assert catalog["database_name"] == row["id"]
    assert set(catalog["columns"]["column_name"]) == {"id", "status", "retries", "payload"}
    assert catalog["pks"]["column_name"].tolist() == ["id"]
    assert "api_token" not in catalog["columns"]["column_name"].tolist()
    result = connector.execute(f"SELECT id FROM {table} ORDER BY id LIMIT 1")
    assert result.iloc[0, 0] == "1"
    assert result.attrs["truncated"] is False
    with pytest.raises(ValueError):
        connector.execute(f"SELECT api_token FROM {table}")


@pytest.mark.parametrize("role", ["viewer", "operator"])
def test_permissions(client, admin_headers, application, payload, make_user, login, role):
    row = create(client, admin_headers, application, payload)
    make_user(role, role)
    headers = {"Authorization": "Bearer " + login(role)["access_token"]}
    base = f"/api/v1/service-databases/{row['id']}"
    assert client.get(base, headers=headers).status_code == 200
    assert client.get(base + "/schema", headers=headers).status_code == 200
    assert client.post(base + "/schema/refresh", headers=headers).status_code == 403
    assert client.put(base, headers=headers, json=payload).status_code == 403
    assert (
        client.post("/api/v1/service-databases/test", headers=headers, json=payload).status_code
        == 403
    )
    assert client.delete(base, headers=headers).status_code == 403


def incident_fixture(db, application_id):
    now = datetime.now(UTC)
    server = Server(
        name="db-server",
        host="127.0.0.1",
        environment="test",
        node_exporter_target="node:9100",
        cadvisor_target="cadvisor:8080",
    )
    db.add(server)
    db.flush()
    container = Container(
        server_id=server.id,
        application_id=UUID(application_id),
        docker_id="db-target",
        name="worker",
        image="worker:v1",
        status="running",
        labels={},
        last_seen_at=now,
    )
    db.add(container)
    db.flush()
    alert = AlertEvent(
        fingerprint=uuid4().hex,
        severity="warning",
        status="firing",
        starts_at=now,
        server_id=server.id,
        labels={"container_id": str(container.id)},
        annotations={},
        rule_snapshot={},
    )
    incident = Incident(
        title="任务等待异常",
        status="open",
        severity="warning",
        started_at=now,
        last_alert_started_at=now,
    )
    db.add_all([alert, incident])
    db.flush()
    db.add(IncidentAlert(incident_id=incident.id, alert_event_id=alert.id))
    db.commit()
    return incident, alert, container


def test_query_evidence_revocation_and_scope(client, admin_headers, application, payload):
    row = create(client, admin_headers, application, payload)
    row, _, table = enable(client, admin_headers, row, payload)
    with SessionLocal() as db:
        incident, alert, container = incident_fixture(db, application)
        context = freeze_database_context(db, incident.id)
        assert len(context["connections"]) == 1  # No source repository or deployment is required.
        user = db.scalar(select(User))
        run = AgentAnalysis(
            incident_id=incident.id,
            requested_by=user.id,
            input_hash="test",
            config={},
            status="running",
            started_at=datetime.now(UTC),
            database_context=context,
        )
        db.add(run)
        db.commit()
        tool = DatabaseQueryInput(
            connection_id=UUID(row["id"]),
            sql=f"SELECT status,COUNT(*) AS total FROM {table} GROUP BY status",
        )
        evidence = dispatch_database(db, run, "query_database_readonly", tool)
        assert evidence.get("rows") == [["waiting", "300"]], evidence
        assert evidence["state"] == "at_query_time" and evidence["content_hash"]
        item = {"source": "query_database_readonly", "data": evidence}
        assert database_evidence_valid(db, context, item, run_started_at=run.started_at)
        assert "waiting" not in evidence["sql"]
        assert not database_evidence_valid(db, context, item, run_started_at=datetime.now(UTC))
        with service.connection_lock(UUID(row["id"])):
            with ThreadPoolExecutor(max_workers=1) as pool:

                def try_lock():
                    with (
                        pytest.raises(HTTPException) as error,
                        service.connection_lock(UUID(row["id"])),
                    ):
                        pass
                    assert error.value.status_code == 409

                pool.submit(try_lock).result()
        # Host-level alerts do not inherit access to colocated services.
        alert.labels = {}
        db.commit()
        assert not freeze_database_context(db, incident.id)["connections"]
        connection = db.get(ServiceDatabase, UUID(row["id"]))
        connection.enabled = False
        connection.config_version += 1
        db.commit()
        rejected = dispatch_database(db, run, "query_database_readonly", tool)
        assert rejected["error_code"] == "database_configuration_changed"
        assert not database_evidence_valid(db, context, item)


def test_reject_privileged_account_and_tls(client, admin_headers, application, payload):
    row = create(client, admin_headers, application, payload)
    row, body, _ = enable(client, admin_headers, row, payload)
    body.update(username="db_test" if payload["engine"] == "postgresql" else "root")
    unsafe = client.put(f"/api/v1/service-databases/{row['id']}", headers=admin_headers, json=body)
    assert unsafe.status_code == 422
    tls = client.post(
        "/api/v1/service-databases/test",
        headers=admin_headers,
        json={**payload, "tls_mode": "verify-full"},
    )
    assert not tls.json()["success"]  # Untrusted self-signed/no server certificate.


def test_query_result_bounds(client, admin_headers, application, payload):
    row = create(client, admin_headers, application, payload)
    row, _, table = enable(client, admin_headers, row, payload)
    with SessionLocal() as db:
        connection = db.get(ServiceDatabase, UUID(row["id"]))
        sql, _, _ = checked_query(
            f"SELECT id FROM {table} LIMIT 10000",
            payload["engine"],
            connection.allowed_tables,
            connection.schema_cache["tables"],
        )
        with adapter.connect(connection) as remote:
            result = adapter.query(remote, sql)
        assert len(result["rows"]) == 200
        assert len(json.dumps(result).encode()) < adapter.MAX_BYTES


def test_trusted_tls(client, admin_headers, application, payload):
    ca = Path(__file__).resolve().parents[2] / ".local/database-acceptance-tls/ca.crt"
    result = client.post(
        "/api/v1/service-databases/test",
        headers=admin_headers,
        json={
            **payload,
            "tls_mode": "verify-full",
            "ca_certificate": ca.read_text(),
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["success"] and result.json()["permission_status"] == "verified", (
        result.text
    )


@contextmanager
def owner_connection(payload):
    import psycopg
    import pymysql

    config = dict(host=payload["host"], port=payload["port"], password=payload["password"])
    if payload["engine"] == "postgresql":
        with psycopg.connect(
            **config, dbname=payload["database"], user="db_test", autocommit=True
        ) as connection:
            yield connection
    else:
        with pymysql.connect(
            **config, database=payload["database"], user="root", autocommit=True
        ) as connection:
            yield connection


def test_timeout_size_and_privilege_revocation(client, admin_headers, application, payload):
    row = create(client, admin_headers, application, payload)
    row, _, table = enable(client, admin_headers, row, payload)
    with SessionLocal() as db:
        connection = db.get(ServiceDatabase, UUID(row["id"]))
        # Exercise server timeouts directly; sleep functions are forbidden to AI.
        with pytest.raises(adapter.DatabaseUnavailable, match="query_timeout"):
            with adapter.connect(connection) as remote:
                adapter.query(
                    remote,
                    "SELECT pg_sleep(6)"
                    if payload["engine"] == "postgresql"
                    else f"SELECT id FROM {table} WHERE SLEEP(6)=0 LIMIT 1",
                )
        if payload["engine"] == "postgresql":
            import time

            started = time.monotonic()
            with pytest.raises(adapter.DatabaseUnavailable, match="query_timeout"):
                with adapter.connect(connection) as remote:
                    adapter.query(remote, f"SELECT id,pg_sleep(0.1) FROM {table} LIMIT 200")
            assert time.monotonic() - started < 7
        with adapter.connect(connection) as remote:
            result = adapter.query(
                remote, f"SELECT REPEAT(payload, 5000) AS large FROM {table} LIMIT 200"
            )
        assert result["truncated"] and len(json.dumps(result).encode()) < adapter.MAX_BYTES
        target = "db_reader" if payload["engine"] == "postgresql" else "'db_reader'@'%'"
        with owner_connection(payload) as owner, owner.cursor() as cursor:
            cursor.execute(f"GRANT UPDATE ON {table} TO {target}")
            try:
                with adapter.connect(connection) as remote:
                    assert adapter.permissions(connection, remote) == "unsafe"
            finally:
                cursor.execute(f"REVOKE UPDATE ON {table} FROM {target}")


def test_service_isolation_new_fields_and_metadata_only(
    client, admin_headers, application, payload
):
    first = create(client, admin_headers, application, payload)
    second = create(client, admin_headers, application, {**payload, "name": "任务库"})
    other_app = client.post(
        "/api/v1/applications",
        headers=admin_headers,
        json={"code": "other-service", "name": "其他服务"},
    ).json()["id"]
    other = create(client, admin_headers, other_app, payload)
    base = f"/api/v1/service-databases/{first['id']}"
    client.post(base + "/schema/refresh", headers=admin_headers)
    with SessionLocal() as db:
        incident, alert, container = incident_fixture(db, application)
        context = freeze_database_context(db, incident.id)
        assert {c["connection_id"] for c in context["connections"]} == {first["id"], second["id"]}
        entry = next(c for c in context["connections"] if c["connection_id"] == first["id"])
        assert entry["schema"] and not entry["query_enabled"]
        run = SimpleNamespace(database_context=context, started_at=datetime.now(UTC))
        with pytest.raises(PermissionError):
            dispatch_database(
                db,
                run,
                "query_database_readonly",
                DatabaseQueryInput(connection_id=UUID(other["id"]), sql="SELECT 1"),
            )
    first, body, table = enable(client, admin_headers, first, payload)
    with owner_connection(payload) as owner, owner.cursor() as cursor:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN new_unapproved int")
        try:
            schema = client.post(base + "/schema/refresh", headers=admin_headers).json()
            current = client.get(base, headers=admin_headers).json()
            assert "new_unapproved" not in current["allowed_tables"][table]
            with pytest.raises(ValueError):
                checked_query(
                    f"SELECT id FROM {table} WHERE new_unapproved=1",
                    payload["engine"],
                    current["allowed_tables"],
                    schema["tables"],
                )
        finally:
            cursor.execute(f"ALTER TABLE {table} DROP COLUMN new_unapproved")


def test_workbench_adoption_report_and_safe_events(client, admin_headers, application, payload):
    from app.services.diagnosis.conversations import adopt
    from app.services.diagnosis.tools import DiagnosticTools
    from app.services.reports.logic import snapshot

    row = create(client, admin_headers, application, payload)
    row, _, table = enable(client, admin_headers, row, payload)
    with SessionLocal() as db:
        incident, alert, container = incident_fixture(db, application)
        user = db.scalar(select(User))
        conversation = AnalysisConversation(
            incident_id=incident.id, owner_id=user.id, title="数据库诊断"
        )
        db.add(conversation)
        db.flush()
        context = freeze_database_context(db, incident.id)
        run = AnalysisRun(
            conversation_id=conversation.id,
            incident_id=incident.id,
            requested_by=user.id,
            config={},
            status="running",
            started_at=datetime.now(UTC),
            database_context=context,
        )
        db.add(run)
        db.commit()
        events = []
        tools = DiagnosticTools(
            run.id, row_model=AnalysisRun, on_event=lambda kind, value: events.append((kind, value))
        )
        queried = tools.call(
            "query_database_readonly",
            {
                "connection_id": row["id"],
                "sql": f"SELECT id FROM {table} WHERE status='private-filter-value'",
            },
        )
        assert "error_code" not in queried["data"], queried
        assert "private-filter-value" not in json.dumps([events, tools.calls])
        run.status, run.evidence = "completed", tools.evidence
        run.finished_at = datetime.now(UTC)
        run.candidate = {
            "summary": "已核对任务状态",
            "possible_causes": [],
            "impact": "待核实",
            "evidence_ids": [queried["id"]],
            "knowledge_ids": [],
            "suggested_checks": [],
            "data_gaps": [],
            "proposal": None,
            "requires_human_approval": True,
        }
        db.commit()
        analysis = adopt(db, run.id, user)
        assert analysis.database_context == context and analysis.evidence[0]["id"] == queried["id"]
        report_context = snapshot(db, incident)
        record = next(r for r in report_context["records"] if r["kind"] == "analysis")
        assert record["data"]["database_context"] == context
        assert record["data"]["evidence"][0]["data"]["state"] == "at_query_time"
        run.status = "cancelled"
        db.commit()
        before = len(tools.evidence)
        with pytest.raises(ValueError):
            tools.call(
                "query_database_readonly",
                {"connection_id": row["id"], "sql": f"SELECT id FROM {table}"},
            )
        assert len(tools.evidence) == before


def test_environment_ambiguity(client, admin_headers, application, payload):
    create(client, admin_headers, application, payload)
    with SessionLocal() as db:
        incident, alert, container = incident_fixture(db, application)
        second = Server(
            name="production-server",
            host="127.0.0.2",
            environment="production",
            node_exporter_target="node2:9100",
            cadvisor_target="cadvisor2:8080",
        )
        db.add(second)
        db.flush()
        db.add(
            Container(
                server_id=second.id,
                application_id=UUID(application),
                docker_id="other",
                name="other",
                image="worker",
                labels={},
                status="running",
                last_seen_at=datetime.now(UTC),
            )
        )
        monitor = ServiceMonitor(
            application_id=UUID(application), name="API", target="http://example.test"
        )
        db.add(monitor)
        db.flush()
        alert.service_monitor_id = monitor.id
        alert.server_id, alert.labels = None, {}
        db.commit()
        context = freeze_database_context(db, incident.id)
        assert (
            not context["connections"] and "database_environment_ambiguous" in context["data_gaps"]
        )


def test_lock_across_processes():
    import subprocess
    import sys

    identifier = uuid4()
    code = (
        "from app.services.service_databases import connection_lock\n"
        "from fastapi import HTTPException\n"
        "try:\n"
        f"    with connection_lock('{identifier}'): print('unexpected')\n"
        "except HTTPException as exc: print(exc.status_code)\n"
    )
    with service.connection_lock(identifier):
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[1],
            timeout=20,
        )
    assert result.returncode == 0 and result.stdout.strip() == "409"


@pytest.mark.parametrize("chat", [False, True])
def test_real_harness_database_tools(
    client, admin_headers, application, payload, ai_config, local_provider, chat
):
    from app.workers.ai import analyze_once
    from app.workers.conversations import conversation_once

    row = create(client, admin_headers, application, payload)
    enable(client, admin_headers, row, payload)
    with SessionLocal() as db:
        incident, _, _ = incident_fixture(db, application)
        identifier = str(incident.id)
    local_provider.scenario = "database_diagnosis"
    if chat:
        conversation = client.post(
            f"/api/v1/incidents/{identifier}/analysis-conversations", headers=admin_headers, json={}
        ).json()["id"]
        message = client.post(
            f"/api/v1/analysis-conversations/{conversation}/messages",
            headers=admin_headers,
            json={"client_message_id": str(uuid4()), "content": "请核对任务状态"},
        )
        assert message.status_code == 202, message.text
        run_id = message.json()["run_id"]
        assert conversation_once()
        result = client.get(f"/api/v1/analysis-runs/{run_id}", headers=admin_headers).json()
        assert result["status"] == "completed", result
        adopted = client.post(f"/api/v1/analysis-runs/{run_id}/adopt", headers=admin_headers)
        assert adopted.status_code == 200, adopted.text
        result = adopted.json()
    else:
        response = client.post(
            f"/api/v1/incidents/{identifier}/analyze", headers=admin_headers, json={}
        )
        assert response.status_code == 202, response.text
        assert analyze_once()
        result = client.get(
            f"/api/v1/analyses/{response.json()['id']}", headers=admin_headers
        ).json()
    assert result["status"] == "completed", result
    evidence = next(e for e in result["evidence"] if e["source"] == "query_database_readonly")
    assert evidence["data"]["rows"] == [["waiting", "300"]]
    assert evidence["id"] in result["result"]["evidence_ids"]
    repeat = client.post(f"/api/v1/incidents/{identifier}/analyze", headers=admin_headers, json={})
    assert repeat.status_code == 202 and repeat.json()["id"] != result["id"]
