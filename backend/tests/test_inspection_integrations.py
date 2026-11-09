import json
import secrets
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from app.core.config import get_settings
from app.core.secrets import cipher
from app.db.session import SessionLocal, engine
from app.models import (
    Application,
    InspectionItem,
    InspectionRun,
    KnowledgeSkill,
    ModelUsage,
    ServiceDatabase,
    User,
)
from app.schemas.inspections import InspectionPlanInput
from app.services import service_databases
from app.services.inspections import agent, plans, runtime, sources


def drain():
    for _ in range(12):
        if not runtime.run_once():
            return
    raise AssertionError("Worker did not finish")


@pytest.mark.parametrize("bind_service", [True, False])
def test_database_without_metadata_columns_scans_and_detects_changes(
    client, admin_headers, bind_service
):
    suffix = uuid4().hex[:10]
    table, role = "inspection_plain_" + suffix, "inspection_reader_" + suffix
    password = secrets.token_urlsafe(24)
    key = "public." + table
    with engine.begin() as remote:
        remote.exec_driver_sql(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}'")
        remote.exec_driver_sql(
            f'CREATE TABLE public."{table}" (business_no integer PRIMARY KEY, amount numeric)'
        )
        remote.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        remote.exec_driver_sql(f'GRANT SELECT ON public."{table}" TO "{role}"')
        remote.exec_driver_sql(f'INSERT INTO public."{table}" VALUES (2, 10), (10, 20)')
    try:
        settings = get_settings()
        cache = {"tables": {key: {"columns": [{"name": c} for c in ["business_no", "amount"]]}}}
        with SessionLocal() as db:
            app = Application(code="plain-inspection", name="Plain inspection", enabled=True)
            db.add(app)
            db.flush()
            connection = ServiceDatabase(
                application_id=app.id if bind_service else None,
                name="No metadata fixture",
                engine="postgresql",
                host=settings.postgres_host,
                port=settings.postgres_port,
                database=settings.postgres_db,
                username=role,
                tls_mode="disable",
                encrypted_config=cipher()
                .encrypt(json.dumps({"password": password}).encode())
                .decode(),
                environment="test",
                query_enabled=True,
                enabled=True,
                permission_status="verified",
                allowed_tables={key: ["business_no", "amount"]},
                schema_cache=cache,
                schema_hash=service_databases.digest(cache),
            )
            db.add(connection)
            db.commit()
            connection_id = str(connection.id)
            application_id = str(app.id)
        options = client.get("/api/v1/inspection-connections", headers=admin_headers)
        assert options.status_code == 200, options.text
        assert connection_id in {row["id"] for row in options.json()}
        body = {
            "name": "Scan existing business fields",
            "environment": "test",
            "enabled": True,
            "max_records": 1,
            "source_kind": "database",
            "source_config": {
                "connection_id": connection_id,
                "table": key,
                "id_column": "business_no",
                "fact_columns": ["amount"],
            },
            "rules": [
                {
                    "code": "amount",
                    "title": "Amount",
                    "kind": "required",
                    "path": "amount",
                    "basis": "Fixture",
                }
            ],
        }
        dataset = client.post(
            "/api/v1/inspection-data-sources",
            headers=admin_headers,
            json={
                "name": "Reusable read-only database",
                "environment": "test",
                "source_kind": "database",
                "source_config": body["source_config"],
            },
        )
        assert dataset.status_code == 201, dataset.text
        if not bind_service:
            scoped = client.post(
                "/api/v1/inspection-data-sources",
                headers=admin_headers,
                json={
                    "name": "Service query using independent connection",
                    "application_id": application_id,
                    "environment": "test",
                    "source_kind": "database",
                    "source_config": body["source_config"],
                },
            )
            assert scoped.status_code == 201, scoped.text
        tested = client.post(
            f"/api/v1/inspection-data-sources/{dataset.json()['id']}/test",
            headers=admin_headers,
        )
        assert tested.json()["success"], tested.text
        response = client.post(
            "/api/v1/inspection-plans",
            headers=admin_headers,
            json={**body, "data_source_id": dataset.json()["id"]},
        )
        assert response.status_code == 201, response.text
        plan = response.json()
        assert plan["source_config"]["version_column"] == ""
        assert plan["source_config"]["time_column"] == ""

        def run():
            response = client.post(
                f"/api/v1/inspection-plans/{plan['id']}/runs", headers=admin_headers
            )
            assert response.status_code == 202, response.text
            drain()
            run_id = response.json()["id"]
            detail = client.get(f"/api/v1/inspection-runs/{run_id}", headers=admin_headers).json()
            assert detail["status"] in {"completed", "partial"}, detail
            return client.get(
                f"/api/v1/inspection-runs/{run_id}/items", headers=admin_headers
            ).json()

        first, second, end = run(), run(), run()
        assert [first[0]["record_id"], second[0]["record_id"]] == ["2", "10"]
        assert end == []
        evidence = first[0]["evidence"]["evidence"][0]
        assert evidence["time_basis"] == "query_time"
        assert evidence["version_basis"] == "content_hash"
        assert run()[0]["record_id"] == "2"  # Each batch keeps its own observations.
        assert run()[0]["record_id"] == "10"
        assert run() == []
        with engine.begin() as remote:
            remote.exec_driver_sql(f'UPDATE public."{table}" SET amount=11 WHERE business_no=2')
        changed = run()
        assert changed[0]["record_id"] == "2"
        assert changed[0]["record_version"] != first[0]["record_version"]
        assert changed[0]["evidence"]["facts"]["amount"] == "11"
        invalid = {**body, "source_config": {**body["source_config"], "time_column": "updated_at"}}
        assert (
            client.post("/api/v1/inspection-plans", headers=admin_headers, json=invalid).status_code
            == 422
        )
    finally:
        with engine.begin() as remote:
            remote.exec_driver_sql(f'DROP TABLE IF EXISTS public."{table}"')
            remote.exec_driver_sql(f'DROP OWNED BY "{role}"')
            remote.exec_driver_sql(f'DROP ROLE "{role}"')


def test_real_readonly_database_pagination_and_scope(client, admin_headers):
    suffix = uuid4().hex[:10]
    table_name, role_name = "inspection_records_" + suffix, "inspection_reader_" + suffix
    password = secrets.token_urlsafe(24)
    with engine.begin() as connection:
        connection.exec_driver_sql(f"CREATE ROLE \"{role_name}\" LOGIN PASSWORD '{password}'")
        connection.exec_driver_sql(
            f'CREATE TABLE public."{table_name}" '
            "(id text PRIMARY KEY, version text, updated_at timestamptz, "
            "error numeric, unit text, decision text)"
        )
        connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{role_name}"')
        connection.exec_driver_sql(f'GRANT SELECT ON public."{table_name}" TO "{role_name}"')
        for name, value in (("one", "0.04"), ("two", "0.08")):
            connection.execute(
                text(
                    f'INSERT INTO public."{table_name}" VALUES '
                    "(:id, :version, :time, :error, :unit, :decision)"
                ),
                {
                    "id": name,
                    "version": "1",
                    "time": datetime.now(UTC) - timedelta(minutes=1),
                    "error": value,
                    "unit": "mm",
                    "decision": "pass",
                },
            )
    try:
        settings = get_settings()
        key = "public." + table_name
        columns = ["id", "version", "updated_at", "error", "unit", "decision"]
        cache = {"tables": {key: {"columns": [{"name": c} for c in columns]}}, "truncated": False}
        with SessionLocal() as db:
            app = Application(code="db-inspection", name="DB inspection", enabled=True)
            db.add(app)
            db.flush()
            connection = ServiceDatabase(
                application_id=app.id,
                name="Readonly fixture",
                engine="postgresql",
                host=settings.postgres_host,
                port=settings.postgres_port,
                database=settings.postgres_db,
                username=role_name,
                tls_mode="disable",
                encrypted_config=cipher()
                .encrypt(json.dumps({"password": password}).encode())
                .decode(),
                environment="test",
                query_enabled=True,
                enabled=True,
                permission_status="verified",
                allowed_tables={key: columns},
                schema_cache=cache,
                schema_hash=service_databases.digest(cache),
            )
            db.add(connection)
            db.commit()
            body = {
                "application_id": str(app.id),
                "name": "DB automatic",
                "environment": "test",
                "enabled": True,
                "max_records": 1,
                "source_kind": "database",
                "source_config": {
                    "connection_id": str(connection.id),
                    "table": key,
                    "id_column": "id",
                    "version_column": "version",
                    "time_column": "updated_at",
                    "decision_column": "decision",
                    "fact_columns": ["error", "unit"],
                },
                "rules": [
                    {
                        "code": "range",
                        "title": "Fixture limit",
                        "kind": "numeric_range",
                        "path": "error",
                        "maximum": "0.05",
                        "basis": "Fixture v1",
                    }
                ],
            }
        response = client.post("/api/v1/inspection-plans", headers=admin_headers, json=body)
        assert response.status_code == 201, response.text
        plan = response.json()
        for _ in range(3):
            response = client.post(
                f"/api/v1/inspection-plans/{plan['id']}/runs", headers=admin_headers
            )
            assert response.status_code == 202
            with SessionLocal() as db:
                from app.models import InspectionPlan

                sources.read_page(
                    db,
                    db.get(InspectionPlan, UUID(plan["id"])),
                    db.get(InspectionRun, UUID(response.json()["id"])),
                    1,
                )
            drain()
        with SessionLocal() as db:
            items = db.scalars(select(InspectionItem)).all()
            assert {i.record_id: i.verdict for i in items} == {
                "one": "supported",
                "two": "contradicted",
            }
        invalid = {**body, "source_config": {**body["source_config"], "fact_columns": ["private"]}}
        assert (
            client.post("/api/v1/inspection-plans", headers=admin_headers, json=invalid).status_code
            == 422
        )
        invalid = {**body, "environment": "other"}
        assert (
            client.post("/api/v1/inspection-plans", headers=admin_headers, json=invalid).status_code
            == 409
        )
        with engine.connect() as remote:
            assert remote.scalar(text(f'SELECT count(*) FROM public."{table_name}"')) == 2

        # One API object is enriched by an explicitly authorized database lookup.
        class Cover(BaseHTTPRequestHandler):
            def do_GET(self):
                raw = json.dumps(
                    {
                        "items": [
                            {
                                "id": "one",
                                "version": "cover-1",
                                "updated_at": (
                                    datetime.now(UTC) - timedelta(minutes=1)
                                ).isoformat(),
                                "facts": {"instrument": "A"},
                                "original_verdict": "pass",
                            }
                        ],
                        "next_cursor": None,
                    }
                ).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Cover)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            mixed = {
                **body,
                "application_id": None,
                "name": "Independent API plus database",
                "source_kind": "api",
                "source_config": {"url": f"http://127.0.0.1:{server.server_port}"},
                "related_sources": [
                    {
                        "name": "details",
                        "source_kind": "database",
                        "source_config": {
                            **body["source_config"],
                            "fact_columns": ["id", "error", "unit"],
                        },
                        "match_field": "id",
                        "cardinality": "one",
                    }
                ],
                "rules": [{**body["rules"][0], "path": "details.error"}],
            }
            response = client.post("/api/v1/inspection-plans", headers=admin_headers, json=mixed)
            assert response.status_code == 201, response.text
            mixed_id = response.json()["id"]
            run = client.post(
                f"/api/v1/inspection-plans/{mixed_id}/runs", headers=admin_headers
            ).json()
            drain()
            item = client.get(
                f"/api/v1/inspection-runs/{run['id']}/items", headers=admin_headers
            ).json()[0]
            assert item["verdict"] == "supported"
            assert item["evidence"]["facts"]["details"]["id"] == "one"
            assert item["evidence"]["facts"]["details"]["error"] == "0.04"
            assert (
                len(
                    client.get(
                        "/api/v1/inspection-connections?environment=test", headers=admin_headers
                    ).json()
                )
                == 1
            )
            with engine.connect() as remote:
                assert remote.scalar(text(f'SELECT count(*) FROM public."{table_name}"')) == 2
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP TABLE public."{table_name}"')
            connection.exec_driver_sql(f'DROP OWNED BY "{role_name}"')
            connection.exec_driver_sql(f'DROP ROLE "{role_name}"')


def test_real_shared_harness_and_mcp_inspection(client, admin_headers, monkeypatch, tmp_path):
    calls = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            names = {t["function"]["name"] for t in body["tools"]}
            assert names == {"mcp__ops__" + n for n in agent.InspectionTools.definitions}
            messages = [m for m in body["messages"] if m["role"] == "tool"]
            calls.append(len(messages))
            steps = ["read_inspection_skill", "get_inspection_record", "get_inspection_checks"]
            if len(messages) < len(steps):
                delta = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_" + str(len(messages)),
                            "type": "function",
                            "function": {
                                "name": "mcp__ops__" + steps[len(messages)],
                                "arguments": "{}",
                            },
                        }
                    ],
                }
                finish = "tool_calls"
            else:
                evidence = json.loads(messages[1]["content"])
                delta = {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "verdict": "supported",
                            "summary": "隔离 Provider 完成只读巡检工具链验证",
                            "evidence_ids": [evidence["id"]],
                            "data_gaps": [],
                        },
                        ensure_ascii=False,
                    ),
                }
                finish = "stop"
            chunk = {
                "id": "chatcmpl-inspection",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": body["model"],
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            }
            last = {
                **chunk,
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            }
            raw = (
                "data: "
                + json.dumps(chunk)
                + "\n\ndata: "
                + json.dumps(last)
                + "\n\ndata: [DONE]\n\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = get_settings()
    key = tmp_path / "inspection-provider.key"
    key.write_text("isolated-provider-" + "x" * 32)
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "ai_key_file", key)
    monkeypatch.setattr(settings, "ai_base_url", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr(settings, "ai_timeout_seconds", 60)
    monkeypatch.setattr(settings, "ai_provider_kind", "mock")
    from app.services.agent_config import import_environment

    with SessionLocal() as db:
        import_environment(db)
    try:
        with SessionLocal() as db:
            user_id = UUID(client.get("/api/v1/me", headers=admin_headers).json()["id"])
            app = Application(code="harness-inspection", name="Harness inspection", enabled=True)
            skill = KnowledgeSkill(
                code="business_inspection",
                title="Inspection",
                tags=[],
                content="Read the supplied facts and cite the record ID.",
            )
            db.add_all([app, skill])
            db.commit()
            body = InspectionPlanInput(
                application_id=app.id,
                name="Harness",
                environment="test",
                enabled=True,
                source_kind="api",
                source_config={"url": "http://127.0.0.1/unused"},
                agent_enabled=True,
                skill_id=skill.id,
            )
            plan = plans.configure(db, body, db.get(User, user_id))
            run = plans.run_now(db, plan.id, db.get(User, user_id))
            row = db.get(InspectionRun, run.id)
            row.status = "evaluating"
            row.summary = {"source_complete": True}
            row.collected = 1
            item = InspectionItem(
                plan_id=plan.id,
                run_id=run.id,
                identity_hash="a" * 64,
                record_id="fixture",
                record_version="1",
                observed_at=datetime.now(UTC),
                evidence={
                    "facts": {"value": "confirmed fixture"},
                    "evidence": [{"text": "fixture"}],
                },
                checks=[],
                verdict="unassessable",
                agent_status="pending",
            )
            db.add(item)
            db.commit()
            item_id = item.id
        drain()
        with SessionLocal() as db:
            item = db.get(InspectionItem, item_id)
            assert item.agent_status == "completed", item.agent_result
            assert item.verdict == "supported"
            assert item.agent_result["evidence_ids"] == [str(item_id)]
            usage = db.scalars(select(ModelUsage).where(ModelUsage.resource_id == item_id)).all()
            assert len(usage) == 4 and all(u.application_id == plan.application_id for u in usage)
        assert calls == [0, 1, 2, 3]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
