"""Stage 7: the ask-data kill switch used during deployment and rollback."""

from uuid import uuid4

import pytest

from app.services import askdata as settings
from app.workers import askdata as worker
from tests.test_askdata_stage3_runs import conversation, prepare, submit


class FakeStop:
    """Stops the loop after the first iteration and records the wait."""

    def __init__(self):
        self.calls = 0
        self.waits = []

    def is_set(self):
        self.calls += 1
        return self.calls > 1

    def wait(self, seconds):
        self.waits.append(seconds)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        (" on ", True),
        ("", False),
        ("0", False),
        ("false", False),
        ("paused", False),
    ],
)
def test_switch_parses_environment(value, expected, monkeypatch):
    monkeypatch.setenv("ASKDATA_PAUSED", value)
    assert settings.paused() is expected


def test_paused_connection_blocks_new_work_but_keeps_reads(
    client, admin_headers, monkeypatch
):
    identifier = prepare(client, admin_headers, monkeypatch)
    created = conversation(client, admin_headers, identifier)
    existing = submit(client, admin_headers, created["id"])

    saved = client.post(
        "/api/v1/askdata/analyses",
        headers=admin_headers,
        json={
            "connection_id": str(identifier),
            "name": "暂停前保存的分析",
            "question": "订单总数是多少？",
            "sql_text": "SELECT COUNT(*) AS total FROM orders",
        },
    )
    assert saved.status_code == 201, saved.text

    monkeypatch.setenv("ASKDATA_PAUSED", "true")

    question = client.post(
        f"/api/v1/askdata/conversations/{created['id']}/runs",
        headers=admin_headers,
        json={"question": "订单总数是多少？", "idempotency_key": str(uuid4())},
    )
    assert question.status_code == 409
    assert "已暂停" in question.json()["detail"]

    build = client.post(
        f"/api/v1/askdata/connections/{identifier}/versions",
        headers=admin_headers,
        json={},
    )
    assert build.status_code == 409
    assert "已暂停" in build.json()["detail"]

    replay = client.post(
        f"/api/v1/askdata/analyses/{saved.json()['id']}/execute",
        headers=admin_headers,
    )
    assert replay.status_code == 409
    assert "已暂停" in replay.json()["detail"]

    # Reads, history and configuration stay available while paused.
    assert (
        client.get("/api/v1/askdata/conversations", headers=admin_headers).status_code
        == 200
    )
    assert (
        client.get(
            f"/api/v1/askdata/runs/{existing['id']}", headers=admin_headers
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/api/v1/askdata/settings/connections/{identifier}",
            headers=admin_headers,
        ).status_code
        == 200
    )


def test_unpausing_restores_submissions(client, admin_headers, monkeypatch):
    identifier = prepare(client, admin_headers, monkeypatch)
    created = conversation(client, admin_headers, identifier)
    monkeypatch.setenv("ASKDATA_PAUSED", "true")
    blocked = client.post(
        f"/api/v1/askdata/conversations/{created['id']}/runs",
        headers=admin_headers,
        json={"question": "订单总数是多少？", "idempotency_key": str(uuid4())},
    )
    assert blocked.status_code == 409

    monkeypatch.delenv("ASKDATA_PAUSED")
    resumed = client.post(
        f"/api/v1/askdata/conversations/{created['id']}/runs",
        headers=admin_headers,
        json={"question": "订单总数是多少？", "idempotency_key": str(uuid4())},
    )
    assert resumed.status_code == 202, resumed.text


def test_worker_loops_claim_nothing_while_paused(monkeypatch):
    monkeypatch.setenv("ASKDATA_PAUSED", "true")

    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("暂停期间不应领取任务")

    monkeypatch.setattr(worker, "run_once", explode)
    stop = FakeStop()
    worker.build_loop(stop)
    assert stop.waits == [5]

    query_stop = FakeStop()
    worker.query_loop(query_stop, pool=None)
    assert query_stop.waits == [1]
