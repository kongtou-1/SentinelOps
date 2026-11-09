from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from test_resources_alerts import SERVICE_HEADERS
from test_resources_alerts import isolated_secrets as isolated_secrets
from test_resources_alerts import server_body as server_body
from test_resources_alerts import server_id as server_id

from app.db.session import SessionLocal
from app.services.incidents.logic import lock_incidents, recompute
from app.models import (
    AlertEvent,
    Container,
    Incident,
    IncidentAlert,
    IncidentTimeline,
    LLMEndpoint,
    PredictionTask,
    Server,
    User,
)


def alert(fingerprint="a1", start=None, status="firing", end=None, **labels):
    start = start or datetime.now(UTC) - timedelta(minutes=1)
    return {
        "fingerprint": fingerprint,
        "status": status,
        "startsAt": start.isoformat(),
        "endsAt": (end or datetime.now(UTC)).isoformat()
        if status == "resolved"
        else "0001-01-01T00:00:00Z",
        "labels": {"alertname": "TestDown", "severity": "warning", **labels},
        "annotations": {"summary": "Test outage"},
    }


def post(client, *items):
    response = client.post(
        "/api/v1/hooks/alertmanager",
        headers=SERVICE_HEADERS,
        json={"version": "4", "alerts": list(items)},
    )
    assert response.status_code == 200, response.text
    return response.json()


def get_incidents(client, headers, **params):
    return client.get("/api/v1/incidents", headers=headers, params=params).json()


def history(client, headers, incident_id):
    return client.get(f"/api/v1/incidents/{incident_id}/timeline?limit=100", headers=headers).json()


def verified_state_fixture():
    """State-machine fixture; actual evidence checks are exercised in test_recovery.

    These aggregation tests include deliberately unbound/invalid assets and do
    not pretend that their synthetic webhook can establish resource health.
    """
    with SessionLocal() as db:
        lock_incidents(db)
        for event in db.scalars(select(AlertEvent).where(AlertEvent.status == "resolved")):
            event.recovery_status = "verified"
            event.recovery_evidence_at = event.ends_at + timedelta(seconds=1)
        for incident in db.scalars(
            select(Incident).where(Incident.status.in_(("open", "in_progress")))
        ):
            recompute(db, incident)
        db.commit()


@pytest.fixture
def incident_id(client, admin_headers, server_id):
    post(client, alert(server_id=server_id))
    return get_incidents(client, admin_headers)[0]["id"]


def endpoint(db, name="endpoint"):
    item = LLMEndpoint(name=name, base_url="http://local", model="test", encrypted_api_key="dummy")
    db.add(item)
    db.flush()
    return item


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
def test_incident_rbac(client, make_user, login, role):
    make_user(role, role)
    auth = {"Authorization": f"Bearer {login(role)['access_token']}"}
    for path in ("", "/assignees"):
        assert client.get("/api/v1/incidents" + path, headers=auth).status_code == 200
    for path in (f"/{uuid4()}", f"/{uuid4()}/alerts", f"/{uuid4()}/timeline"):
        assert client.get("/api/v1/incidents" + path, headers=auth).status_code == 404
    for suffix, body in (
        ("/status", {"status": "in_progress", "comment": "investigate"}),
        ("/comments", {"comment": "note"}),
        ("/merge", {"target_incident_id": str(uuid4()), "comment": "same failure"}),
    ):
        assert client.post(
            f"/api/v1/incidents/{uuid4()}{suffix}", json=body, headers=auth
        ).status_code == (403 if role == "viewer" else 404)
    assert client.patch(
        f"/api/v1/incidents/{uuid4()}", json={"title": "new"}, headers=auth
    ).status_code == (403 if role == "viewer" else 404)
    permissions = client.get("/api/v1/me", headers=auth).json()["permissions"]
    assert "incidents:read" in permissions
    assert ("incidents:handle" in permissions) == (role != "viewer")


@pytest.mark.parametrize("path", ["", "/assignees", "/" + str(uuid4())])
def test_incident_anonymous_denied(client, path):
    assert client.get("/api/v1/incidents" + path).status_code == 401


def test_multiple_alerts_partial_recovery_recurrence(client, admin_headers, server_id):
    start = datetime.now(UTC) - timedelta(minutes=3)
    first = alert("a1", start, server_id=server_id, alertname="CPU")
    second = alert(
        "a2",
        start + timedelta(seconds=60),
        server_id=server_id,
        alertname="ContainerDown",
        container_id=str(uuid4()),
        severity="critical",
    )
    assert post(client, first)["changed"] == 1
    post(client, second)
    rows = get_incidents(client, admin_headers)
    assert len(rows) == 1 and rows[0]["severity"] == "critical"
    incident_id = rows[0]["id"]
    base = f"/api/v1/incidents/{incident_id}"
    detail = client.get(base, headers=admin_headers).json()
    assert detail["alert_count"] == detail["firing_alert_count"] == 2
    assert detail["server_ids"] == [server_id]
    before = history(client, admin_headers, incident_id)
    assert post(client, first, second)["changed"] == 0
    assert history(client, admin_headers, incident_id) == before
    events = client.get(base + "/alerts", headers=admin_headers).json()
    assert {item["incident_id"] for item in events} == {incident_id}
    assert (
        len(
            client.get(
                "/api/v1/alerts", headers=admin_headers, params={"incident_id": incident_id}
            ).json()
        )
        == 2
    )
    first_end = start + timedelta(minutes=2)
    post(client, alert("a1", start, "resolved", first_end, server_id=server_id))
    assert client.get(base, headers=admin_headers).json()["status"] == "open"
    assert (
        client.post(
            base + "/status", headers=admin_headers, json={"status": "closed", "comment": "done"}
        ).status_code
        == 409
    )
    second_end = datetime.now(UTC)
    post(
        client,
        alert("a2", start + timedelta(seconds=60), "resolved", second_end, server_id=server_id),
    )
    assert client.get(base, headers=admin_headers).json()["status"] == "open"
    verified_state_fixture()
    recovered = client.get(base, headers=admin_headers).json()
    assert recovered["status"] == "recovered" and recovered["severity"] == "critical"
    assert datetime.fromisoformat(recovered["recovered_at"]) == second_end + timedelta(seconds=1)
    terminal = history(client, admin_headers, incident_id)
    post(client, first, alert("a2", start + timedelta(seconds=60), "resolved", second_end))
    assert history(client, admin_headers, incident_id) == terminal
    post(client, alert("a1", datetime.now(UTC), server_id=server_id))
    assert len(get_incidents(client, admin_headers)) == 2


@pytest.mark.parametrize("seconds,expected", [(300, 1), (301, 2), (-300, 1), (-301, 2)])
def test_window_boundary(client, admin_headers, server_id, seconds, expected):
    start = datetime.now(UTC) - timedelta(minutes=10)
    post(client, alert("a1", start, server_id=server_id))
    post(client, alert("a2", start + timedelta(seconds=seconds), server_id=server_id))
    assert len(get_incidents(client, admin_headers)) == expected


def test_select_nearest_then_oldest_candidate(client, admin_headers, server_id):
    start = datetime.now(UTC) - timedelta(minutes=10)
    post(client, alert("a1", start, server_id=server_id))
    oldest = get_incidents(client, admin_headers)[0]["id"]
    post(client, alert("a2", start + timedelta(seconds=600), server_id=server_id))
    newer = get_incidents(client, admin_headers)[0]["id"]
    post(client, alert("a3", start + timedelta(seconds=300), server_id=server_id))
    events = client.get("/api/v1/alerts", headers=admin_headers).json()
    assert next(item for item in events if item["fingerprint"] == "a3")["incident_id"] == oldest
    post(client, alert("a4", start + timedelta(seconds=590), server_id=server_id))
    events = client.get("/api/v1/alerts", headers=admin_headers).json()
    assert next(item for item in events if item["fingerprint"] == "a4")["incident_id"] == newer


def test_asset_isolation_unknown_and_resolved_first(client, admin_headers, server_id):
    with SessionLocal() as db:
        one = endpoint(db)
        two = endpoint(db, "second")
        ids = str(one.id), str(two.id)
        db.commit()
    start = datetime.now(UTC) - timedelta(minutes=2)
    post(
        client,
        alert("a1", start, server_id=server_id),
        alert("a2", start, endpoint_id=ids[0]),
        alert("a3", start, endpoint_id=ids[0]),
        alert("a4", start, endpoint_id=ids[1]),
        alert("a5", start),
        alert("a6", start),
        alert("a7", start, server_id=str(uuid4())),
        alert("a8", start, server_id=server_id, endpoint_id=ids[0]),
    )
    assert len(get_incidents(client, admin_headers)) == 7
    assert len(get_incidents(client, admin_headers, llm_endpoint_id=ids[0])) == 1
    post(client, alert("a9", start, "resolved"))
    post(client, alert("a9", start))
    orphan = next(
        item
        for item in client.get("/api/v1/alerts", headers=admin_headers).json()
        if item["fingerprint"] == "a9"
    )
    assert orphan["incident_id"] is None and orphan["status"] == "resolved"


def test_prediction_targets_resolve_from_tasks(client, admin_headers, server_id):
    with SessionLocal() as db:
        container = Container(
            server_id=UUID(server_id),
            docker_id="/test",
            name="worker",
            image="test",
            status="running",
            labels={},
            last_seen_at=datetime.now(UTC),
        )
        db.add(container)
        llm = endpoint(db)
        db.flush()
        tasks = [
            PredictionTask(
                name="host", server_id=UUID(server_id), metric="host_cpu_percent", threshold=80
            ),
            PredictionTask(
                name="container",
                container_id=container.id,
                metric="container_memory_bytes",
                threshold=80,
            ),
            PredictionTask(
                name="llm", llm_endpoint_id=llm.id, metric="llm_latency_avg_seconds", threshold=5
            ),
        ]
        db.add_all(tasks)
        db.flush()
        ids = [str(item.id) for item in tasks]
        endpoint_id = str(llm.id)
        db.commit()
    start = datetime.now(UTC) - timedelta(minutes=1)
    post(
        client,
        alert("a1", start, server_id=server_id),
        *[alert(f"b{i}", start, task_id=task_id) for i, task_id in enumerate(ids)],
        alert("a2", start, endpoint_id=endpoint_id),
    )
    assert len(get_incidents(client, admin_headers)) == 2
    host = get_incidents(client, admin_headers, server_id=server_id)[0]
    assert (
        client.get(f"/api/v1/incidents/{host['id']}", headers=admin_headers).json()["alert_count"]
        == 3
    )


def test_assignee_changes_and_handling_history(client, admin_headers, incident_id, make_user):
    operator = str(make_user("ops", "operator"))
    viewer = str(make_user("read", "viewer"))
    inactive = str(make_user("off", "operator", active=False))
    base = f"/api/v1/incidents/{incident_id}"
    people = client.get("/api/v1/incidents/assignees", headers=admin_headers).json()
    assert operator in {item["id"] for item in people}
    assert not {viewer, inactive} & {item["id"] for item in people}
    assert all(set(item) == {"id", "username", "role"} for item in people)
    for record_id in (viewer, inactive, str(uuid4())):
        assert (
            client.patch(base, headers=admin_headers, json={"assignee_id": record_id}).status_code
            == 422
        )
    response = client.patch(
        base, headers=admin_headers, json={"assignee_id": operator, "title": "Investigating worker"}
    )
    assert response.status_code == 200
    before = history(client, admin_headers, incident_id)
    client.patch(
        base, headers=admin_headers, json={"assignee_id": operator, "title": "Investigating worker"}
    )
    assert history(client, admin_headers, incident_id) == before
    with SessionLocal() as db:
        db.get(User, UUID(operator)).is_active = False
        db.commit()
    assert client.get(base, headers=admin_headers).json()["assignee_id"] == operator
    assert client.patch(base, headers=admin_headers, json={"assignee_id": None}).status_code == 200
    response = client.post(
        base + "/status",
        headers=admin_headers,
        json={"status": "in_progress", "comment": "inspect metrics"},
    )
    assert response.status_code == 200
    count = len(history(client, admin_headers, incident_id))
    client.post(
        base + "/status", headers=admin_headers, json={"status": "in_progress", "comment": "same"}
    )
    assert len(history(client, admin_headers, incident_id)) == count
    response = client.post(base + "/comments", headers=admin_headers, json={"comment": "checked"})
    assert response.status_code == 201 and response.json()["actor_type"] == "user"
    repeated = client.post(base + "/comments", headers=admin_headers, json={"comment": "checked"})
    assert repeated.json()["id"] == response.json()["id"]
    event = client.get(base + "/alerts", headers=admin_headers).json()[0]
    ack = f"/api/v1/alerts/{event['id']}/ack"
    client.post(ack, headers=admin_headers, json={"comment": "ack"})
    client.post(ack, headers=admin_headers, json={"comment": "ack"})
    rows = history(client, admin_headers, incident_id)
    assert sum(item["event_type"] == "alert_acknowledged" for item in rows) == 1
    assert all(item["actor_id"] for item in rows if item["actor_type"] == "user")


def test_closed_and_merged_are_readonly(client, admin_headers, incident_id):
    base = f"/api/v1/incidents/{incident_id}"
    event = client.get(base + "/alerts", headers=admin_headers).json()[0]
    post(
        client, alert(event["fingerprint"], datetime.fromisoformat(event["starts_at"]), "resolved")
    )
    verified_state_fixture()
    assert (
        client.post(
            base + "/status", headers=admin_headers, json={"status": "open", "comment": "reopen"}
        ).status_code
        == 409
    )
    response = client.post(
        base + "/status", headers=admin_headers, json={"status": "closed", "comment": "verified"}
    )
    assert response.status_code == 200 and response.json()["closed_at"]
    rows = history(client, admin_headers, incident_id)
    assert (
        client.post(
            base + "/status", headers=admin_headers, json={"status": "closed", "comment": "retry"}
        ).status_code
        == 200
    )
    assert history(client, admin_headers, incident_id) == rows
    assert client.patch(base, headers=admin_headers, json={"title": "change"}).status_code == 409
    assert (
        client.post(
            base + "/comments", headers=admin_headers, json={"comment": "change"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/alerts/{event['id']}/ack", headers=admin_headers, json={"comment": "late"}
        ).status_code
        == 409
    )
    assert client.delete(base, headers=admin_headers).status_code == 405


def test_manual_merge_preserves_history_and_following_recovery(client, admin_headers, server_id):
    with SessionLocal() as db:
        llm = endpoint(db)
        endpoint_id = str(llm.id)
        db.commit()
    start = datetime.now(UTC) - timedelta(minutes=2)
    post(
        client,
        alert("a1", start, server_id=server_id),
        alert("a2", start, endpoint_id=endpoint_id, severity="critical"),
    )
    source = get_incidents(client, admin_headers, llm_endpoint_id=endpoint_id)[0]["id"]
    target = get_incidents(client, admin_headers, server_id=server_id)[0]["id"]
    source_base, target_base = f"/api/v1/incidents/{source}", f"/api/v1/incidents/{target}"
    client.post(
        source_base + "/status",
        headers=admin_headers,
        json={"status": "in_progress", "comment": "working"},
    )
    client.post(
        source_base + "/status",
        headers=admin_headers,
        json={"status": "open", "comment": "retriage"},
    )
    source_history = history(client, admin_headers, source)
    body = {"target_incident_id": target, "comment": "same application outage"}
    merged = client.post(source_base + "/merge", headers=admin_headers, json=body)
    assert merged.status_code == 200, merged.text
    assert merged.json()["status"] == "in_progress" and merged.json()["severity"] == "critical"
    assert client.get(source_base, headers=admin_headers).json()["merged_into_id"] == target
    assert len(history(client, admin_headers, source)) == len(source_history) + 1
    assert client.get(source_base + "/alerts", headers=admin_headers).json() == []
    detail = client.get(target_base, headers=admin_headers).json()
    assert detail["server_ids"] == [server_id] and detail["llm_endpoint_ids"] == [endpoint_id]
    assert detail["alert_count"] == 2
    assert (
        get_incidents(client, admin_headers, server_id=server_id, llm_endpoint_id=endpoint_id)[0][
            "id"
        ]
        == target
    )
    before = history(client, admin_headers, target)
    assert client.post(source_base + "/merge", headers=admin_headers, json=body).status_code == 200
    assert history(client, admin_headers, target) == before
    assert (
        client.patch(source_base, headers=admin_headers, json={"title": "edit"}).status_code == 409
    )
    assert (
        client.post(
            target_base + "/merge",
            headers=admin_headers,
            json={"target_incident_id": target, "comment": "self"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            target_base + "/merge",
            headers=admin_headers,
            json={"target_incident_id": source, "comment": "cycle"},
        ).status_code
        == 409
    )
    # The merged target participates in automatic grouping for both original resources.
    post(client, alert("a3", start + timedelta(seconds=20), endpoint_id=endpoint_id))
    assert client.get(target_base, headers=admin_headers).json()["alert_count"] == 3
    post(client, alert("a1", start, "resolved"), alert("a2", start, "resolved"))
    assert client.get(target_base, headers=admin_headers).json()["status"] == "in_progress"
    post(client, alert("a3", start + timedelta(seconds=20), "resolved"))
    assert client.get(target_base, headers=admin_headers).json()["status"] == "in_progress"
    verified_state_fixture()
    assert client.get(target_base, headers=admin_headers).json()["status"] == "recovered"


def test_batch_recovery_and_firing_do_not_temporarily_recover(client, admin_headers, server_id):
    start = datetime.now(UTC) - timedelta(minutes=2)
    post(client, alert("a1", start, server_id=server_id))
    incident_id = get_incidents(client, admin_headers)[0]["id"]
    post(
        client,
        alert("a1", start, "resolved"),
        alert("a2", start + timedelta(seconds=20), server_id=server_id),
    )
    assert len(get_incidents(client, admin_headers)) == 1
    assert get_incidents(client, admin_headers)[0]["status"] == "open"
    assert not any(
        item["content"].get("to") == "recovered"
        for item in history(client, admin_headers, incident_id)
    )


def test_existing_firing_backfill_preserves_ack_actor(client, admin_headers, server_id, make_user):
    actor = make_user("ops", "operator")
    now = datetime.now(UTC)
    with SessionLocal() as db:
        item = AlertEvent(
            fingerprint="a1",
            starts_at=now,
            status="firing",
            severity="warning",
            labels={"server_id": server_id},
            annotations={},
            server_id=UUID(server_id),
            acknowledged_at=now,
            acknowledged_by=actor,
            comment="pre-upgrade ack",
        )
        db.add(item)
        db.commit()
    assert post(client, alert("a1", now, server_id=server_id))["changed"] == 0
    incident_id = get_incidents(client, admin_headers)[0]["id"]
    row = next(
        item
        for item in history(client, admin_headers, incident_id)
        if item["event_type"] == "alert_acknowledged"
    )
    assert row["actor_id"] == str(actor) and row["content"]["backfilled"]


def test_concurrent_notifications_create_one_incident_and_one_link(
    client, admin_headers, server_id
):
    start = datetime.now(UTC) - timedelta(minutes=1)
    barrier = Barrier(4)

    def send(index):
        barrier.wait(timeout=5)
        return post(client, alert("a1" if index < 2 else "a2", start, server_id=server_id))

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(send, range(4)))
    assert sum(item["changed"] for item in results) == 2
    assert len(get_incidents(client, admin_headers)) == 1
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(IncidentAlert)) == 2
        assert db.scalar(select(func.count()).select_from(IncidentTimeline)) == 5


def test_failure_rolls_back_entire_notification(client, admin_headers, server_id, monkeypatch):
    import app.services.alerts as alerts_api

    original = alerts_api.attach_alert
    called = 0

    def fail(db, event):
        nonlocal called
        called += 1
        incident = original(db, event)
        if called == 2:
            raise RuntimeError("simulated incident failure")
        return incident

    monkeypatch.setattr(alerts_api, "attach_alert", fail)
    with pytest.raises(RuntimeError, match="simulated incident failure"):
        post(client, alert("a1", server_id=server_id), alert("a2", server_id=server_id))
    with SessionLocal() as db:
        for model in (AlertEvent, Incident, IncidentAlert, IncidentTimeline):
            assert db.scalar(select(func.count()).select_from(model)) == 0


def test_unique_membership_and_timestamp_update(client, admin_headers, incident_id):
    with SessionLocal() as db:
        member = db.scalar(select(IncidentAlert))
        before = member.updated_at
        member.incident_id = member.incident_id
        db.add(IncidentAlert(incident_id=member.incident_id, alert_event_id=member.alert_event_id))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        assert db.scalar(select(IncidentAlert)).updated_at == before
    response = client.patch(
        f"/api/v1/incidents/{incident_id}", headers=admin_headers, json={"title": "new title"}
    )
    assert response.status_code == 200
    with SessionLocal() as db:
        item = db.get(Incident, UUID(incident_id))
        assert item.updated_at > item.created_at


def test_filters_pagination_and_input_validation(client, admin_headers, incident_id, server_id):
    base = f"/api/v1/incidents/{incident_id}"
    assert len(get_incidents(client, admin_headers, server_id=server_id, status="open")) == 1
    assert get_incidents(client, admin_headers, status="closed") == []
    assert get_incidents(client, admin_headers, server_id=server_id, offset=1) == []
    for body in ({}, {"title": None}, {"title": "  "}, {"unknown": "x"}):
        assert client.patch(base, headers=admin_headers, json=body).status_code == 422
    for params in (
        {"limit": 101},
        {"offset": -1},
        {"status": "invalid"},
        {"start": "2026-10-02"},
        {"start": "2026-10-02T00:00:00Z", "end": "2026-10-01T00:00:00Z"},
    ):
        assert (
            client.get("/api/v1/incidents", headers=admin_headers, params=params).status_code == 422
        )
    for i in range(55):
        assert (
            client.post(
                base + "/comments", headers=admin_headers, json={"comment": f"note {i}"}
            ).status_code
            == 201
        )
    detail = client.get(base, headers=admin_headers).json()
    assert len(detail["timeline"]) == 50
    all_rows = history(client, admin_headers, incident_id)
    assert len(all_rows) == 58
    assert client.get(base + "/timeline?offset=50", headers=admin_headers).json() == all_rows[50:]
    assert [item["occurred_at"] for item in all_rows] == sorted(
        item["occurred_at"] for item in all_rows
    )
    with SessionLocal() as db:
        db.get(Server, UUID(server_id)).deleted_at = datetime.now(UTC)
        db.commit()
    assert client.get(base, headers=admin_headers).json()["server_ids"] == [server_id]


def test_container_resource_without_server_label(client, admin_headers, server_id):
    with SessionLocal() as db:
        item = Container(
            server_id=UUID(server_id),
            docker_id="/test",
            name="worker",
            image="test",
            status="running",
            labels={},
            last_seen_at=datetime.now(UTC),
        )
        db.add(item)
        db.flush()
        container_id = str(item.id)
        db.commit()
    start = datetime.now(UTC) - timedelta(minutes=1)
    post(
        client,
        alert("a1", start, server_id=server_id),
        alert("a2", start, container_id=container_id),
    )
    assert len(get_incidents(client, admin_headers)) == 1
    assert len(get_incidents(client, admin_headers, server_id=server_id)) == 1


def test_firing_and_resolution_in_same_payload(client, admin_headers, server_id):
    start = datetime.now(UTC) - timedelta(minutes=1)
    assert (
        post(
            client,
            alert("a1", start, server_id=server_id),
            alert("a1", start, "resolved", server_id=server_id),
        )["changed"]
        == 2
    )
    rows = get_incidents(client, admin_headers)
    assert len(rows) == 1 and rows[0]["status"] == "open"
    assert rows[0]["recovered_at"] is None
    assert (
        sum(
            item["event_type"] == "alert_resolved"
            for item in history(client, admin_headers, rows[0]["id"])
        )
        == 1
    )


def test_merge_two_recovered_and_reject_closed_target(client, admin_headers):
    start = datetime.now(UTC) - timedelta(minutes=2)
    post(client, alert("a1", start), alert("a2", start))
    rows = get_incidents(client, admin_headers)
    target, source = rows[0]["id"], rows[1]["id"]
    post(client, alert("a1", start, "resolved"), alert("a2", start, "resolved"))
    verified_state_fixture()
    response = client.post(
        f"/api/v1/incidents/{source}/merge",
        headers=admin_headers,
        json={"target_incident_id": target, "comment": "same resolved outage"},
    )
    assert response.status_code == 200 and response.json()["status"] == "recovered"
    client.post(
        f"/api/v1/incidents/{target}/status",
        headers=admin_headers,
        json={"status": "closed", "comment": "done"},
    )
    post(client, alert("a3", start))
    fresh = get_incidents(client, admin_headers, status="open")[0]["id"]
    assert (
        client.post(
            f"/api/v1/incidents/{fresh}/merge",
            headers=admin_headers,
            json={"target_incident_id": target, "comment": "closed target"},
        ).status_code
        == 409
    )


def test_concurrent_merge_and_recovery_are_atomic(client, admin_headers):
    start = datetime.now(UTC) - timedelta(minutes=1)
    post(client, alert("a1", start), alert("a2", start))
    target, source = [item["id"] for item in get_incidents(client, admin_headers)]
    barrier = Barrier(2)

    def merging():
        barrier.wait(timeout=5)
        return client.post(
            f"/api/v1/incidents/{source}/merge",
            headers=admin_headers,
            json={"target_incident_id": target, "comment": "merge during recovery"},
        )

    def recovering():
        barrier.wait(timeout=5)
        return post(client, alert("a1", start, "resolved"), alert("a2", start, "resolved"))

    with ThreadPoolExecutor(max_workers=2) as pool:
        merged = pool.submit(merging)
        resolved = pool.submit(recovering)
        assert merged.result().status_code == 200
        assert resolved.result()["changed"] == 2
    detail = client.get(f"/api/v1/incidents/{target}", headers=admin_headers).json()
    assert detail["alert_count"] == 2 and detail["status"] == "open"
    assert detail["recovered_at"] is None
    assert (
        client.get(f"/api/v1/incidents/{source}", headers=admin_headers).json()["status"]
        == "merged"
    )


def test_merge_failure_rolls_back_membership_and_history(client, admin_headers, monkeypatch):
    import app.services.incidents.management as incident_api

    post(client, alert("a1"), alert("a2"))
    target, source = [item["id"] for item in get_incidents(client, admin_headers)]
    before = {
        record_id: history(client, admin_headers, record_id) for record_id in (target, source)
    }
    original = incident_api.recompute

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("simulated merge failure")

    monkeypatch.setattr(incident_api, "recompute", fail)
    with pytest.raises(RuntimeError, match="simulated merge failure"):
        client.post(
            f"/api/v1/incidents/{source}/merge",
            headers=admin_headers,
            json={"target_incident_id": target, "comment": "test"},
        )
    for record_id in (target, source):
        assert history(client, admin_headers, record_id) == before[record_id]
        assert (
            client.get(f"/api/v1/incidents/{record_id}", headers=admin_headers).json()[
                "alert_count"
            ]
            == 1
        )
