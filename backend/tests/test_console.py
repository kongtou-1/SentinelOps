from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import (
    AgentAnalysis,
    Approval,
    AuditLog,
    Container,
    Incident,
    PostmortemReport,
    Server,
    SkillExecution,
    User,
)


@pytest.fixture
def console_records(client, admin_headers):
    now = datetime.now(UTC)
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "admin"))
        server = Server(
            name="console_node",
            host="localhost",
            node_exporter_target="node:9100",
            cadvisor_target="cadvisor:8080",
        )
        deleted = Server(
            name="deleted_node",
            host="localhost",
            node_exporter_target="node:9100",
            cadvisor_target="cadvisor:8080",
            deleted_at=now,
        )
        incident = Incident(
            title="Console incident", severity="critical", started_at=now, last_alert_started_at=now
        )
        db.add_all([server, deleted, incident])
        db.flush()
        container = Container(
            server_id=server.id,
            docker_id="/docker/" + "a" * 64,
            name="console-app",
            image="test:local",
            status="running",
            labels={},
            last_seen_at=now,
        )
        db.add(container)
        db.flush()
        approvals = []
        for index, (state, expires, soft) in enumerate(
            [
                ("pending", now + timedelta(minutes=30), False),
                ("pending", now - timedelta(minutes=1), False),
                ("approved", now - timedelta(minutes=1), False),
                ("pending", now + timedelta(minutes=30), True),
            ]
        ):
            analysis = AgentAnalysis(
                incident_id=incident.id,
                requested_by=user.id,
                config={},
                input_hash=str(index) * 64,
                status="completed",
            )
            db.add(analysis)
            db.flush()
            approval = Approval(
                incident_id=incident.id,
                analysis_id=analysis.id,
                container_id=container.id,
                requested_by=user.id,
                status=state,
                binding={},
                binding_hash="a" * 64,
                expires_at=expires,
                deleted_at=now if soft else None,
            )
            db.add(approval)
            approvals.append(approval)
        db.flush()
        execution = SkillExecution(
            incident_id=incident.id,
            approval_id=approvals[0].id,
            container_id=container.id,
            initiated_by=user.id,
            status="succeeded",
            binding_hash="a" * 64,
        )
        report = PostmortemReport(
            incident_id=incident.id,
            requested_by=user.id,
            status="draft",
            snapshot={},
            snapshot_hash="a" * 64,
            config={},
            revision=1,
        )
        db.add_all([execution, report])
        db.commit()
        return {
            "incident": str(incident.id),
            "actor": str(user.id),
            "expired": str(approvals[1].id),
            "server": str(server.id),
        }


def test_global_lists_filter_and_expiry_without_mutating(client, admin_headers, console_records):
    ids = console_records
    for path in ("approvals", "executions", "reports"):
        reply = client.get(
            f"/api/v1/{path}",
            headers=admin_headers,
            params={"incident_id": ids["incident"], "actor_id": ids["actor"], "limit": 1},
        )
        assert reply.status_code == 200
        assert isinstance(reply.json(), list)
        row = reply.json()[0]
        assert row["incident_title"] == "Console incident" and row["actor_username"] == "admin"
        assert "snapshot" not in row and "binding" not in row
        assert int(reply.headers["X-Total-Count"]) == (3 if path == "approvals" else 1)
        assert (
            client.get(f"/api/v1/{path}?incident_id={uuid4()}", headers=admin_headers).json() == []
        )
        assert (
            client.get(
                f"/api/v1/{path}?start=2026-10-04T00:00:00Z&end=2026-10-03T00:00:00Z",
                headers=admin_headers,
            ).status_code
            == 422
        )
    pending = client.get("/api/v1/approvals?status=pending", headers=admin_headers)
    assert pending.headers["X-Total-Count"] == "1"
    expired = client.get("/api/v1/approvals?status=expired", headers=admin_headers)
    assert expired.headers["X-Total-Count"] == "2"
    assert all(row["status"] == "expired" for row in expired.json())
    with SessionLocal() as db:
        assert db.get(Approval, ids["expired"]).status == "pending"


def test_summary_and_array_pagination(client, admin_headers, console_records):
    reply = client.get("/api/v1/dashboard/summary", headers=admin_headers)
    assert reply.status_code == 200
    data = reply.json()
    assert data["servers_total"] == data["servers_monitored"] == 1
    assert data["containers_total"] == 1 and data["containers_by_status"] == {"running": 1}
    assert data["approvals_pending"] == 1
    for path in (
        "servers",
        "containers",
        "llm-endpoints",
        "prediction-tasks",
        "predictions",
        "alerts",
        "alert-rules",
        "incidents",
        "knowledge",
        "skills",
        "ssh-credentials",
        "users",
        "audit-logs",
        "incidents/assignees",
    ):
        response = client.get("/api/v1/" + path, headers=admin_headers)
        assert response.status_code == 200, (path, response.text)
        assert "X-Total-Count" in response.headers, path
        assert isinstance(response.json(), list)
    for q, count in [("console", "1"), ("_", "1"), ("%", "0"), ("deleted", "0")]:
        response = client.get("/api/v1/servers", params={"q": q, "limit": 1}, headers=admin_headers)
        assert response.headers["X-Total-Count"] == count
    empty = client.get("/api/v1/servers?offset=1&limit=1", headers=admin_headers)
    assert empty.json() == [] and empty.headers["X-Total-Count"] == "1"


def test_console_permissions(client, make_user, console_records):
    from conftest import PASSWORD

    for role in ("viewer", "operator"):
        make_user(role, role)
        pair = client.post(
            "/api/v1/auth/login", json={"username": role, "password": PASSWORD}
        ).json()
        headers = {"Authorization": "Bearer " + pair["access_token"]}
        for path in ("dashboard/summary", "approvals", "executions", "reports"):
            assert client.get("/api/v1/" + path, headers=headers).status_code == 200
        assert client.get("/api/v1/audit-logs", headers=headers).status_code == 403
    for path in ("dashboard/summary", "approvals", "executions", "reports"):
        assert client.get("/api/v1/" + path).status_code == 401


def test_event_filters(client, admin_headers, console_records):
    reply = client.get("/api/v1/incidents?unassigned=true", headers=admin_headers)
    assert reply.headers["X-Total-Count"] == "1"
    assert (
        client.get(
            "/api/v1/alerts?severity=critical&acknowledged=false", headers=admin_headers
        ).status_code
        == 200
    )
    assert (
        client.get("/api/v1/audit-logs?action=approval_created", headers=admin_headers).status_code
        == 200
    )
    for path in ("alerts", "audit-logs"):
        assert (
            client.get(
                f"/api/v1/{path}?start=2026-10-04T00:00:00Z&end=2026-10-03T00:00:00Z",
                headers=admin_headers,
            ).status_code
            == 422
        )


def test_audits_show_names_and_search_incident_titles(
    client, admin_headers, console_records, make_user
):
    retired = make_user("retired-operator", "operator")
    now = datetime.now(UTC)
    with SessionLocal() as db:
        actor = db.get(User, retired)
        actor.deleted_at = now
        incident = db.get(Incident, UUID(console_records["incident"]))
        incident.deleted_at = now
        percent_incident = Incident(
            title="Literal_100% incident",
            severity="warning",
            started_at=now,
            last_alert_started_at=now,
        )
        db.add(percent_incident)
        db.flush()
        db.add_all(
            [
                AuditLog(
                    user_id=retired,
                    incident_id=incident.id,
                    action="approval_requested",
                    request_id=str(uuid4()),
                    detail={},
                ),
                AuditLog(
                    user_id=None,
                    incident_id=percent_incident.id,
                    action="report_generated",
                    request_id=str(uuid4()),
                    detail={},
                ),
            ]
        )
        db.commit()
    path = "/api/v1/audit-logs"
    reply = client.get(path, params={"q": "console", "limit": 1}, headers=admin_headers)
    assert reply.status_code == 200, reply.text
    assert reply.headers["X-Total-Count"] == "1"
    row = reply.json()[0]
    assert row["actor_username"] == "retired-operator"
    assert row["incident_title"] == "Console incident"
    assert row["user_id"] == str(retired) and row["request_id"]
    literal = client.get(path, params={"q": "%"}, headers=admin_headers)
    assert literal.headers["X-Total-Count"] == "1"
    assert literal.json()[0]["incident_title"] == "Literal_100% incident"
    assert literal.json()[0]["actor_username"] is None
    assert literal.json()[0]["user_id"] is None
    combined = client.get(
        path, params={"q": "console", "action": "report_generated"}, headers=admin_headers
    )
    assert combined.json() == [] and combined.headers["X-Total-Count"] == "0"
    paged = client.get(path, params={"limit": 1, "offset": 1}, headers=admin_headers)
    assert paged.headers["X-Total-Count"] == "2" and len(paged.json()) == 1
