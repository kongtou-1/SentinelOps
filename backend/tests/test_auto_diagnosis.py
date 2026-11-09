from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from sqlalchemy import select
from test_ai import ai_config as ai_config
from test_incidents import alert, get_incidents, post
from test_resources_alerts import isolated_secrets as isolated_secrets
from test_resources_alerts import server_body as server_body
from test_resources_alerts import server_id as server_id

from app.db.session import SessionLocal
from app.models import AgentAnalysis, AlertEvent, IncidentTimeline, User
from app.services.diagnosis.analyses import complete, requester_valid
from app.workers.ai import analyze_once

PATH = "/api/v1/system/diagnosis-policy"


def configure(client, headers, enabled):
    response = client.put(PATH, headers=headers, json={"enabled": enabled})
    assert response.status_code == 200, response.text
    return response.json()


def rule(client, headers, override=None):
    response = client.post(
        "/api/v1/alert-rules",
        headers=headers,
        json={"code": "AutoTest", "metric": "target_up", "threshold": 1, "auto_diagnose": override},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def analyses():
    with SessionLocal() as db:
        return db.scalars(select(AgentAnalysis)).all()


@pytest.mark.parametrize(
    "role,read_status,write_status",
    [("viewer", 403, 403), ("operator", 200, 403), ("admin", 200, 200)],
)
def test_configuration_permissions(client, make_user, login, role, read_status, write_status):
    make_user(role, role)
    headers = {"Authorization": "Bearer " + login(role)["access_token"]}
    assert client.get(PATH, headers=headers).status_code == read_status
    assert client.put(PATH, headers=headers, json={"enabled": True}).status_code == write_status
    assert client.put(PATH, json={"enabled": True}).status_code == 401


@pytest.mark.parametrize(
    "global_enabled,override,expected",
    [
        (False, None, 0),
        (True, None, 1),
        (False, True, 1),
        (True, True, 1),
        (True, False, 0),
        (False, False, 0),
    ],
)
def test_global_default_and_rule_override(
    client, admin_headers, server_id, ai_config, global_enabled, override, expected
):
    assert client.get(PATH, headers=admin_headers).json()["enabled"] is False
    configure(client, admin_headers, global_enabled)
    identifier = rule(client, admin_headers, override)
    post(client, alert(server_id=server_id, rule_id=identifier))
    rows = analyses()
    assert len(rows) == expected
    if expected:
        assert rows[0].trigger == "automatic" and rows[0].status == "queued"
        with SessionLocal() as db:
            timeline = db.scalar(
                select(IncidentTimeline).where(IncidentTimeline.event_type == "analysis_queued")
            )
            assert timeline.actor_type == "system" and timeline.actor_id is None
            assert timeline.content["authorized_by"] == str(rows[0].requested_by)
        response = client.get(
            f"/api/v1/incidents/{rows[0].incident_id}/analysis", headers=admin_headers
        )
        assert response.status_code == 200 and response.json()["trigger"] == "automatic"


def test_duplicate_concurrent_and_later_alerts_only_queue_once(
    client, admin_headers, server_id, ai_config
):
    configure(client, admin_headers, True)
    event = alert(server_id=server_id)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _: post(client, event), range(3)))
    assert len(analyses()) == 1
    with SessionLocal() as db:
        row = db.scalar(select(AgentAnalysis))
        complete(db, row.id, None, [], [], [], error="provider_failed")
    post(client, alert("b2", server_id=server_id))
    assert len(analyses()) == 1
    assert analyses()[0].status == "failed"


def test_enabling_does_not_backfill_repeated_old_alerts_or_resolved_events(
    client, admin_headers, server_id, ai_config
):
    event = alert(server_id=server_id)
    post(client, event)
    configure(client, admin_headers, True)
    post(client, event)
    post(client, alert("b2", status="resolved", server_id=server_id))
    assert analyses() == []


def test_existing_manual_analysis_suppresses_automatic_attempt(
    client, admin_headers, server_id, ai_config
):
    post(client, alert(server_id=server_id))
    incident = get_incidents(client, admin_headers)[0]["id"]
    response = client.post(f"/api/v1/incidents/{incident}/analyze", headers=admin_headers, json={})
    assert response.status_code == 202
    configure(client, admin_headers, True)
    post(client, alert("b2", server_id=server_id))
    assert len(analyses()) == 1 and analyses()[0].trigger == "manual"


def test_active_manual_conversation_suppresses_automatic_attempt(
    client, admin_headers, server_id, ai_config
):
    from test_conversations import create, send

    post(client, alert(server_id=server_id))
    identifier = get_incidents(client, admin_headers)[0]["id"]
    conversation = create(client, admin_headers, {"incident_id": identifier})
    send(client, admin_headers, conversation)
    configure(client, admin_headers, True)
    post(client, alert("b2", server_id=server_id))
    assert analyses() == []


@pytest.mark.parametrize("failure", ["unconfigured", "enqueue_error"])
def test_queue_failure_never_loses_alert(
    client, admin_headers, server_id, ai_config, monkeypatch, failure
):
    configure(client, admin_headers, True)
    if failure == "unconfigured":
        monkeypatch.setattr(ai_config, "ai_enabled", False)
    else:

        def fail(*args, **kwargs):
            raise RuntimeError("sensitive provider contents")

        monkeypatch.setattr("app.services.diagnosis.policy.enqueue", fail)
    post(client, alert(server_id=server_id))
    assert analyses() == []
    with SessionLocal() as db:
        assert db.scalar(select(AlertEvent)) is not None
        skipped = db.scalar(
            select(IncidentTimeline).where(IncidentTimeline.event_type == "analysis_auto_skipped")
        )
        assert skipped.content == {
            "reason": "ai_unavailable" if failure == "unconfigured" else "enqueue_failed"
        }


@pytest.mark.parametrize("change", ["disable_policy", "disable_user", "close_incident"])
def test_policy_revocation_and_terminal_incident_block_worker(
    client, admin_headers, server_id, ai_config, monkeypatch, change
):
    configure(client, admin_headers, True)
    post(client, alert(server_id=server_id))
    identifier = analyses()[0].id
    if change == "disable_policy":
        configure(client, admin_headers, False)
    else:
        from app.models import Incident

        with SessionLocal() as db:
            row = db.get(AgentAnalysis, identifier)
            if change == "disable_user":
                db.get(User, row.requested_by).is_active = False
            else:
                db.get(Incident, row.incident_id).status = "closed"
            db.commit()
    calls = []
    monkeypatch.setattr("app.workers.ai.HarnessAdapter.run", lambda *args: calls.append(args))
    assert analyze_once()
    assert calls == []
    assert analyses()[0].status == "failed"


def test_automatic_worker_produces_normal_read_only_diagnosis(
    client, admin_headers, server_id, ai_config, monkeypatch
):
    import json

    from test_ai import diagnosis

    configure(client, admin_headers, True)
    post(client, alert(server_id=server_id))
    monkeypatch.setattr("app.services.diagnosis.tools.search", lambda *args, **kwargs: [])

    def run(self, url, token, model, prompt, session_id, timeout):
        payload = json.loads(prompt.split("\n", 1)[1])
        return diagnosis(payload["context"]["id"]), None

    monkeypatch.setattr("app.workers.ai.HarnessAdapter.run", run)
    assert analyze_once()
    rows = analyses()
    assert rows[0].status == "completed" and rows[0].trigger == "automatic"
    assert rows[0].result["requires_human_approval"] is True
    assert rows[0].result["proposal"] is None
    with SessionLocal() as db:
        assert requester_valid(db, db.get(AgentAnalysis, UUID(str(rows[0].id))))


def test_disabling_or_deleting_override_revokes_task(client, admin_headers, server_id, ai_config):
    identifier = rule(client, admin_headers, True)
    post(client, alert(server_id=server_id, rule_id=identifier))
    with SessionLocal() as db:
        assert requester_valid(db, db.scalar(select(AgentAnalysis)))
    response = client.put(
        f"/api/v1/alert-rules/{identifier}",
        headers=admin_headers,
        json={"code": "AutoTest", "metric": "target_up", "threshold": 1, "auto_diagnose": False},
    )
    assert response.status_code == 200
    with SessionLocal() as db:
        assert not requester_valid(db, db.scalar(select(AgentAnalysis)))
    configure(client, admin_headers, True)
    client.delete(f"/api/v1/alert-rules/{identifier}", headers=admin_headers)
    with SessionLocal() as db:
        assert not requester_valid(db, db.scalar(select(AgentAnalysis)))


def test_retired_rule_notification_does_not_use_global(client, admin_headers, server_id, ai_config):
    configure(client, admin_headers, True)
    identifier = rule(client, admin_headers, False)
    assert (
        client.delete(f"/api/v1/alert-rules/{identifier}", headers=admin_headers).status_code == 204
    )
    post(client, alert(server_id=server_id, rule_id=identifier))
    assert analyses() == []


def test_new_policy_has_timestamp_trigger_and_automatic_unique_index(
    client, admin_headers, server_id, ai_config
):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    configure(client, admin_headers, True)
    post(client, alert(server_id=server_id))
    with SessionLocal() as db:
        before = db.scalar(text("SELECT updated_at FROM diagnosis_policy WHERE id = 1"))
        after = db.scalar(
            text("UPDATE diagnosis_policy SET enabled=enabled WHERE id = 1 RETURNING updated_at")
        )
        assert after > before
        row = db.scalar(select(AgentAnalysis))
        complete(db, row.id, None, [], [], [], error="provider_failed")
        with pytest.raises(IntegrityError), db.begin_nested():
            db.add(
                AgentAnalysis(
                    incident_id=row.incident_id,
                    requested_by=row.requested_by,
                    trigger="automatic",
                    input_hash="a" * 64,
                    config={},
                )
            )
            db.flush()
