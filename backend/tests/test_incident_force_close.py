from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from uuid import UUID

import pytest
from alembic import command
from docx import Document
from pypdf import PdfReader
from sqlalchemy import func, select, text
from test_executions import approved, enqueue, request_approval
from test_executions import proposal as proposal
from test_incidents import alert, get_incidents, history, post, verified_state_fixture
from test_incidents import incident_id as incident_id
from test_resources_alerts import isolated_secrets as isolated_secrets
from test_resources_alerts import server_body as server_body
from test_resources_alerts import server_id as server_id

from app.db.session import SessionLocal
from app.models import AuditLog, Incident, IncidentTimeline
from app.services.reports.exports import docx, pdf, sections
from app.services.reports.logic import snapshot
from app.utils.canonical_json import digest
from app.workers.executions import execute_once


def close(client, headers, identifier, *, force=True, comment="接受当前风险，转交人工处置"):
    return client.post(
        f"/api/v1/incidents/{identifier}/status",
        headers=headers,
        json={"status": "closed", "force": force, "comment": comment},
    )


@pytest.mark.parametrize("role,expected", [("viewer", 403), ("operator", 403), ("admin", 200)])
def test_force_close_role_boundary(
    client, admin_headers, incident_id, make_user, login, role, expected
):
    make_user("force-role", role)
    headers = {"Authorization": "Bearer " + login("force-role")["access_token"]}
    result = close(client, headers, incident_id)
    assert result.status_code == expected
    if expected == 200:
        assert result.json()["closure"]["actor_username"] == "force-role"
    else:
        assert (
            client.get(f"/api/v1/incidents/{incident_id}", headers=admin_headers).json()["status"]
            == "open"
        )


@pytest.mark.parametrize("state", ["open", "in_progress", "resolved_pending"])
def test_force_close_keeps_alert_and_recovery_facts(client, admin_headers, incident_id, state):
    base = f"/api/v1/incidents/{incident_id}"
    if state == "in_progress":
        assert (
            client.post(
                base + "/status",
                headers=admin_headers,
                json={"status": "in_progress", "comment": "调查中"},
            ).status_code
            == 200
        )
    elif state == "resolved_pending":
        event = client.get(base + "/alerts", headers=admin_headers).json()[0]
        post(
            client,
            alert(event["fingerprint"], datetime.fromisoformat(event["starts_at"]), "resolved"),
        )
    before = client.get(base + "/alerts", headers=admin_headers).json()
    assert close(client, admin_headers, incident_id, force=False).status_code == 409
    result = close(client, admin_headers, incident_id)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["status"] == "closed" and data["recovered_at"] is None
    assert data["closure"]["forced"] is True and data["closure"]["recovery_verified"] is False
    assert len(data["closure"]["alerts"]) == 1
    after = client.get(base + "/alerts", headers=admin_headers).json()
    assert before == after
    with SessionLocal() as db:
        audit = db.scalar(
            select(AuditLog).where(
                AuditLog.incident_id == UUID(incident_id),
                AuditLog.action == "incident_force_closed",
            )
        )
        assert audit.detail == data["closure"]
        assert db.scalar(
            select(IncidentTimeline.id).where(
                IncidentTimeline.incident_id == UUID(incident_id),
                IncidentTimeline.event_type == "incident_force_closed",
            )
        )


@pytest.mark.parametrize("comment", ["", "   ", "x" * 2001])
def test_force_reason_required(client, admin_headers, incident_id, comment):
    assert close(client, admin_headers, incident_id, comment=comment).status_code == 422


def test_force_only_closes_and_does_not_bypass_merged(client, admin_headers, incident_id):
    base = f"/api/v1/incidents/{incident_id}"
    assert (
        client.post(
            base + "/status",
            headers=admin_headers,
            json={"status": "in_progress", "force": True, "comment": "wrong"},
        ).status_code
        == 422
    )
    with SessionLocal() as db:
        db.get(Incident, UUID(incident_id)).status = "merged"
        db.commit()
    assert close(client, admin_headers, incident_id).status_code == 409


def test_force_close_concurrent_and_retry_are_immutable(
    client, admin_headers, incident_id, make_user, login
):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: close(client, admin_headers, incident_id), range(2)))
    assert all(x.status_code == 200 for x in results)
    closure = results[0].json()["closure"]
    assert results[1].json()["closure"] == closure
    before = history(client, admin_headers, incident_id)
    assert (
        close(client, admin_headers, incident_id, comment="重试不能改写理由").json()["closure"]
        == closure
    )
    assert history(client, admin_headers, incident_id) == before
    make_user("operator-force", "operator")
    headers = {"Authorization": "Bearer " + login("operator-force")["access_token"]}
    assert close(client, headers, incident_id).status_code == 403
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.action == "incident_force_closed",
                    AuditLog.incident_id == UUID(incident_id),
                )
            )
            == 1
        )


def test_late_notifications_do_not_reopen_and_new_occurrence_creates_incident(
    client, admin_headers, incident_id, server_id
):
    base = f"/api/v1/incidents/{incident_id}"
    event = client.get(base + "/alerts", headers=admin_headers).json()[0]
    start = datetime.fromisoformat(event["starts_at"])
    assert close(client, admin_headers, incident_id).status_code == 200
    frozen = client.get(base, headers=admin_headers).json()["closure"]
    post(client, alert(event["fingerprint"], start, server_id=server_id))
    assert len(get_incidents(client, admin_headers)) == 1
    post(
        client,
        alert(event["fingerprint"], start, "resolved", datetime.now(UTC), server_id=server_id),
    )
    data = client.get(base, headers=admin_headers).json()
    assert data["status"] == "closed" and data["recovered_at"] is None and data["closure"] == frozen
    post(
        client,
        alert(event["fingerprint"], datetime.now(UTC) + timedelta(seconds=1), server_id=server_id),
    )
    assert len(get_incidents(client, admin_headers)) == 2


@pytest.mark.parametrize("approval_state", ["pending", "approved", "queued"])
def test_closure_invalidates_unused_approval_and_prevents_queued_ssh(
    client, admin_headers, proposal, monkeypatch, approval_state
):
    row = (
        request_approval(client, admin_headers, proposal)
        if approval_state == "pending"
        else approved(client, admin_headers, proposal)
    )
    execution = enqueue(client, admin_headers, row) if approval_state == "queued" else None
    assert close(client, admin_headers, proposal.incident).status_code == 200
    if execution:
        monkeypatch.setattr(
            "app.workers.executions.ActionSSH", lambda *_: pytest.fail("SSH opened after closure")
        )
        assert execute_once()
        result = client.get(f"/api/v1/executions/{execution['id']}", headers=admin_headers).json()
        assert result["status"] == "failed" and result["error_code"] == "incident_changed"
        assert result["dispatched_at"] is None
    else:
        result = client.get(f"/api/v1/approvals/{row['id']}", headers=admin_headers).json()
        assert result["status"] == "invalidated" and result["error_code"] == "incident_changed"
        assert (
            client.post(f"/api/v1/approvals/{row['id']}/execute", headers=admin_headers).status_code
            == 409
        )


def test_normal_close_preserves_verified_time_and_force_comment_is_redacted(
    client, admin_headers, incident_id
):
    base = f"/api/v1/incidents/{incident_id}"
    event = client.get(base + "/alerts", headers=admin_headers).json()[0]
    post(
        client, alert(event["fingerprint"], datetime.fromisoformat(event["starts_at"]), "resolved")
    )
    verified_state_fixture()
    recovered = client.get(base, headers=admin_headers).json()["recovered_at"]
    result = close(client, admin_headers, incident_id, force=False)
    assert result.status_code == 200
    assert result.json()["recovered_at"] == recovered
    assert result.json()["closure"]["recovery_verified"] is True
    assert result.json()["closure"]["forced"] is False


def test_forced_report_freezes_decision_and_exports_without_recovery_claim(
    client, admin_headers, incident_id
):
    result = close(client, admin_headers, incident_id, comment="接受风险 password=do-not-persist")
    assert result.status_code == 200 and "do-not-persist" not in str(result.json())
    with SessionLocal() as db:
        frozen = snapshot(db, db.get(Incident, UUID(incident_id)))
        assert any("管理员强制关闭" in x for x in frozen["data_gaps"])
        incident_record = next(x for x in frozen["records"] if x["kind"] == "incident")
        assert incident_record["data"]["closure"]["forced"] is True
        row = SimpleNamespace(snapshot=frozen, incident_id=UUID(incident_id))
        content = {
            "summary": "测试",
            "evidence_ids": [],
            "impact": "未知",
            "causes": [],
            "response_and_recovery": "尚未恢复",
            "lessons": [],
            "improvements": [],
            "data_gaps": frozen["data_gaps"],
        }
        rendered = dict(sections(row, content))
        assert "关闭时资源恢复未通过核验" in rendered["管理员强制关闭"][0]
        assert "未取得恢复核验" in rendered["事故时间"][0]
        assert incident_record["data"]["recovered_at"] is None
        row.id = UUID(incident_id)
        row.revision = 1
        row.reviewed_by = UUID(incident_record["data"]["closure"]["actor_id"])
        row.reviewed_at = datetime.now(UTC)
        row.snapshot_hash = digest(frozen)
        document = Document(BytesIO(docx(row, content)))
        word_text = "\n".join(p.text for p in document.paragraphs)
        pdf_text = "".join(p.extract_text() for p in PdfReader(BytesIO(pdf(row, content))).pages)
        for rendered_text in (word_text, pdf_text):
            assert "管理员强制关闭" in rendered_text
            assert "关闭时资源恢复未通过核验" in rendered_text
            assert "do-not-persist" not in rendered_text


@pytest.mark.parametrize("when", ["precheck", "dispatched"])
def test_close_during_execution_respects_dispatch_boundary(
    client, admin_headers, proposal, monkeypatch, when
):
    execution = enqueue(client, admin_headers, approved(client, admin_headers, proposal))
    calls = []

    class SSH:
        def __init__(self, *_):
            pass

        def inspect(self):
            if when == "precheck":
                assert close(client, admin_headers, proposal.incident).status_code == 200
            return {"paused": False, "restarting": False, "status": "running"}

        def restart(self):
            calls.append("dispatched")
            assert close(client, admin_headers, proposal.incident).status_code == 200
            return {"exit_code": 0, "stdout": "", "stderr": ""}

        def close(self):
            pass

    monkeypatch.setattr("app.workers.executions.ActionSSH", SSH)
    monkeypatch.setattr("app.workers.executions.verify", lambda _: {"passed": True})
    assert execute_once()
    result = client.get(f"/api/v1/executions/{execution['id']}", headers=admin_headers).json()
    assert result["status"] == ("failed" if when == "precheck" else "succeeded")
    assert bool(result["dispatched_at"]) == (when == "dispatched")
    assert calls == ([] if when == "precheck" else ["dispatched"])
    incident = client.get(f"/api/v1/incidents/{proposal.incident}", headers=admin_headers).json()
    assert incident["status"] == "closed" and incident["recovered_at"] is None


def test_closure_migration_preserves_old_rows_and_utc_timestamps(
    client, admin_headers, incident_id, migrated_database
):
    try:
        command.downgrade(migrated_database, "0015_repair_handoffs")
        assert client.get("/health/ready").status_code == 503
        command.upgrade(migrated_database, "head")
        assert client.get("/health/ready").status_code == 200
        command.check(migrated_database)
        with SessionLocal() as db:
            incident = db.get(Incident, UUID(incident_id))
            assert (
                incident.closure == {} and incident.created_at.tzinfo and incident.updated_at.tzinfo
            )
            previous = incident.updated_at
            db.execute(
                text("UPDATE incidents SET closure = '{\"forced\": false}' WHERE id=:id"),
                {"id": incident.id},
            )
            db.commit()
            db.refresh(incident)
            assert incident.updated_at > previous and incident.deleted_at is None
    finally:
        command.upgrade(migrated_database, "head")
