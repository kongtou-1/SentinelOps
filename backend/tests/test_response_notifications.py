import smtplib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import (
    AlertEvent,
    Application,
    Incident,
    IncidentAlert,
    IncidentResponseRecord,
    Notification,
    NotificationPolicy,
    ServiceMonitor,
    User,
)
from app.services.operations import mail_settings, notifications, response


def test_operation_records_include_actor_names_and_preserve_deleted_targets(client, admin_headers):
    with SessionLocal() as db:
        app = Application(code="audit-order", name="订单服务")
        db.add(app)
        db.commit()
        app_id = str(app.id)
    now = datetime.now(UTC)
    window = client.post(
        "/api/v1/maintenance-windows",
        headers=admin_headers,
        json={
            "scope_kind": "service",
            "scope_id": app_id,
            "kind": "maintenance",
            "reason": "例行维护",
            "starts_at": now.isoformat(),
            "ends_at": (now + timedelta(hours=1)).isoformat(),
        },
    )
    assert window.status_code == 201, window.text
    assert (
        client.delete(
            f"/api/v1/maintenance-windows/{window.json()['id']}?expected_version=1",
            headers=admin_headers,
        ).status_code
        == 204
    )
    with SessionLocal() as db:
        notifications.repository.audit(db, None, "notification_failed", "notification")
        db.commit()
    path = "/api/v1/system/operations-audit-logs"
    result = client.get(path + "?limit=1", headers=admin_headers)
    assert result.status_code == 200, result.text
    assert result.headers["X-Total-Count"] == "3"
    assert result.json()[0]["user_id"] is None and result.json()[0]["actor_username"] is None
    rows = client.get(path + "?offset=1&limit=2", headers=admin_headers).json()
    assert {row["action"] for row in rows} == {"maintenance_saved", "maintenance_deleted"}
    assert all(
        row["actor_username"] == "admin" and row["resource_name"] == "订单服务" for row in rows
    )
    with SessionLocal() as db:
        notifications.repository.audit(db, None, "inspection_enqueued", "inspection_run")
        db.commit()
    scoped = client.get(path + "?scope=notifications&limit=1", headers=admin_headers)
    assert scoped.status_code == 200, scoped.text
    assert scoped.headers["X-Total-Count"] == "3"
    assert scoped.json()[0]["action"] == "notification_failed"
    scoped_page = client.get(
        path + "?scope=notifications&offset=1&limit=2", headers=admin_headers
    ).json()
    assert {row["action"] for row in scoped_page} == {"maintenance_saved", "maintenance_deleted"}
    assert client.get(path, headers=admin_headers).headers["X-Total-Count"] == "4"
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "admin"))
        user.deleted_at = now
        db.commit()
        result = notifications.audit_listing(db, Response())
        assert all(row.actor_username == "admin" for row in result if row.user_id)


def test_operation_records_require_audit_permission(client, make_user, login):
    make_user("viewer", "viewer")
    headers = {"Authorization": "Bearer " + login("viewer")["access_token"]}
    assert client.get("/api/v1/system/operations-audit-logs", headers=headers).status_code == 403


def test_notification_switch_restores_previous_files_and_runtime(tmp_path, monkeypatch):
    from app.integrations import notification_config

    original = (
        b"route:\n  receiver: platform\nreceivers:\n- name: platform\n  email_configs:\n"
        b"  - to: operator@example.test\n    from: alerts@example.test\n"
        b"    smarthost: mailpit:1025\n"
    )
    path = tmp_path / "alertmanager.yml"
    path.write_bytes(original)
    monkeypatch.setattr(notification_config.get_settings(), "notification_config_dir", tmp_path)
    calls = []

    def reload(managed):
        calls.append(managed)
        if managed:
            raise ValueError("rejected configuration")

    monkeypatch.setattr(notification_config, "reload_and_verify", reload)
    with pytest.raises(HTTPException) as error:
        notification_config.switch(True)
    assert error.value.status_code == 502
    assert path.read_bytes() == original and calls == [True, False]
    assert not (tmp_path / "mode.json").exists()


def test_explicit_inhibition_cycle_and_reenable(client, admin_headers):
    rules = []
    for code in ("ParentRule", "ChildRule"):
        result = client.post(
            "/api/v1/alert-rules",
            headers=admin_headers,
            json={"code": code, "metric": "target_up", "threshold": 1},
        )
        assert result.status_code == 201, result.text
        rules.append(result.json()["id"])
    body = {"parent_rule_id": rules[0], "child_rule_id": rules[1]}
    result = client.post("/api/v1/alert-inhibitions", headers=admin_headers, json=body)
    assert result.status_code == 201, result.text
    identifier = result.json()["id"]
    assert (
        client.post(
            "/api/v1/alert-inhibitions",
            headers=admin_headers,
            json={"parent_rule_id": rules[1], "child_rule_id": rules[0]},
        ).status_code
        == 422
    )
    assert (
        client.delete(f"/api/v1/alert-inhibitions/{identifier}", headers=admin_headers).status_code
        == 204
    )
    restored = client.post("/api/v1/alert-inhibitions", headers=admin_headers, json=body)
    assert restored.status_code == 201 and restored.json()["id"] == identifier


def setup_incident(client, admin_headers, make_user, login):
    primary = make_user("primary", "operator")
    backup = make_user("backup", "operator")
    now = datetime.now(UTC)
    with SessionLocal() as db:
        app = Application(id=uuid4(), code="response-test", name="Service")
        db.add(app)
        db.flush()
        monitor = ServiceMonitor(
            id=uuid4(),
            application_id=app.id,
            name="API",
            protocol="http",
            target="http://business/ready",
        )
        db.add(monitor)
        db.commit()
        app_id, monitor_id = app.id, monitor.id
    policy = client.put(
        f"/api/v1/business-services/{app_id}/operations-policy",
        headers=admin_headers,
        json={
            "expected_version": 0,
            "response": {
                "primary_id": str(primary),
                "backup_id": str(backup),
                "critical_seconds": 300,
                "warning_seconds": 900,
            },
        },
    )
    assert policy.status_code == 200, policy.text
    with SessionLocal() as db:
        incident = Incident(
            title="API failed",
            severity="critical",
            status="open",
            started_at=now,
            last_alert_started_at=now,
        )
        db.add(incident)
        db.flush()
        event = AlertEvent(
            id=uuid4(),
            fingerprint=uuid4().hex,
            status="firing",
            severity="critical",
            starts_at=now,
            labels={},
            annotations={},
            service_monitor_id=monitor_id,
        )
        db.add(event)
        db.flush()
        db.add(IncidentAlert(incident_id=incident.id, alert_event_id=event.id))
        db.flush()
        response.initialize(db, incident, created=True)
        db.commit()
        identifier = incident.id
    primary_headers = {"Authorization": "Bearer " + login("primary")["access_token"]}
    backup_headers = {"Authorization": "Bearer " + login("backup")["access_token"]}
    return identifier, primary, backup, primary_headers, backup_headers, app_id


@pytest.mark.parametrize("already_read", [False, True])
def test_delete_own_notification_preserves_incident_and_other_recipients(
    client, admin_headers, make_user, login, already_read
):
    identifier, primary, backup, headers, backup_headers, _ = setup_incident(
        client, admin_headers, make_user, login
    )
    row = client.get("/api/v1/notifications", headers=headers).json()[0]
    path = f"/api/v1/notifications/{row['id']}"
    with SessionLocal() as db:
        previous_updated_at = db.get(Notification, UUID(row["id"])).updated_at
        before = dict(
            db.execute(select(Incident.__table__).where(Incident.id == identifier)).mappings().one()
        )
        notifications.enqueue(
            db,
            [backup],
            "incident_opened",
            "incident",
            identifier,
            "API failed",
            incident_id=identifier,
        )
        db.commit()
    if already_read:
        assert client.post(path + "/read", headers=headers).status_code == 200
    assert client.delete(path).status_code == 401
    for other_headers in (backup_headers, admin_headers):
        assert client.delete(path, headers=other_headers).status_code == 404
    assert client.get("/api/v1/notifications", headers=headers).headers["X-Total-Count"] == "1"
    assert client.delete(path, headers=headers).status_code == 204
    feed = client.get("/api/v1/notifications", headers=headers)
    assert feed.json() == [] and feed.headers["X-Total-Count"] == "0"
    assert client.get("/api/v1/notifications?unread_only=true", headers=headers).json() == []
    assert client.get("/api/v1/notifications/count", headers=headers).json() == {"unread": 0}
    assert len(client.get("/api/v1/notifications", headers=backup_headers).json()) == 1
    assert client.delete(path, headers=headers).status_code == 404
    assert client.post(path + "/read", headers=headers).status_code == 404
    assert client.delete(f"/api/v1/notifications/{uuid4()}", headers=headers).status_code == 404
    assert client.get(f"/api/v1/incidents/{identifier}", headers=headers).status_code == 200
    with SessionLocal() as db:
        deleted = db.get(Notification, UUID(row["id"]))
        assert deleted.deleted_at is not None
        assert deleted.updated_at > previous_updated_at
        after = dict(
            db.execute(select(Incident.__table__).where(Incident.id == identifier)).mappings().one()
        )
        assert after == before
        assert (
            notifications.enqueue(
                db,
                [primary],
                "incident_opened",
                "incident",
                identifier,
                "API failed",
                incident_id=identifier,
            )
            == 0
        )
    audit = client.get("/api/v1/system/operations-audit-logs", headers=admin_headers).json()
    assert any(
        entry["action"] == "notification_deleted" and entry["resource_id"] == row["id"]
        for entry in audit
    )


def test_response_freeze_read_is_not_acceptance_and_once_only_escalation(
    client, admin_headers, make_user, login
):
    identifier, primary, backup, headers, backup_headers, app_id = setup_incident(
        client, admin_headers, make_user, login
    )
    rows = client.get("/api/v1/notifications", headers=headers).json()
    assert len(rows) == 1 and rows[0]["kind"] == "incident_opened"
    assert (
        client.post(
            f"/api/v1/notifications/{rows[0]['id']}/read", headers=backup_headers
        ).status_code
        == 404
    )
    assert (
        client.post(f"/api/v1/notifications/{rows[0]['id']}/read", headers=headers).status_code
        == 200
    )
    state = client.get(f"/api/v1/incidents/{identifier}/response", headers=headers).json()
    assert state["accepted_at"] is None
    client.put(
        f"/api/v1/business-services/{app_id}/operations-policy",
        headers=admin_headers,
        json={"expected_version": 1, "response": {"critical_seconds": 1200}},
    )
    with SessionLocal() as db:
        record = db.scalar(
            select(IncidentResponseRecord).where(IncidentResponseRecord.incident_id == identifier)
        )
        assert record.snapshot["services"][str(app_id)]["critical_seconds"] == 300
        due = record.due_at
        response.reconcile(db, now=due + timedelta(seconds=1))
        response.reconcile(db, now=due + timedelta(seconds=60))
    rows = client.get("/api/v1/notifications", headers=backup_headers).json()
    assert len(rows) == 1 and rows[0]["kind"] == "incident_overdue"
    accepted = client.post(f"/api/v1/incidents/{identifier}/accept", headers=headers)
    assert accepted.status_code == 200 and accepted.json()["accepted_by"] == str(primary)
    repeated = client.post(f"/api/v1/incidents/{identifier}/accept", headers=headers).json()
    assert repeated["accepted_at"] == accepted.json()["accepted_at"]
    assert (
        client.get(f"/api/v1/incidents/{identifier}", headers=headers).json()["status"]
        == "in_progress"
    )


def test_maintenance_preserves_facts_and_defers_notifications(
    client, admin_headers, make_user, login
):
    identifier, primary, backup, headers, backup_headers, app_id = setup_incident(
        client, admin_headers, make_user, login
    )
    now = datetime.now(UTC)
    body = {
        "scope_kind": "service",
        "scope_id": str(app_id),
        "kind": "maintenance",
        "reason": "planned test",
        "starts_at": (now - timedelta(minutes=1)).isoformat(),
        "ends_at": (now + timedelta(hours=1)).isoformat(),
    }
    window = client.post("/api/v1/maintenance-windows", json=body, headers=admin_headers)
    assert window.status_code == 201, window.text
    state = client.get(f"/api/v1/incidents/{identifier}/response", headers=headers).json()
    assert state["suppression"][0]["reason"] == "planned test"
    with SessionLocal() as db:
        due = db.scalar(
            select(IncidentResponseRecord).where(IncidentResponseRecord.incident_id == identifier)
        ).due_at
        response.reconcile(db, now=due + timedelta(seconds=1))
        assert db.get(Incident, identifier).status == "open"
        assert (
            db.scalar(select(AlertEvent).where(AlertEvent.service_monitor_id.is_not(None))).status
            == "firing"
        )
    assert client.get("/api/v1/notifications", headers=backup_headers).json() == []
    assert (
        client.delete(
            f"/api/v1/maintenance-windows/{window.json()['id']}?expected_version=1",
            headers=admin_headers,
        ).status_code
        == 204
    )
    with SessionLocal() as db:
        response.reconcile(db, now=due + timedelta(seconds=2))
    assert (
        client.get("/api/v1/notifications", headers=backup_headers).json()[0]["kind"]
        == "incident_overdue"
    )


def test_mail_policy_confirmation_contact_validation_and_revoke(client, admin_headers, monkeypatch):
    monkeypatch.setattr(
        notifications.notification_config, "switch", lambda managed: {"managed": not managed}
    )
    path = "/api/v1/system/notification-policy"
    body = {"expected_version": 0, "managed": True, "email_enabled": True}
    assert client.put(path, json=body, headers=admin_headers).status_code == 422
    body["confirm_legacy_switch"] = True
    # Sender enablement is independent from service recipient configuration.
    assert client.put(path, json=body, headers=admin_headers).status_code == 200
    body["expected_version"] = 1
    identifier = client.get("/api/v1/me", headers=admin_headers).json()["id"]
    assert (
        client.patch(
            f"/api/v1/users/{identifier}",
            headers=admin_headers,
            json={"notification_email": "invalid"},
        ).status_code
        == 422
    )
    result = client.patch(
        f"/api/v1/users/{identifier}",
        headers=admin_headers,
        json={"notification_email": "operator@example.test"},
    )
    assert result.status_code == 200, result.text
    assert client.put(path, json=body, headers=admin_headers).status_code == 200
    with SessionLocal() as db:
        notifications.enqueue(db, [UUID(identifier)], "test", "test", uuid4(), "Notice")
        db.commit()
        row = db.scalar(select(Notification))
        assert row.mail_status == "queued"
        policy = db.get(NotificationPolicy, 1)
        policy.email_enabled = False
        db.commit()
    assert notifications.deliver_once()
    with SessionLocal() as db:
        row = db.scalar(select(Notification))
        assert (
            row.mail_status == "failed"
            and row.error_code == "authorization_or_configuration_changed"
        )


def test_dispatched_smtp_disconnect_is_unknown_and_never_retried(
    client, admin_headers, monkeypatch
):
    identifier = UUID(client.get("/api/v1/me", headers=admin_headers).json()["id"])
    with SessionLocal() as db:
        user = db.get(User, identifier)
        user.notification_email = "operator@example.test"
        db.add(NotificationPolicy(id=1, managed=True, email_enabled=True, configured_by=identifier))
        db.flush()
        notifications.enqueue(db, [identifier], "test", "test", uuid4(), "Notice")
        db.commit()
    calls = []

    class SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def ehlo(self):
            pass

        def mail(self, sender):
            return 250, b"ok"

        def rcpt(self, recipient):
            return 250, b"ok"

        def data(self, payload):
            calls.append(payload)
            raise smtplib.SMTPServerDisconnected

        def close(self):
            pass

    monkeypatch.setattr(notifications.smtplib, "SMTP", SMTP)
    monkeypatch.setattr(mail_settings.get_settings(), "smtp_require_tls", False)
    monkeypatch.setattr(mail_settings.get_settings(), "smtp_username", "")
    assert notifications.deliver_once()
    assert not notifications.deliver_once()
    with SessionLocal() as db:
        notifications.interrupt_deliveries(db)
        row = db.scalar(select(Notification))
        assert row.mail_status == "unknown" and row.dispatched_at is not None
    assert len(calls) == 1
