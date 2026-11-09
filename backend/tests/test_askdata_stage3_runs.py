"""Stage 3: private conversations, idempotent runs, durable events, export."""

import json
import time
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import func, select, update

from app.db.session import SessionLocal
from app.models import (
    AskDataConfiguration,
    AskDataModelVersion,
    AskDataRun,
    ServiceDatabase,
    User,
)
from app.repositories import askdata_runs as repository
from app.schemas.askdata_runs import AskDataResult
from app.services import askdata_query, askdata_runs as service
from tests.test_askdata import create_connection


def prepare(client, headers, monkeypatch):
    """One enabled connection with a published business model version."""
    monkeypatch.setattr(service, "_check_capabilities", lambda: None)
    identifier = create_connection(client, headers)
    with SessionLocal() as db:
        db.execute(
            update(ServiceDatabase)
            .where(ServiceDatabase.id == identifier)
            .values(
                query_enabled=True,
                permission_status="verified",
                schema_hash="b" * 64,
                schema_cache={"tables": {}},
                allowed_tables={},
            )
        )
        db.commit()
        connection = db.get(ServiceDatabase, identifier)
        owner = db.scalar(select(User).where(User.username == "admin"))
        version = AskDataModelVersion(
            connection_id=identifier,
            created_by=owner.id,
            config_version=connection.config_version,
            schema_hash=connection.schema_hash,
            status="ready",
            context={},
        )
        db.add(version)
        db.flush()
        db.add(
            AskDataConfiguration(
                connection_id=identifier,
                enabled=True,
                version=1,
                active_model_version_id=version.id,
            )
        )
        db.commit()
    return identifier


def conversation(client, headers, identifier):
    response = client.post(
        "/api/v1/askdata/conversations",
        headers=headers,
        json={"connection_id": str(identifier), "title": "订单分析"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def submit(client, headers, conversation_id, key=None, question="上个月订单总额是多少？"):
    response = client.post(
        f"/api/v1/askdata/conversations/{conversation_id}/runs",
        headers=headers,
        json={"question": question, "idempotency_key": key or str(uuid4())},
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_run_submission_is_idempotent_and_history_is_private(
    client, admin_headers, make_user, login, monkeypatch
):
    identifier = prepare(client, admin_headers, monkeypatch)
    created = conversation(client, admin_headers, identifier)
    key = str(uuid4())
    first = submit(client, admin_headers, created["id"], key)
    second = submit(client, admin_headers, created["id"], key)
    assert second["id"] == first["id"]
    with SessionLocal() as db:
        count = db.scalar(
            select(func.count())
            .select_from(AskDataRun)
            .where(AskDataRun.conversation_id == created["id"])
        )
        assert count == 1

    detail = client.get(
        f"/api/v1/askdata/runs/{first['id']}", headers=admin_headers
    ).json()
    assert detail["status"] == "queued"
    assert detail["context"]["connection_id"] == str(identifier)
    assert detail["context"]["model_version_id"]
    assert any(event["step"] == "queued" for event in detail["events"])

    make_user("other", "admin")
    other = {"Authorization": "Bearer " + login("other")["access_token"]}
    assert client.get("/api/v1/askdata/conversations", headers=other).json() == []
    assert (
        client.get(f"/api/v1/askdata/runs/{first['id']}", headers=other).status_code == 404
    )


def test_viewer_can_read_but_cannot_submit(client, admin_headers, make_user, login, monkeypatch):
    identifier = prepare(client, admin_headers, monkeypatch)
    make_user("reader", "viewer")
    reader = {"Authorization": "Bearer " + login("reader")["access_token"]}
    assert client.get("/api/v1/askdata/conversations", headers=reader).status_code == 200
    assert (
        client.post(
            "/api/v1/askdata/conversations",
            headers=reader,
            json={"connection_id": str(identifier)},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/askdata/conversations",
            headers=admin_headers,
            json={"connection_id": str(identifier)},
        ).status_code
        == 201
    )


def test_cancel_sse_replay_and_export_guard(client, admin_headers, monkeypatch):
    identifier = prepare(client, admin_headers, monkeypatch)
    created = conversation(client, admin_headers, identifier)
    run = submit(client, admin_headers, created["id"])
    path = f"/api/v1/askdata/runs/{run['id']}"

    cancelled = client.post(f"{path}/cancel", headers=admin_headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    page = client.get(f"{path}/events?after_seq=0", headers=admin_headers).json()
    assert page["terminal"] is True
    assert any(item["step"] == "cancelled" for item in page["items"])

    # Reconnection only replays durable events; it never re-executes the query.
    stream = client.get(f"{path}/stream?after_seq=0", headers=admin_headers)
    assert stream.status_code == 200
    assert "event: event" in stream.text and "event: done" in stream.text

    assert client.get(f"{path}/export", headers=admin_headers).status_code == 409
    with SessionLocal() as db:
        assert repository.finish_run(db, run["id"], "completed", {"answer": "late"}).status == "cancelled"
        db.commit()


def test_step_events_never_expose_internal_reasoning(client, admin_headers, monkeypatch):
    identifier = prepare(client, admin_headers, monkeypatch)
    created = conversation(client, admin_headers, identifier)
    run = submit(client, admin_headers, created["id"])
    events = [
        {"type": "step", "phase": "start", "node": "retrieve_candidates", "thought": "SECRET"},
        {"type": "step", "phase": "end", "node": "retrieve_candidates", "thought": "SECRET"},
        {"type": "sql", "sql": "SELECT id FROM public.orders"},
        {"type": "result", "answer": {"response": "共 3 单", "sql_code": ""}},
    ]
    answer = askdata_query._events(
        run["id"], lambda payload: iter(events), {}, time.monotonic() + 30
    )
    assert answer["sql_code"] == "SELECT id FROM public.orders"
    with SessionLocal() as db:
        rows = repository.events_after(db, run["id"], 0, 100)
        assert rows
        assert all("SECRET" not in json.dumps(row.data) for row in rows)
        assert [row.seq for row in rows] == sorted(row.seq for row in rows)
        assert any("检索候选对象" in str(row.data) for row in rows)


def test_result_shares_one_execution_for_answer_table_and_chart(
    client, admin_headers, monkeypatch
):
    identifier = prepare(client, admin_headers, monkeypatch)
    created = conversation(client, admin_headers, identifier)
    run = submit(client, admin_headers, created["id"])
    raw = {
        "columns": ["status", "total"],
        "column_types": [
            {"name": "status", "kind": "text", "type_code": 25},
            {"name": "total", "kind": "decimal", "type_code": 1700},
        ],
        "rows": [["paid", "12.50"], ["open", "3.25"]],
        "truncated": False,
        "observed_at": "2026-11-06T10:00:00+00:00",
        "executed_sql": "SELECT status, total FROM public.orders LIMIT 200",
    }
    connector = SimpleNamespace(execute_raw=lambda sql: raw)
    state = {"version_id": run["model_version_id"]}
    answer = {"response": "共 2 类状态", "sql_code": "SELECT status, total FROM public.orders"}
    result, status = askdata_query._result(run["id"], state, answer, connector)
    assert status == "completed"
    assert result.columns[1].kind == "decimal"
    assert result.row_count == 2 and result.rows[0][1] == "12.50"
    assert result.chart.kind == "bar" and result.chart.y == ["total"]
    assert result.executed_sql == raw["executed_sql"]
    assert result.certification in ("none", "draft", "certified")

    clarifying, status = askdata_query._result(
        run["id"], state, {"response": "请问您指的是哪个时间范围？", "sql_code": ""}, connector
    )
    assert status == "needs_clarification" and clarifying.clarification


def test_export_contains_only_returned_authorized_rows(client, admin_headers, monkeypatch):
    identifier = prepare(client, admin_headers, monkeypatch)
    created = conversation(client, admin_headers, identifier)
    run = submit(client, admin_headers, created["id"])
    result = AskDataResult(
        answer="共 2 行",
        sql="SELECT status, total FROM public.orders",
        columns=[
            {"name": "status", "kind": "text"},
            {"name": "total", "kind": "decimal"},
        ],
        rows=[["paid", "12.50"], ["open", "3.25"]],
        row_count=2,
    )
    with SessionLocal() as db:
        row = repository.worker_run(db, run["id"])
        row.status, row.result, row.error_code = "completed", result.model_dump(), None
        db.commit()
    response = client.get(
        f"/api/v1/askdata/runs/{run['id']}/export", headers=admin_headers
    )
    assert response.status_code == 200
    assert "status,total" in response.text
    assert "12.50" in response.text and "secret" not in response.text


def test_orchestration_completes_a_claimed_run_with_injected_pipeline(
    client, admin_headers, monkeypatch
):
    """Worker path end to end: claim -> upstream events -> one result -> completed."""
    from contextlib import contextmanager

    import app.integrations.askdata_semantic as semantic
    import app.integrations.ontology as ontology
    import app.services.askdata as settings
    import auto_ontology.host as host
    import auto_ontology.utils.retriever as retriever

    identifier = prepare(client, admin_headers, monkeypatch)
    created = conversation(client, admin_headers, identifier)
    run = submit(client, admin_headers, created["id"])
    with SessionLocal() as db:
        repository.worker_run(db, run["id"]).status = "running"
        db.commit()

    executed = []

    class FakeConnector:
        database_name = str(identifier)
        dialect = "postgres"

        def execute_raw(self, sql):
            executed.append(sql)
            return {
                "columns": ["status", "total"],
                "column_types": [
                    {"name": "status", "kind": "text", "type_code": 25},
                    {"name": "total", "kind": "decimal", "type_code": 1700},
                ],
                "rows": [["paid", "12.50"]],
                "truncated": False,
                "observed_at": "2026-11-06T10:00:00+00:00",
                "executed_sql": sql + " LIMIT 200",
            }

    class FakeStorage:
        def __init__(self, *args, **kwargs):
            pass

        def store(self):
            return object()

        @contextmanager
        def transaction(self):
            yield

        def close(self, *, success):
            pass

    @contextmanager
    def fake_task(bindings):
        yield bindings

    monkeypatch.setattr(ontology, "task", fake_task)
    monkeypatch.setattr(settings, "database_connector", lambda *a, **k: FakeConnector())
    monkeypatch.setattr(semantic, "BuildStorage", FakeStorage)
    monkeypatch.setattr(host, "HostBindings", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(retriever, "get_data_objects_retriever", lambda: object())
    monkeypatch.setattr(retriever, "get_semantic_objects_retriever", lambda: object())

    def pipeline(payload):
        assert payload["question"] == "上个月订单总额是多少？"
        assert "简体中文" in payload["custom_prompts"]
        yield {"type": "step", "phase": "start", "node": "retrieve_candidates", "thought": "SECRET"}
        yield {"type": "step", "phase": "end", "node": "construct_sql_from_candidates", "thought": "SECRET"}
        yield {"type": "sql", "sql": "SELECT status, SUM(amount) AS total FROM public.orders GROUP BY status"}
        yield {"type": "result", "answer": {"response": "已支付订单合计 12.50", "sql_code": ""}}

    askdata_query.run(run["id"], stream_fn=pipeline)
    detail = client.get(f"/api/v1/askdata/runs/{run['id']}", headers=admin_headers).json()
    assert detail["status"] == "completed", detail
    assert detail["result"]["answer"] == "已支付订单合计 12.50"
    assert detail["result"]["row_count"] == 1
    assert detail["result"]["columns"][1]["kind"] == "decimal"
    assert executed == ["SELECT status, SUM(amount) AS total FROM public.orders GROUP BY status"]
    assert any(event["summary"] == "生成 SQL" for event in detail["events"])
    assert all("SECRET" not in json.dumps(event) for event in detail["events"])
    assert detail["context"]["model_version_id"]
