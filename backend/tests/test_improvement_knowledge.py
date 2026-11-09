from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import literal, select

from app.db.session import SessionLocal
from app.models import (
    Incident,
    KnowledgeChunk,
    KnowledgeDocument,
    Notification,
    PostmortemReport,
    PostmortemRevision,
    User,
)
from app.repositories.knowledge.index import search_query
from app.services.operations.improvements import remind_due


def report_fixture():
    with SessionLocal() as db:
        now = datetime.now(UTC)
        user = db.scalar(select(User).where(User.username == "admin"))
        incident = Incident(
            title="Reviewed report",
            severity="warning",
            status="closed",
            started_at=now,
            last_alert_started_at=now,
        )
        db.add(incident)
        db.flush()
        report = PostmortemReport(
            incident_id=incident.id,
            requested_by=user.id,
            status="approved",
            revision=1,
            snapshot={"records": []},
            snapshot_hash="a" * 64,
            config={},
        )
        db.add(report)
        db.flush()
        db.add(
            PostmortemRevision(
                report_id=report.id,
                revision=1,
                author_id=user.id,
                content={
                    "improvements": [
                        {
                            "action": "Add capacity check",
                            "owner": "operator text",
                            "deadline": "tomorrow",
                        }
                    ]
                },
                content_hash="b" * 64,
            )
        )
        db.commit()
        return report.id, incident.id


def test_import_submission_rejection_and_admin_acceptance(client, admin_headers, make_user, login):
    report_id, incident_id = report_fixture()
    operator_id = make_user("improver", "operator")
    operator = {"Authorization": "Bearer " + login("improver")["access_token"]}
    path = f"/api/v1/reports/{report_id}/improvements"
    body = {"expected_revision": 1, "item_indexes": [0]}
    response = client.post(path, headers=operator, json=body)
    assert response.status_code == 200, response.text
    task = response.json()[0]
    assert task["status"] == "unassigned" and task["assignee_id"] is None and task["due_at"] is None
    assert client.post(path, headers=operator, json=body).json()[0]["id"] == task["id"]
    path = "/api/v1/improvement-tasks/" + task["id"]
    update = {
        "expected_version": 1,
        "assignee_id": str(operator_id),
        "due_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        "acceptance": "Capacity regression passes",
        "status": "in_progress",
    }
    response = client.put(path, headers=operator, json=update)
    assert response.status_code == 200, response.text
    assert client.put(path, headers=operator, json=update).status_code == 409
    with SessionLocal() as db:
        remind_due(db)
        remind_due(db)
        notifications = db.scalars(
            select(Notification).where(Notification.resource_id == UUID(task["id"]))
        ).all()
        assert len(notifications) == 2  # Owner and Admin, once each.
        assert db.get(Incident, incident_id).status == "closed"
    submit = {"expected_version": 2, "result": "Validated change and tests", "handoff_ids": []}
    response = client.post(path + "/submit", headers=operator, json=submit)
    assert response.status_code == 200 and response.json()["status"] == "awaiting_review"
    decision = {"expected_version": 3, "accepted": False, "evidence": "Add deployment evidence"}
    assert client.post(path + "/decision", headers=operator, json=decision).status_code == 403
    response = client.post(path + "/decision", headers=admin_headers, json=decision)
    assert (
        response.json()["status"] == "in_progress"
        and response.json()["submission"]["result"] == submit["result"]
    )
    assert (
        client.post(
            path + "/submit", headers=operator, json={**submit, "expected_version": 4}
        ).status_code
        == 200
    )
    response = client.post(
        path + "/decision",
        headers=admin_headers,
        json={**decision, "expected_version": 5, "accepted": True},
    )
    assert response.status_code == 200 and response.json()["status"] == "completed"
    assert (
        client.put(path, headers=operator, json={**update, "expected_version": 6}).status_code
        == 409
    )


def test_knowledge_from_reviewed_report_starts_disabled_and_is_idempotent(client, admin_headers):
    report_id, _ = report_fixture()
    body = {
        "expected_revision": 1,
        "title": "Capacity knowledge",
        "content": "Check CPU and resource limits",
    }
    response = client.post(
        f"/api/v1/reports/{report_id}/knowledge-draft", headers=admin_headers, json=body
    )
    assert response.status_code == 201, response.text
    assert response.json()["enabled"] is False
    again = client.post(
        f"/api/v1/reports/{report_id}/knowledge-draft", headers=admin_headers, json=body
    )
    assert again.json()["id"] == response.json()["id"]


def test_expired_knowledge_leaves_current_search_and_scope_filters(client, admin_headers):
    application_id = str(uuid4())
    with SessionLocal() as db:
        rows = [
            KnowledgeDocument(
                title=name,
                content=name,
                index_status="ready",
                application_ids=scope,
                applicable_versions=versions,
                expires_at=expiry,
            )
            for name, scope, versions, expiry in [
                ("legacy", [], [], None),
                ("expired", [], [], datetime.now(UTC) - timedelta(seconds=1)),
                ("scoped", [application_id], ["v2"], None),
            ]
        ]
        db.add_all(rows)
        db.flush()
        for row in rows:
            db.add(
                KnowledgeChunk(
                    document_id=row.id,
                    version=1,
                    position=0,
                    content=row.content,
                    embedding=[0.1] * 512,
                )
            )
        db.commit()
        legacy_id, expired_id = rows[0].id, rows[1].id
        assert {r[1].title for r in db.execute(search_query(literal(0.5), [], [])).all()} == {
            "legacy"
        }
        assert {
            r[1].title
            for r in db.execute(search_query(literal(0.5), [application_id], ["v2"])).all()
        } == {"legacy", "scoped"}
        assert {
            r[1].title
            for r in db.execute(search_query(literal(0.5), [application_id], ["v1"])).all()
        } == {"legacy"}
        other = str(uuid4())
        assert {
            r[1].title
            for r in db.execute(
                search_query(
                    literal(0.5), [application_id, other], {application_id: ["v1"], other: ["v2"]}
                )
            ).all()
        } == {"legacy"}
    assert client.get(f"/api/v1/knowledge/{expired_id}", headers=admin_headers).status_code == 200
    body = {
        "expected_version": 1,
        "application_ids": [],
        "applicable_versions": [],
        "owner_id": None,
        "review_at": None,
        "expires_at": None,
    }
    path = f"/api/v1/knowledge/{legacy_id}/validity"
    assert client.put(path, headers=admin_headers, json=body).status_code == 200
    assert client.put(path, headers=admin_headers, json=body).status_code == 409
