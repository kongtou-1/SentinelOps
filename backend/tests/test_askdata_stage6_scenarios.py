"""Stage 6: runtime scenarios the earlier stages did not exercise.

Covers the acceptance list that deterministic unit tests elsewhere do not:
structure change, configuration change, revocation during a run, worker
restart recovery, single-worker claim under concurrency, and SSE replay
stability.
"""

from threading import Barrier, Thread
from uuid import uuid4

from sqlalchemy import select, update

from app.db.session import SessionLocal
from app.models import (
    AskDataConfiguration,
    ServiceDatabase,
    User,
)
from app.repositories import askdata_runs as repository
from app.services import askdata_query
from tests.test_askdata_stage3_runs import conversation, prepare, submit


def _deactivate(user_id):
    with SessionLocal() as db:
        db.execute(update(User).where(User.id == user_id).values(is_active=False))
        db.commit()


def _owner_id():
    with SessionLocal() as db:
        return db.scalar(select(User.id).where(User.username == "admin"))


def test_structure_change_blocks_new_questions(client, admin_headers, monkeypatch):
    identifier = prepare(client, admin_headers, monkeypatch)
    created = conversation(client, admin_headers, identifier)
    assert submit(client, admin_headers, created["id"])["status"] == "queued"

    with SessionLocal() as db:
        db.execute(
            update(ServiceDatabase)
            .where(ServiceDatabase.id == identifier)
            .values(schema_hash="c" * 64)
        )
        db.commit()

    response = client.post(
        f"/api/v1/askdata/conversations/{created['id']}/runs",
        headers=admin_headers,
        json={"question": "订单总数是多少？", "idempotency_key": str(uuid4())},
    )
    assert response.status_code == 409, response.text
    assert "结构已变化" in response.json()["detail"]


def test_disabled_configuration_blocks_new_questions(client, admin_headers, monkeypatch):
    identifier = prepare(client, admin_headers, monkeypatch)
    created = conversation(client, admin_headers, identifier)

    with SessionLocal() as db:
        configuration = db.scalar(
            select(AskDataConfiguration).where(
                AskDataConfiguration.connection_id == identifier
            )
        )
        configuration.enabled = False
        db.commit()

    response = client.post(
        f"/api/v1/askdata/conversations/{created['id']}/runs",
        headers=admin_headers,
        json={"question": "订单总数是多少？", "idempotency_key": str(uuid4())},
    )
    assert response.status_code == 409, response.text
    assert "已停用" in response.json()["detail"]


def test_running_query_rechecks_authorization_after_revocation(
    client, admin_headers, monkeypatch
):
    identifier = prepare(client, admin_headers, monkeypatch)
    created = conversation(client, admin_headers, identifier)
    run = submit(client, admin_headers, created["id"])
    owner = _owner_id()

    with SessionLocal() as db:
        repository.worker_run(db, run["id"]).status = "running"
        db.commit()

    _deactivate(owner)

    with SessionLocal() as db:
        assert repository.worker_run(db, run["id"]).status == "running"

    # Any further storage write during the run must fail closed.
    with SessionLocal() as db:
        try:
            askdata_query._guard(owner, identifier, run["model_version_id"], False)
        except askdata_query.RunFailure as failure:
            assert failure.code == "authorization_revoked"
        else:  # pragma: no cover - the guard must never pass after revocation
            raise AssertionError("撤权后仍通过授权校验")

    # The worker refuses to even load the run once the owner is inactive.
    with SessionLocal() as db:
        try:
            loaded = askdata_query._load(run["id"])
        except askdata_query.RunFailure as failure:
            assert failure.code == "run_context_missing"
            loaded = None
    assert loaded is None

    askdata_query.run(run["id"])
    detail = client.get(f"/api/v1/askdata/runs/{run['id']}", headers=admin_headers)
    assert detail.status_code in (401, 403)

    with SessionLocal() as db:
        assert repository.worker_run(db, run["id"]).status == "failed"


def test_worker_restart_interrupts_running_queries_without_replaying(
    client, admin_headers, monkeypatch
):
    identifier = prepare(client, admin_headers, monkeypatch)
    created = conversation(client, admin_headers, identifier)
    running = submit(client, admin_headers, created["id"])
    finished = submit(client, admin_headers, created["id"])

    with SessionLocal() as db:
        repository.worker_run(db, running["id"]).status = "running"
        repository.finish_run(db, finished["id"], "completed", {"answer": "既有结果"})
        db.commit()

    with SessionLocal() as db:
        recovered = repository.recover_interrupted_runs(db)
        db.commit()
        assert [str(row.id) for row in recovered] == [running["id"]]
        row = repository.worker_run(db, running["id"])
        assert row.status == "interrupted"
        assert row.error_code == "run_interrupted"
        assert row.result["interrupted_at"]
        # A completed run is never touched by recovery.
        assert repository.worker_run(db, finished["id"]).status == "completed"
        # The interrupted run can no longer be claimed, so it is not re-executed.
        assert repository.claim_run(db) is None
        db.commit()

    askdata_query.run(running["id"])
    with SessionLocal() as db:
        assert repository.worker_run(db, running["id"]).status == "interrupted"


def test_only_one_worker_claims_a_queued_run(client, admin_headers, monkeypatch):
    identifier = prepare(client, admin_headers, monkeypatch)
    created = conversation(client, admin_headers, identifier)
    run = submit(client, admin_headers, created["id"])

    barrier = Barrier(2)
    claimed = []

    def claim():
        with SessionLocal() as db:
            barrier.wait(timeout=10)
            row = repository.claim_run(db)
            claimed.append(str(row.id) if row else None)
            db.commit()

    workers = [Thread(target=claim) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=20)

    assert claimed.count(run["id"]) == 1
    assert claimed.count(None) == 1


def test_configuration_change_does_not_rewrite_recorded_context(
    client, admin_headers, monkeypatch
):
    identifier = prepare(client, admin_headers, monkeypatch)
    created = conversation(client, admin_headers, identifier)
    run = submit(client, admin_headers, created["id"])

    before = client.get(f"/api/v1/askdata/runs/{run['id']}", headers=admin_headers).json()
    version_id = before["context"]["model_version_id"]

    with SessionLocal() as db:
        configuration = db.scalar(
            select(AskDataConfiguration).where(
                AskDataConfiguration.connection_id == identifier
            )
        )
        configuration.budget_seconds = 60
        configuration.version = (configuration.version or 0) + 1
        db.commit()

    after = client.get(f"/api/v1/askdata/runs/{run['id']}", headers=admin_headers).json()
    assert after["context"]["model_version_id"] == version_id
    assert after["context"] == before["context"]


def test_readiness_follows_the_current_migration_head(client, monkeypatch):
    """Regression: readiness used to pin one revision and 503 after new migrations."""
    from app.repositories import system as system_repository
    from app.services import system

    with SessionLocal() as db:
        revision = system_repository.ready_query(db)
    assert system.head_revision() == revision
    assert client.get("/health/ready").status_code == 200

    monkeypatch.setattr(
        system_repository, "ready_query", lambda db: "0047_inspection_agent_timestamps"
    )
    assert client.get("/health/ready").status_code == 503


def test_reconnect_replays_the_same_events_without_executing_again(
    client, admin_headers, monkeypatch
):
    identifier = prepare(client, admin_headers, monkeypatch)
    created = conversation(client, admin_headers, identifier)
    run = submit(client, admin_headers, created["id"])
    path = f"/api/v1/askdata/runs/{run['id']}"
    with SessionLocal() as db:
        repository.finish_run(
            db, run["id"], "completed", {"answer": "共 120 单", "sql": "SELECT 1"}
        )
        db.commit()

    first = client.get(f"{path}/events?after_seq=0", headers=admin_headers).json()
    second = client.get(f"{path}/events?after_seq=0", headers=admin_headers).json()
    assert first["items"] == second["items"]
    sequences = [item["seq"] for item in first["items"]]
    assert sequences == sorted(sequences)
    assert len(set(sequences)) == len(sequences)

    resumed = client.get(
        f"{path}/events?after_seq={sequences[-1]}", headers=admin_headers
    ).json()
    assert resumed["items"] == []

    with SessionLocal() as db:
        events = repository.events_after(db, run["id"], 0, 100)
        steps = [event for event in events if event.kind != "context"]
        # The execution context is persisted once and never re-emitted as a step.
        assert [event.kind for event in events].count("context") == 1
        assert len(steps) == len(sequences)
        assert repository.worker_run(db, run["id"]).status == "completed"
