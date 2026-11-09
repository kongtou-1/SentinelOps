from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_inspection_checks import create, execute
from test_recycle_bin import ROOT, entry, restore

from app import models as m
from app.db.session import SessionLocal


def purge(client, headers, row):
    return client.request(
        "DELETE",
        f"{ROOT}/{row['kind']}/{row['id']}",
        headers=headers,
        json={"expected_deleted_at": row["deleted_at"]},
    )


def server(**values):
    return m.Server(
        name="可清除主机",
        host="127.0.0.1",
        node_exporter_target="node:9100",
        cadvisor_target="cadvisor:8080",
        **values,
    )


@pytest.mark.parametrize("role", ["viewer", "operator"])
def test_public_deletion_requires_admin(client, make_user, login, role):
    make_user(role, role)
    headers = {"Authorization": f"Bearer {login(role)['access_token']}"}
    result = purge(
        client,
        headers,
        {
            "kind": "servers",
            "id": str(uuid4()),
            "deleted_at": datetime.now(UTC).isoformat(),
        },
    )
    assert result.status_code == 403


def test_delete_requires_recycled_record_and_exact_timestamp(client, admin_headers):
    with SessionLocal() as db:
        row = server()
        db.add(row)
        db.commit()
        identifier = row.id
    request = {
        "kind": "servers",
        "id": str(identifier),
        "deleted_at": datetime.now(UTC).isoformat(),
    }
    assert purge(client, admin_headers, request).status_code == 404
    assert client.delete(f"/api/v1/servers/{identifier}", headers=admin_headers).status_code == 204
    recycled = entry(client, admin_headers, "servers", identifier)
    stale = {**recycled, "deleted_at": (datetime.now(UTC) - timedelta(days=1)).isoformat()}
    assert purge(client, admin_headers, stale).status_code == 409
    assert restore(client, admin_headers, recycled).status_code == 200
    assert purge(client, admin_headers, recycled).status_code == 404
    assert client.delete(f"/api/v1/servers/{identifier}", headers=admin_headers).status_code == 204
    recycled_again = entry(client, admin_headers, "servers", identifier)
    assert purge(client, admin_headers, recycled).status_code == 409
    assert purge(client, admin_headers, recycled_again).status_code == 204
    assert restore(client, admin_headers, recycled_again).status_code == 404
    assert purge(client, admin_headers, recycled_again).status_code == 404
    with SessionLocal() as db:
        assert db.get(m.Server, identifier) is None
        audits = db.scalars(
            select(m.OperationsAuditLog).where(
                m.OperationsAuditLog.action == "resource_purged",
            )
        ).all()
        assert len(audits) == 1 and audits[0].resource_id == identifier
        assert audits[0].detail["resource_type"] == "servers"
        # Physical deletion releases the server name, unlike soft deletion.
        db.add(server())
        db.commit()


def test_deleted_child_reference_blocks_parent_and_deletion_preserves_parent(client, admin_headers):
    with SessionLocal() as db:
        parent = server(deleted_at=datetime.now(UTC))
        db.add(parent)
        db.flush()
        child = m.Container(
            server_id=parent.id,
            name="已退役容器",
            docker_id="purge-test",
            image="test:local",
            status="stopped",
            labels={},
            last_seen_at=datetime.now(UTC),
            deleted_at=datetime.now(UTC),
        )
        db.add(child)
        db.commit()
        parent_id, child_id = parent.id, child.id
    parent_entry = entry(client, admin_headers, "servers", parent_id)
    result = purge(client, admin_headers, parent_entry)
    assert result.status_code == 409 and "容器" in result.json()["detail"]
    with SessionLocal() as db:
        assert db.get(m.Server, parent_id) is not None and db.get(m.Container, child_id) is not None
    assert (
        purge(
            client, admin_headers, entry(client, admin_headers, "containers", child_id)
        ).status_code
        == 204
    )
    assert purge(client, admin_headers, parent_entry).status_code == 204


@pytest.mark.parametrize("kind", ["notifications", "analysis-conversations"])
def test_private_deletion_is_owner_only(client, admin_headers, make_user, login, kind):
    owner_id = make_user("purge-owner", "operator")
    make_user("purge-other", "operator")
    with SessionLocal() as db:
        now = datetime.now(UTC)
        if kind == "notifications":
            row = m.Notification(
                user_id=owner_id,
                kind="test",
                title="私人记录",
                resource_kind="test",
                resource_id=uuid4(),
                dedupe_key=str(uuid4()),
                detail={},
                deleted_at=now,
            )
        else:
            incident = m.Incident(
                title="事故", severity="warning", started_at=now, last_alert_started_at=now
            )
            db.add(incident)
            db.flush()
            row = m.AnalysisConversation(
                incident_id=incident.id, owner_id=owner_id, title="私人记录", deleted_at=now
            )
        db.add(row)
        db.flush()
        if kind == "analysis-conversations":
            run = m.AnalysisRun(
                conversation_id=row.id,
                incident_id=incident.id,
                requested_by=owner_id,
                status="completed",
                config={},
            )
            db.add(run)
            db.flush()
            db.add(
                m.AnalysisMessage(
                    conversation_id=row.id,
                    run_id=run.id,
                    role="user",
                    content="私密内容",
                    status="saved",
                )
            )
            db.add(
                m.AnalysisEvent(conversation_id=row.id, run_id=run.id, seq=1, kind="test", data={})
            )
        db.commit()
        identifier, model = row.id, type(row)
    owner = {"Authorization": f"Bearer {login('purge-owner')['access_token']}"}
    other = {"Authorization": f"Bearer {login('purge-other')['access_token']}"}
    recycled = entry(client, owner, kind, identifier)
    for headers in (admin_headers, other):
        assert purge(client, headers, recycled).status_code == 404
    assert purge(client, owner, recycled).status_code == 204
    with SessionLocal() as db:
        assert db.get(model, identifier) is None
        if kind == "analysis-conversations":
            for child in (m.AnalysisMessage, m.AnalysisEvent, m.AnalysisRun):
                assert db.scalar(select(child.id)) is None


def test_knowledge_purge_removes_chunks(client, admin_headers):
    with SessionLocal() as db:
        document = m.KnowledgeDocument(
            title="已删除知识", content="正文", index_status="ready", deleted_at=datetime.now(UTC)
        )
        db.add(document)
        db.flush()
        db.add(
            m.KnowledgeChunk(
                document_id=document.id,
                version=1,
                position=0,
                content="分块",
                embedding=[0.0] * 512,
            )
        )
        db.commit()
        identifier = document.id
    recycled = entry(client, admin_headers, "knowledge", identifier)
    assert purge(client, admin_headers, recycled).status_code == 204
    with SessionLocal() as db:
        assert db.get(m.KnowledgeDocument, identifier) is None
        assert db.scalar(select(m.KnowledgeChunk.id)) is None


def test_database_constraint_failure_rolls_back_without_success_audit(
    client,
    admin_headers,
    monkeypatch,
):
    from app.repositories import recycle_bin

    identifier = uuid4()
    with SessionLocal() as db:
        db.add(server(id=identifier, deleted_at=datetime.now(UTC)))
        db.flush()
        db.add(
            m.Container(
                server_id=identifier,
                name="引用",
                docker_id="constraint",
                image="test:local",
                status="stopped",
                labels={},
                last_seen_at=datetime.now(UTC),
            )
        )
        db.commit()
    recycled = entry(client, admin_headers, "servers", identifier)
    # A reference appearing after the preflight is still enforced by the database.
    monkeypatch.setattr(recycle_bin, "deletion_conflict", lambda *args: None)
    assert purge(client, admin_headers, recycled).status_code == 409
    with SessionLocal() as db:
        assert db.get(m.Server, identifier) is not None
        assert (
            db.scalar(
                select(m.OperationsAuditLog.id).where(
                    m.OperationsAuditLog.action == "resource_purged",
                )
            )
            is None
        )


@pytest.mark.parametrize("reviewed", [False, True])
def test_inspection_purge_details_and_preserve_review_history(client, admin_headers, reviewed):
    plan = create(client, admin_headers)
    run, checks = execute(client, admin_headers, plan)
    with SessionLocal() as db:
        item = db.scalar(select(m.InspectionItem).where(m.InspectionItem.run_id == UUID(run["id"])))
        assert item is not None
        item_id = item.id
        db.add(
            m.InspectionTrace(
                run_id=UUID(run["id"]),
                plan_id=UUID(plan["id"]),
                check_result_id=UUID(checks[0]["id"]),
                tool="local_test",
                success=True,
                data={},
            )
        )
        if reviewed:
            actor = UUID(client.get("/api/v1/me", headers=admin_headers).json()["id"])
            db.add(
                m.InspectionReview(
                    item_id=item_id, reviewer_id=actor, verdict="pass", comment="人工复核"
                )
            )
        db.commit()
    assert (
        client.delete(f"/api/v1/inspection-runs/{run['id']}", headers=admin_headers).status_code
        == 204
    )
    recycled = entry(client, admin_headers, "inspection-runs", run["id"])
    result = purge(client, admin_headers, recycled)
    assert result.status_code == (409 if reviewed else 204), result.text
    with SessionLocal() as db:
        assert (db.get(m.InspectionRun, UUID(run["id"])) is not None) == reviewed
        for model in (m.InspectionItem, m.InspectionCheckResult, m.InspectionTrace):
            assert (db.scalar(select(model.id).limit(1)) is not None) == reviewed
        assert db.get(m.InspectionPlan, UUID(plan["id"])) is not None
        if reviewed:
            assert db.scalar(select(m.InspectionReview.id)) is not None


def test_item_purge_leaves_run_and_other_item(client, admin_headers):
    plan = create(
        client,
        admin_headers,
        fixed_objects=[
            {"id": "a", "facts": {"amount": 1}},
            {"id": "b", "facts": {"amount": 2}},
        ],
    )
    run, _ = execute(client, admin_headers, plan)
    items = client.get(f"/api/v1/inspection-runs/{run['id']}/items", headers=admin_headers).json()
    first = items[0]["id"]
    assert (
        client.delete(f"/api/v1/inspection-items/{first}", headers=admin_headers).status_code == 204
    )
    recycled = entry(client, admin_headers, "inspection-items", first)
    assert purge(client, admin_headers, recycled).status_code == 204
    with SessionLocal() as db:
        assert db.get(m.InspectionItem, UUID(first)) is None
        assert db.get(m.InspectionItem, UUID(items[1]["id"])) is not None
        assert db.get(m.InspectionRun, UUID(run["id"])) is not None
        assert (
            db.scalar(
                select(m.InspectionCheckResult.id).where(
                    m.InspectionCheckResult.item_id == UUID(first),
                )
            )
            is None
        )


def test_plan_with_history_is_protected_and_then_deletable(client, admin_headers):
    plan = create(client, admin_headers)
    run, _ = execute(client, admin_headers, plan)
    assert (
        client.delete(
            f"/api/v1/inspection-plans/{plan['id']}",
            headers=admin_headers,
            params={"expected_version": plan["version"]},
        ).status_code
        == 204
    )
    recycled = entry(client, admin_headers, "inspection-plans", plan["id"])
    assert purge(client, admin_headers, recycled).status_code == 409
    assert (
        client.delete(f"/api/v1/inspection-runs/{run['id']}", headers=admin_headers).status_code
        == 204
    )
    assert (
        purge(
            client, admin_headers, entry(client, admin_headers, "inspection-runs", run["id"])
        ).status_code
        == 204
    )
    assert purge(client, admin_headers, recycled).status_code == 204


def test_connection_json_reference_is_protected(client, admin_headers):
    with SessionLocal() as db:
        connection = m.InspectionHTTPConnection(
            name="已删除连接",
            environment="test",
            base_url="https://example.invalid",
            deleted_at=datetime.now(UTC),
        )
        db.add(connection)
        db.flush()
        db.add(
            m.InspectionDataSource(
                name="仍引用的取数",
                environment="test",
                source_kind="api",
                source_config={"http_connection_id": str(connection.id)},
            )
        )
        db.commit()
        identifier = connection.id
    recycled = entry(client, admin_headers, "inspection-http-connections", identifier)
    assert purge(client, admin_headers, recycled).status_code == 409


def test_deletion_rejects_unsupported_types_and_missing_timestamp(client, admin_headers):
    row = {"kind": "audit-logs", "id": str(uuid4()), "deleted_at": datetime.now(UTC).isoformat()}
    assert purge(client, admin_headers, row).status_code == 422
    url = f"{ROOT}/servers/{uuid4()}"
    assert client.request("DELETE", url, headers=admin_headers, json={}).status_code == 422
    assert (
        client.request(
            "DELETE",
            url,
            headers=admin_headers,
            json={"expected_deleted_at": "2026-10-31T00:00:00"},
        ).status_code
        == 422
    )
    assert (
        client.request("DELETE", url, json={"expected_deleted_at": row["deleted_at"]}).status_code
        == 401
    )
