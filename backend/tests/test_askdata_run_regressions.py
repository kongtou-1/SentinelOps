"""Ask-data storage, concurrent lifecycle writes and multi-turn regressions."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.integrations.askdata_semantic import BuildStorage
from app.models import AskDataDefinition
from app.models.askdata import ONTOLOGY_TABLES
from app.repositories import askdata_runs as repository
from app.services import askdata as settings
from app.services import askdata_query
from tests.test_askdata_stage3_runs import conversation, prepare, submit


def test_real_storage_first_access_and_transaction_complete():
    errors = []

    def access_storage():
        storage = BuildStorage(uuid4(), lambda writing: None)
        try:
            store = storage.store()
            assert storage.store() is store
            with storage.transaction():
                assert store.query(select(ONTOLOGY_TABLES["term"])) == []
        except Exception as exc:
            errors.append(exc)
        finally:
            storage.close(success=not errors)

    thread = threading.Thread(target=access_storage, daemon=True)
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive(), "BuildStorage first access did not complete"
    assert not errors


def test_concurrent_identical_submissions_return_same_run(client, admin_headers, monkeypatch):
    connection_id = prepare(client, admin_headers, monkeypatch)
    created = conversation(client, admin_headers, connection_id)
    barrier = threading.Barrier(2)
    original = repository.conversation

    def simultaneous_lookup(*args, **kwargs):
        barrier.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(repository, "conversation", simultaneous_lookup)
    key = str(uuid4())

    def post():
        return client.post(
            f"/api/v1/askdata/conversations/{created['id']}/runs",
            headers=admin_headers,
            json={"question": "订单数", "idempotency_key": key},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(post) for _ in range(2)]
        responses = [future.result(timeout=10) for future in futures]
    assert [response.status_code for response in responses] == [202, 202]
    assert responses[0].json()["id"] == responses[1].json()["id"]
    with SessionLocal() as db:
        rows = repository.runs(db, created["id"])
        assert len(rows) == 1
        assert [event.seq for event in repository.events_after(db, rows[0].id)] == [1, 2]


def test_cancellation_wins_over_stale_completion(client, admin_headers, monkeypatch):
    connection_id = prepare(client, admin_headers, monkeypatch)
    created = conversation(client, admin_headers, connection_id)
    run = submit(client, admin_headers, created["id"])
    with SessionLocal() as db:
        repository.worker_run(db, run["id"]).status = "running"
        db.commit()
    with SessionLocal() as completing:
        loaded = repository.worker_run(completing, run["id"])
        assert loaded.status == "running"
        response = client.post(f"/api/v1/askdata/runs/{run['id']}/cancel", headers=admin_headers)
        assert response.json()["status"] == "cancelled"
        repository.finish_run(completing, run["id"], "completed", {"answer": "late result"})
        completing.commit()
    with SessionLocal() as db:
        row = repository.worker_run(db, run["id"])
        assert row.status == "cancelled" and row.result == {}


def test_worker_event_and_cancel_commit_unique_sequences(client, admin_headers, monkeypatch):
    connection_id = prepare(client, admin_headers, monkeypatch)
    created = conversation(client, admin_headers, connection_id)
    run = submit(client, admin_headers, created["id"])
    cancelling = threading.Event()
    original = repository.cancel

    def cancel(db, identifier):
        cancelling.set()
        return original(db, identifier)

    monkeypatch.setattr(repository, "cancel", cancel)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with SessionLocal() as worker:
            repository.append_event(worker, run["id"], "step", {"step": "execute_sql_query"})
            future = pool.submit(
                client.post, f"/api/v1/askdata/runs/{run['id']}/cancel", headers=admin_headers
            )
            assert cancelling.wait(timeout=5)
            worker.commit()
        response = future.result(timeout=10)
    assert response.status_code == 200 and response.json()["status"] == "cancelled"
    with SessionLocal() as db:
        events = repository.events_after(db, run["id"])
        assert [event.seq for event in events] == [1, 2, 3, 4]
        assert events[-1].data["step"] == "cancelled"


def test_stale_cancel_does_not_replace_completed_result(client, admin_headers, monkeypatch):
    connection_id = prepare(client, admin_headers, monkeypatch)
    created = conversation(client, admin_headers, connection_id)
    run = submit(client, admin_headers, created["id"])
    with SessionLocal() as cancelling:
        loaded = repository.worker_run(cancelling, run["id"])
        assert loaded.status == "queued"
        with SessionLocal() as completing:
            repository.finish_run(completing, run["id"], "completed", {"answer": "done"})
            completing.commit()
        assert repository.cancel(cancelling, run["id"]) is False
        cancelling.commit()
    with SessionLocal() as db:
        row = repository.worker_run(db, run["id"])
        assert row.status == "completed" and row.result == {"answer": "done"}


@pytest.mark.parametrize("review_required", [True, False])
def test_pending_definition_is_not_certified(client, admin_headers, monkeypatch, review_required):
    connection_id = prepare(client, admin_headers, monkeypatch)
    created = conversation(client, admin_headers, connection_id)
    run = submit(client, admin_headers, created["id"])
    with SessionLocal() as db:
        db.add(
            AskDataDefinition(
                connection_id=connection_id,
                model_version_id=run["model_version_id"],
                object_kind="metric",
                object_key="amount",
                payload={"description": "口径"},
                certified=True,
                review_required=review_required,
            )
        )
        db.commit()
    with SessionLocal() as db:
        evidence, certification = askdata_query._evidence(
            db, run["model_version_id"], "SELECT SUM(amount) FROM public.orders"
        )
    assert evidence[0].certified is (not review_required)
    assert certification == ("draft" if review_required else "certified")


def test_long_conversation_uses_latest_terminal_rounds(client, admin_headers, monkeypatch):
    connection_id = prepare(client, admin_headers, monkeypatch)
    created = conversation(client, admin_headers, connection_id)
    with SessionLocal() as db:
        conv = repository.conversation(db, created["id"], created["owner_id"])
        version_id = repository.active_version(db, connection_id)
        for index in range(25):
            row = repository.create_run(db, conv, version_id, f"question-{index}", uuid4())
            row.status = "running" if index == 24 else "completed"
            row.created_at = datetime(2026, 11, 1, tzinfo=UTC) + timedelta(seconds=index)
            db.flush()
        current = repository.create_run(db, conv, version_id, "current", uuid4())
        current.status = "completed"
        db.flush()
        current_id = current.id
        db.commit()
    with SessionLocal() as db:
        history = askdata_query._history(db, created["id"], current_id)
        assert [row.question for row in history] == ["question-21", "question-22", "question-23"]


@pytest.mark.parametrize("failure_source", ["upstream", "database"])
def test_long_errors_finish_real_orchestration(client, admin_headers, monkeypatch, failure_source):
    import auto_ontology.host as host
    import auto_ontology.utils.retriever as retriever

    connection_id = prepare(client, admin_headers, monkeypatch)
    created = conversation(client, admin_headers, connection_id)
    run = submit(client, admin_headers, created["id"])
    with SessionLocal() as db:
        repository.worker_run(db, run["id"]).status = "running"
        db.commit()
    message = "private query details " * 30

    def execute(sql):
        raise ValueError(message)

    monkeypatch.setattr(
        settings,
        "database_connector",
        lambda *a, **kw: SimpleNamespace(
            execute_raw=execute,
        ),
    )
    monkeypatch.setattr(retriever, "get_data_objects_retriever", lambda: object())
    monkeypatch.setattr(retriever, "get_semantic_objects_retriever", lambda: object())

    def pipeline(payload):
        # Exercise the real task bindings and storage, replacing only external I/O.
        store = host.require().store_factory()
        assert store.query(select(ONTOLOGY_TABLES["term"])) == []
        if failure_source == "upstream":
            yield {"type": "error", "message": message}
        else:
            yield {
                "type": "result",
                "answer": {
                    "response": "回答",
                    "sql_code": "SELECT amount FROM public.orders",
                },
            }

    askdata_query.run(run["id"], stream_fn=pipeline)
    detail = client.get(f"/api/v1/askdata/runs/{run['id']}", headers=admin_headers).json()
    assert detail["status"] == "failed"
    expected = "agent_failed" if failure_source == "upstream" else "query_failed"
    assert detail["error_code"] == expected
    assert "private query details" not in json.dumps(detail)
