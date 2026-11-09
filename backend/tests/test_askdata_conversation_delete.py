"""Conversation deletion hides private history and cancels outstanding work."""

import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest

from app.db.session import SessionLocal
from app.models import AskDataConversation, ServiceDatabase
from app.repositories import askdata_runs as repository
from app.services import askdata_runs as service
from tests.test_askdata_stage3_runs import conversation, prepare, submit


@pytest.mark.parametrize("status", ["queued", "running", "completed"])
def test_delete_hides_history_and_cancels_only_unfinished_work(
    client, admin_headers, monkeypatch, status
):
    connection_id = prepare(client, admin_headers, monkeypatch)
    chat = conversation(client, admin_headers, connection_id)
    run = submit(client, admin_headers, chat["id"])
    other = conversation(client, admin_headers, connection_id)
    other_run = submit(client, admin_headers, other["id"])
    with SessionLocal() as db:
        row = repository.worker_run(db, run["id"])
        row.status = status
        row.result = {"answer": "保留历史记录"} if status == "completed" else {}
        # Users can also clean up history belonging to a disabled connection.
        db.get(ServiceDatabase, connection_id).enabled = False
        db.commit()

    path = f"/api/v1/askdata/conversations/{chat['id']}"
    response = client.delete(path, headers=admin_headers)
    assert response.status_code == 204 and not response.content
    assert client.get(path, headers=admin_headers).status_code == 404
    assert client.delete(path, headers=admin_headers).status_code == 404
    assert {row["id"] for row in client.get(
        "/api/v1/askdata/conversations", headers=admin_headers
    ).json()} == {other["id"]}
    for suffix in ("", "/events", "/stream", "/export"):
        assert client.get(
            f"/api/v1/askdata/runs/{run['id']}{suffix}", headers=admin_headers
        ).status_code == 404
    assert client.post(
        f"{path}/runs", headers=admin_headers,
        json={"question": "再问一次", "idempotency_key": str(uuid4())},
    ).status_code == 404
    with SessionLocal() as db:
        deleted = db.get(AskDataConversation, UUID(chat["id"]))
        assert deleted.deleted_at is not None
        # PostgreSQL's touch_updated_at trigger uses the transaction timestamp.
        assert deleted.updated_at > deleted.created_at
        row = repository.worker_run(db, run["id"])
        if status == "completed":
            assert row.status == "completed" and row.result["answer"] == "保留历史记录"
        else:
            assert repository.cancelled(db, run["id"])
            assert repository.finish_run(db, run["id"], "completed").status == "cancelled"
            assert repository.events_after(db, run["id"])[-1].data["step"] == "cancelled"
        assert repository.worker_run(db, other_run["id"]).status == "queued"


def test_delete_requires_owner_and_run_permission(
    client, admin_headers, monkeypatch, make_user, login
):
    identifier = prepare(client, admin_headers, monkeypatch)
    chat = conversation(client, admin_headers, identifier)
    path = f"/api/v1/askdata/conversations/{chat['id']}"
    assert client.delete(path).status_code == 401
    make_user("other-admin", "admin")
    other = {"Authorization": "Bearer " + login("other-admin")["access_token"]}
    assert client.delete(path, headers=other).status_code == 404
    make_user("viewer", "viewer")
    viewer = {"Authorization": "Bearer " + login("viewer")["access_token"]}
    assert client.delete(path, headers=viewer).status_code == 403
    assert client.get(path, headers=admin_headers).status_code == 200


def test_deletion_serializes_with_inflight_submission(client, admin_headers, monkeypatch):
    identifier = prepare(client, admin_headers, monkeypatch)
    chat = conversation(client, admin_headers, identifier)
    entered, release, deleting = threading.Event(), threading.Event(), threading.Event()
    original = repository.conversation

    def capabilities():
        entered.set()
        assert release.wait(timeout=10)

    def lookup(*args, **kwargs):
        if entered.is_set():
            deleting.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "_check_capabilities", capabilities)
    monkeypatch.setattr(repository, "conversation", lookup)
    with ThreadPoolExecutor(max_workers=2) as pool:
        submitted = pool.submit(submit, client, admin_headers, chat["id"])
        try:
            assert entered.wait(timeout=5)
            removed = pool.submit(
                client.delete, f"/api/v1/askdata/conversations/{chat['id']}", headers=admin_headers
            )
            assert deleting.wait(timeout=5)
        finally:
            release.set()
        run = submitted.result(timeout=10)
        assert removed.result(timeout=10).status_code == 204
    with SessionLocal() as db:
        assert repository.worker_run(db, run["id"]).status == "cancelled"
