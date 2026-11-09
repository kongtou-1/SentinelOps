from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_inspection_checks import create, execute

from app import models as m
from app.db.session import SessionLocal

ROOT = "/api/v1/system/recycle-bin"


def entry(client, headers, kind, identifier):
    result = client.get(ROOT, headers=headers, params={"kind": kind})
    assert result.status_code == 200, result.text
    return next(row for row in result.json() if row["id"] == str(identifier))


def restore(client, headers, row):
    return client.post(
        f"{ROOT}/{row['kind']}/{row['id']}/restore",
        headers=headers,
        json={"expected_deleted_at": row["deleted_at"]},
    )


@pytest.mark.parametrize("role", ["viewer", "operator"])
def test_admin_boundary(client, make_user, login, role):
    make_user(role, role)
    headers = {"Authorization": f"Bearer {login(role)['access_token']}"}
    result = client.get(ROOT, headers=headers, params={"kind": "servers"})
    assert result.status_code == 403
    allowed = client.get(ROOT + "/types", headers=headers)
    assert allowed.status_code == 200
    assert {row["kind"] for row in allowed.json()} <= {"notifications", "analysis-conversations"}
    assert (
        client.post(
            f"{ROOT}/users/{uuid4()}/restore",
            headers=headers,
            json={"expected_deleted_at": datetime.now(UTC).isoformat()},
        ).status_code
        == 403
    )


def test_catalogue_excludes_authorization_and_audit(client, admin_headers):
    result = client.get(ROOT + "/types", headers=admin_headers)
    assert result.status_code == 200, result.text
    kinds = {row["kind"] for row in result.json()}
    assert len(kinds) == 26
    assert {"servers", "inspection-plans", "users", "knowledge", "agent-channels"} <= kinds
    assert not {"approvals", "auth-sessions", "audit-logs", "access-grants"} & kinds
    assert client.get(ROOT, params={"kind": "approvals"}, headers=admin_headers).status_code == 422
    assert client.get(ROOT).status_code == 401


@pytest.mark.parametrize("run_only", [False, True])
def test_private_conversation_owner_and_diagnosis_permission(
    client, admin_headers, make_user, login, run_only
):
    owner_id = make_user("conversation-owner", "operator")
    make_user("conversation-other", "operator")
    with SessionLocal() as db:
        if run_only:
            role_id = db.scalar(select(m.User.role_id).where(m.User.id == owner_id))
            permission = db.scalar(
                select(m.RolePermission).where(
                    m.RolePermission.role_id == role_id,
                    m.RolePermission.permission == "analyses:read",
                )
            )
            db.delete(permission)
        now = datetime.now(UTC)
        incident = m.Incident(
            title="私人会话恢复事故",
            severity="warning",
            started_at=now,
            last_alert_started_at=now,
        )
        db.add(incident)
        db.flush()
        conversation = m.AnalysisConversation(
            incident_id=incident.id,
            owner_id=owner_id,
            title="私人会话恢复",
            deleted_at=now,
        )
        db.add(conversation)
        db.commit()
        identifier = conversation.id
    owner = {"Authorization": f"Bearer {login('conversation-owner')['access_token']}"}
    other = {"Authorization": f"Bearer {login('conversation-other')['access_token']}"}
    for headers in (other, admin_headers):
        assert client.get(ROOT, headers=headers).json() == []
        assert (
            restore(
                client,
                headers,
                {
                    "kind": "analysis-conversations",
                    "id": str(identifier),
                    "deleted_at": now.isoformat(),
                },
            ).status_code
            == 404
        )
    row = entry(client, owner, "analysis-conversations", identifier)
    assert restore(client, owner, row).status_code == 200


def test_inspection_run_restores_only_its_delete_cohort(client, admin_headers):
    plan = create(
        client,
        admin_headers,
        fixed_objects=[
            {"id": "a", "facts": {"amount": 1}},
            {"id": "b", "facts": {"amount": 2}},
        ],
    )
    run, _ = execute(client, admin_headers, plan)
    items_url = f"/api/v1/inspection-runs/{run['id']}/items"
    items = client.get(items_url, headers=admin_headers).json()
    assert len(items) == 2
    assert (
        client.delete(
            f"/api/v1/inspection-items/{items[0]['id']}", headers=admin_headers
        ).status_code
        == 204
    )
    assert (
        client.delete(f"/api/v1/inspection-runs/{run['id']}", headers=admin_headers).status_code
        == 204
    )
    row = entry(client, admin_headers, "inspection-runs", run["id"])
    result = restore(client, admin_headers, row)
    assert result.status_code == 200, result.text
    assert result.json()["restored_children"] == 1
    restored = client.get(items_url, headers=admin_headers).json()
    assert [item["id"] for item in restored] == [items[1]["id"]]
    assert restore(client, admin_headers, row).status_code == 404


def test_task_restore_paused_and_parent_dependency(client, admin_headers):
    plan = create(client, admin_headers)
    run, _ = execute(client, admin_headers, plan)
    assert (
        client.delete(f"/api/v1/inspection-runs/{run['id']}", headers=admin_headers).status_code
        == 204
    )
    assert (
        client.delete(
            f"/api/v1/inspection-plans/{plan['id']}",
            headers=admin_headers,
            params={"expected_version": plan["version"]},
        ).status_code
        == 204
    )
    row = entry(client, admin_headers, "inspection-runs", run["id"])
    assert "巡检任务" in row["blocked_reason"]
    assert restore(client, admin_headers, row).status_code == 409
    task = entry(client, admin_headers, "inspection-plans", plan["id"])
    stale = {**task, "deleted_at": (datetime.now(UTC) - timedelta(days=1)).isoformat()}
    assert restore(client, admin_headers, stale).status_code == 409
    assert restore(client, admin_headers, task).status_code == 200
    detail = client.get(f"/api/v1/inspection-plans/{plan['id']}", headers=admin_headers).json()
    assert detail["enabled"] is False and detail["next_run_at"] is None
    assert detail["version"] > plan["version"]
    assert restore(client, admin_headers, row).status_code == 200
    with SessionLocal() as db:
        assert (
            len(
                db.scalars(
                    select(m.OperationsAuditLog).where(
                        m.OperationsAuditLog.action == "resource_restored"
                    )
                ).all()
            )
            == 2
        )


def test_active_run_cannot_be_deleted(client, admin_headers):
    plan = create(client, admin_headers)
    run = client.post(f"/api/v1/inspection-plans/{plan['id']}/runs", headers=admin_headers).json()
    assert (
        client.delete(f"/api/v1/inspection-runs/{run['id']}", headers=admin_headers).status_code
        == 409
    )


def test_assets_and_secret_metadata(client, admin_headers):
    with SessionLocal() as db:
        credential = m.SSHCredential(
            name="SSH archive",
            auth_type="password",
            encrypted_secret="private-ciphertext",
            deleted_at=datetime.now(UTC),
        )
        server = m.Server(
            name="恢复主机",
            host="127.0.0.1",
            node_exporter_target="node:9100",
            cadvisor_target="cadvisor:8080",
            monitoring_enabled=True,
        )
        db.add_all([credential, server])
        db.commit()
        server_id, credential_id = server.id, credential.id
        container = m.Container(
            server_id=server.id,
            name="恢复容器",
            docker_id="docker-test",
            labels={},
            image="test:local",
            status="running",
            last_seen_at=datetime.now(UTC),
        )
        db.add(container)
        db.commit()
        container_id = container.id
    assert client.delete(f"/api/v1/servers/{server_id}", headers=admin_headers).status_code == 204
    listing = client.get(ROOT, headers=admin_headers)
    assert "private-ciphertext" not in listing.text
    child = entry(client, admin_headers, "containers", container_id)
    assert restore(client, admin_headers, child).status_code == 409
    assert (
        restore(
            client, admin_headers, entry(client, admin_headers, "servers", server_id)
        ).status_code
        == 200
    )
    assert restore(client, admin_headers, child).status_code == 200
    assert (
        restore(
            client, admin_headers, entry(client, admin_headers, "ssh-credentials", credential_id)
        ).status_code
        == 200
    )
    with SessionLocal() as db:
        assert db.get(m.Server, server_id).monitoring_enabled is False
        assert db.get(m.SSHCredential, credential_id).encrypted_secret == "private-ciphertext"


def test_notification_restore_does_not_send_or_expose_other_owner(
    client, admin_headers, make_user, login
):
    owner = UUID(client.get("/api/v1/me", headers=admin_headers).json()["id"])
    other = make_user("other", "operator")
    with SessionLocal() as db:
        rows = [
            m.Notification(
                user_id=user_id,
                kind="test",
                resource_kind="inspection_run",
                resource_id=uuid4(),
                dedupe_key=str(uuid4()),
                title="待办",
                detail={},
                mail_status="queued",
                deleted_at=datetime.now(UTC),
            )
            for user_id in [owner, other]
        ]
        db.add_all(rows)
        db.commit()
        own_id, other_id = [r.id for r in rows]
    result = client.get(ROOT, headers=admin_headers, params={"kind": "notifications"})
    assert [r["id"] for r in result.json()] == [str(own_id)]
    assert (
        client.post(
            f"{ROOT}/notifications/{other_id}/restore",
            headers=admin_headers,
            json={"expected_deleted_at": datetime.now(UTC).isoformat()},
        ).status_code
        == 404
    )
    assert restore(client, admin_headers, result.json()[0]).status_code == 200
    owner_headers = {"Authorization": f"Bearer {login('other')['access_token']}"}
    personal = entry(client, owner_headers, "notifications", other_id)
    assert restore(client, owner_headers, personal).status_code == 200
    with SessionLocal() as db:
        row = db.get(m.Notification, own_id)
        assert row.read_at is not None and row.mail_status != "queued"


def test_account_restore_stays_disabled_and_sessions_revoked(
    client, admin_headers, make_user, login
):
    identifier = make_user("recover-user", "operator")
    token = login("recover-user")["access_token"]
    assert client.delete(f"/api/v1/users/{identifier}", headers=admin_headers).status_code == 204
    assert (
        restore(
            client, admin_headers, entry(client, admin_headers, "users", identifier)
        ).status_code
        == 200
    )
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    with SessionLocal() as db:
        assert db.get(m.User, identifier).is_active is False


def test_model_channel_restore_appends_disabled_and_changes_config(client, admin_headers):
    with SessionLocal() as db:
        config = db.get(m.AgentConfig, "inspection")
        version = config.version
        rows = [
            m.AgentChannel(
                module="inspection",
                name=str(i),
                enabled=True,
                position=0,
                base_url="https://example.invalid",
                model="test",
                encrypted_api_key="secret",
                deleted_at=datetime.now(UTC) if i == 0 else None,
            )
            for i in range(2)
        ]
        db.add_all(rows)
        db.commit()
        identifier = rows[0].id
    assert (
        restore(
            client, admin_headers, entry(client, admin_headers, "agent-channels", identifier)
        ).status_code
        == 200
    )
    with SessionLocal() as db:
        row = db.get(m.AgentChannel, identifier)
        assert row.enabled is False and row.position == 1
        assert db.get(m.AgentConfig, "inspection").version == version + 1


@pytest.mark.parametrize(
    "kind",
    [
        "applications",
        "llm-endpoints",
        "service-monitors",
        "service-databases",
        "prediction-tasks",
        "alert-rules",
        "knowledge",
        "skills",
        "inspection-data-sources",
        "inspection-http-connections",
        "service-objectives",
        "market-categories",
    ],
)
def test_configurable_resource_recovery_stays_disabled(client, admin_headers, kind):
    with SessionLocal() as db:
        app = m.Application(code="parent", name="关联服务")
        server = m.Server(
            name="关联主机",
            host="127.0.0.1",
            node_exporter_target="node:9100",
            cadvisor_target="cadvisor:8080",
        )
        db.add_all([app, server])
        db.flush()
        candidates = {
            "applications": lambda: m.Application(code="restored", name="恢复服务"),
            "llm-endpoints": lambda: m.LLMEndpoint(
                name="恢复LLM",
                base_url="https://example.invalid",
                model="test",
                encrypted_api_key="private-key",
                probe_enabled=True,
            ),
            "service-monitors": lambda: m.ServiceMonitor(
                application_id=app.id, name="恢复监测", target="https://example.invalid"
            ),
            "service-databases": lambda: m.ServiceDatabase(
                name="恢复数据库",
                engine="postgresql",
                host="localhost",
                port=5432,
                database="test",
                username="test",
                encrypted_config="private-config",
                environment="test",
                query_enabled=True,
            ),
            "prediction-tasks": lambda: m.PredictionTask(
                name="恢复预测", server_id=server.id, metric="cpu_percent", threshold=80
            ),
            "alert-rules": lambda: m.AlertRule(
                code="restore-rule", server_id=server.id, metric="cpu_percent", threshold=80
            ),
            "knowledge": lambda: m.KnowledgeDocument(
                title="恢复知识", content="test", index_status="ready"
            ),
            "skills": lambda: m.KnowledgeSkill(
                code="restore-skill", title="恢复方法", content="test"
            ),
            "inspection-data-sources": lambda: m.InspectionDataSource(
                name="恢复取数",
                environment="test",
                source_kind="api",
                source_config={"url": "https://example.invalid"},
            ),
            "inspection-http-connections": lambda: m.InspectionHTTPConnection(
                name="恢复连接", environment="test", base_url="https://example.invalid"
            ),
            "service-objectives": lambda: m.ServiceObjective(
                application_id=app.id, name="恢复目标", kind="business_success", target=99
            ),
            "market-categories": lambda: m.MarketCategory(name="恢复分类-" + str(uuid4())),
        }
        row = candidates[kind]()
        if hasattr(row, "enabled"):
            row.enabled = True
        row.deleted_at = datetime.now(UTC)
        db.add(row)
        db.commit()
        model, identifier = type(row), row.id
    recycled = entry(client, admin_headers, kind, identifier)
    result = restore(client, admin_headers, recycled)
    assert result.status_code == 200, result.text
    with SessionLocal() as db:
        row = db.get(model, identifier)
        assert row.deleted_at is None and row.updated_at >= row.created_at
        if hasattr(row, "enabled"):
            assert row.enabled is False
        if kind == "llm-endpoints":
            assert row.probe_enabled is False
        if kind == "service-databases":
            assert row.query_enabled is False
        if kind == "knowledge":
            assert row.index_status == "pending"


def test_restoring_inhibition_checks_cycles(client, admin_headers):
    with SessionLocal() as db:
        rules = [
            m.AlertRule(code=f"cycle-{i}", metric="cpu_percent", threshold=80) for i in range(3)
        ]
        db.add_all(rules)
        db.flush()
        edges = [
            m.AlertInhibition(
                parent_rule_id=rules[a].id,
                child_rule_id=rules[b].id,
                created_by=UUID(client.get("/api/v1/me", headers=admin_headers).json()["id"]),
            )
            for a, b in [(0, 1), (1, 2), (2, 0)]
        ]
        edges[-1].deleted_at = datetime.now(UTC)
        db.add_all(edges)
        db.commit()
        identifier = edges[-1].id
    result = restore(
        client, admin_headers, entry(client, admin_headers, "alert-inhibitions", identifier)
    )
    assert result.status_code == 409
    assert "循环" in result.json()["detail"]
