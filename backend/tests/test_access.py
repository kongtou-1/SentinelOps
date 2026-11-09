from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from alembic import command
from fastapi import HTTPException
from sqlalchemy import func, inspect, literal, select, text
from sqlalchemy.dialects.postgresql import JSONB

from app.db.session import SessionLocal, engine
from app.models import (
    AccessGrant,
    AccessRequest,
    AlertEvent,
    Application,
    Container,
    Incident,
    IncidentAlert,
    Server,
    ServiceMonitor,
)
from app.repositories.access import active_user
from app.services.access_control import Authorization, bind, record_allowed, resource_guard


@pytest.fixture
def assets():
    with SessionLocal() as db:
        app = Application(code="demo", name="应用A")
        other = Application(code="other", name="应用B")
        db.add_all([app, other])
        db.flush()
        server = Server(
            name="主机A",
            host="127.0.0.1",
            node_exporter_target="localhost:9100",
            cadvisor_target="localhost:8080",
        )
        db.add(server)
        db.flush()
        container = Container(
            server_id=server.id,
            application_id=app.id,
            docker_id="a" * 64,
            name="容器A",
            image="demo",
            status="running",
            labels={},
            last_seen_at=datetime.now(UTC),
        )
        db.add(container)
        db.commit()
        return {
            "application": str(app.id),
            "other": str(other.id),
            "server": str(server.id),
            "container": str(container.id),
        }


def auth(login, name):
    return {"Authorization": "Bearer " + login(name)["access_token"]}


def apply(client, headers, kind, identifier, bundles=None, days=7, key=None):
    return client.post(
        "/api/v1/access-requests",
        headers=headers,
        json={
            "idempotency_key": str(key or uuid4()),
            "reason": "负责该应用的故障排查",
            "duration_days": days,
            "items": [
                {
                    "bundle_ids": bundles or ["diagnosis.run"],
                    "resource_kind": kind,
                    "resource_id": identifier,
                    "include_descendants": True,
                }
            ],
        },
    )


def restrict(client, admin_headers, kind, identifier):
    response = client.put(
        f"/api/v1/access-policies/{kind}/{identifier}",
        headers=admin_headers,
        json={"restricted": True, "expected_version": 0},
    )
    assert response.status_code == 200, response.text


def approve(client, headers, request_id, **body):
    result = client.post(
        f"/api/v1/access-requests/{request_id}/approve", headers=headers, json=body
    )
    assert result.status_code == 200, result.text
    return result


def test_group_request_scoped_grant_and_revocation(client, admin_headers, make_user, login, assets):
    identifier = make_user("viewer", "viewer")
    headers = auth(login, "viewer")
    assert "analyses:run" not in client.get("/api/v1/me", headers=headers).json()["permissions"]
    restrict(client, admin_headers, "application", assets["application"])
    assert client.get(f"/api/v1/containers/{assets['container']}", headers=headers).status_code in (
        403,
        404,
    )
    response = apply(client, headers, "application", assets["application"])
    assert response.status_code == 201, response.text
    approve(client, admin_headers, response.json()["id"])
    assert "analyses:run" in client.get("/api/v1/me", headers=headers).json()["permissions"]
    assert (
        client.get(f"/api/v1/containers/{assets['container']}", headers=headers).status_code == 200
    )
    with SessionLocal() as db:
        user = active_user(db, identifier)
        current = Authorization(db, user)
        assert current.allows("analyses:run", "container", assets["container"])
        assert not current.allows("analyses:run", "application", assets["other"])
        grant = db.scalar(select(AccessGrant).where(AccessGrant.user_id == identifier))
        grant_id = grant.id
    assert (
        client.post(f"/api/v1/access-grants/{grant_id}/revoke", headers=admin_headers).status_code
        == 204
    )
    assert "analyses:run" not in client.get("/api/v1/me", headers=headers).json()["permissions"]
    assert client.get(f"/api/v1/containers/{assets['container']}", headers=headers).status_code in (
        403,
        404,
    )


def test_policy_filters_lists_totals_and_dashboard(client, admin_headers, make_user, login, assets):
    make_user("viewer", "viewer")
    headers = auth(login, "viewer")
    restrict(client, admin_headers, "server", assets["server"])
    for path in ("/servers", "/containers"):
        result = client.get("/api/v1" + path, headers=headers)
        assert result.status_code == 200, result.text
        assert result.json() == []
        assert result.headers["X-Total-Count"] == "0"
    dashboard = client.get("/api/v1/dashboard/summary", headers=headers)
    assert dashboard.status_code == 200, dashboard.text
    assert dashboard.json()["servers_total"] == 0
    assert dashboard.json()["containers_total"] == 0
    restrict(client, admin_headers, "dashboard", "overview")
    assert client.get("/api/v1/dashboard/summary", headers=headers).status_code == 403


def user_policy(client, headers, user_id, kind, identifier, restricted=True, version=0):
    return client.put(
        f"/api/v1/access-policies/{kind}/{identifier}",
        params={"user_id": str(user_id)},
        headers=headers,
        json={"restricted": restricted, "expected_version": version},
    )


def test_user_denial_overrides_grants_and_defaults_without_affecting_others(
    client, admin_headers, make_user, login, assets
):
    user_id = make_user("viewer", "viewer")
    make_user("other-viewer", "viewer")
    headers, other_headers = auth(login, "viewer"), auth(login, "other-viewer")
    application, container = assets["application"], assets["container"]
    request = apply(client, headers, "application", application).json()
    approve(client, admin_headers, request["id"])
    guard = resource_guard(user_id, "analyses:run", "container", container)
    guard()
    before = client.get("/api/v1/me", headers=headers).json()["permissions_version"]
    response = user_policy(client, admin_headers, user_id, "application", application)
    assert response.status_code == 200, response.text
    assert response.json()["restricted"] and not response.json()["allowed"]
    with pytest.raises(HTTPException, match="权限已失效"):
        guard()
    for path in (f"/applications/{application}", f"/containers/{container}"):
        assert client.get("/api/v1" + path, headers=headers).status_code in (403, 404)
        assert client.get("/api/v1" + path, headers=other_headers).status_code == 200
        assert client.get("/api/v1" + path, headers=admin_headers).status_code == 200
    containers = client.get("/api/v1/containers", headers=headers)
    assert containers.json() == [] and containers.headers["X-Total-Count"] == "0"
    assert client.get("/api/v1/dashboard/summary", headers=headers).json()["containers_total"] == 0
    assert client.get("/api/v1/me", headers=headers).json()["permissions_version"] != before
    with SessionLocal() as db:
        current = Authorization(db, active_user(db, user_id))
        assert not current.allows("analyses:run", "container", container)
        assert not record_allowed(db, current.user, Container, UUID(container))
        frozen = literal({"application_id": application}, type_=JSONB)
        assert not db.scalar(select(current.json_clause(frozen, "sources:read")))
    restored = user_policy(client, admin_headers, user_id, "application", application, False, 1)
    assert restored.status_code == 200 and restored.json()["allowed"]
    assert client.get(f"/api/v1/containers/{container}", headers=headers).status_code == 200
    guard()


def test_policy_migration_retains_global_records_and_utc_update_behavior(
    client, admin_headers, assets, migrated_database
):
    identifier = assets["server"]
    restrict(client, admin_headers, "server", identifier)
    command.downgrade(migrated_database, "0038_platform_mail")
    command.upgrade(migrated_database, "head")
    command.check(migrated_database)
    response = client.get(f"/api/v1/access-policies/server/{identifier}", headers=admin_headers)
    assert response.status_code == 200, response.text
    assert response.json()["restricted"] and response.json()["version"] == 1
    assert response.json()["user_id"] is None
    columns = {column["name"]: column for column in inspect(engine).get_columns("access_policies")}
    for name in ("created_at", "updated_at", "deleted_at"):
        assert columns[name]["type"].timezone
        assert columns[name]["nullable"] == (name == "deleted_at")
    assert columns["user_id"]["nullable"]
    with engine.begin() as connection:
        before = connection.execute(
            text("SELECT created_at, updated_at FROM access_policies")
        ).one()
    updated = client.put(
        f"/api/v1/access-policies/server/{identifier}",
        headers=admin_headers,
        json={"restricted": False, "expected_version": 1},
    )
    assert updated.status_code == 200, updated.text
    with engine.begin() as connection:
        after = connection.execute(text("SELECT created_at, updated_at FROM access_policies")).one()
    assert after.created_at == before.created_at and after.updated_at > before.updated_at


def test_user_policy_is_independent_from_global_policy_and_restores_original_permissions(
    client, admin_headers, make_user, login, assets
):
    user_id = make_user("viewer", "viewer")
    headers = auth(login, "viewer")
    identifier = assets["application"]
    restrict(client, admin_headers, "application", identifier)
    denied = user_policy(client, admin_headers, user_id, "application", identifier)
    assert denied.status_code == 200, denied.text
    conflict = user_policy(client, admin_headers, user_id, "application", identifier, False)
    assert conflict.status_code == 409
    restored = user_policy(client, admin_headers, user_id, "application", identifier, False, 1)
    assert restored.status_code == 200 and not restored.json()["allowed"]
    global_policy = client.get(
        f"/api/v1/access-policies/application/{identifier}", headers=admin_headers
    ).json()
    assert global_policy["restricted"] and global_policy["version"] == 1
    assert global_policy["user_id"] is None
    assert client.get(f"/api/v1/applications/{identifier}", headers=headers).status_code in (
        403,
        404,
    )
    # Releasing a denial never grants a business action the role did not have.
    with SessionLocal() as db:
        assert not Authorization(db, active_user(db, user_id)).allows(
            "analyses:run", "application", identifier
        )


def test_user_policy_protects_admin_and_rejects_nonadmin_management(
    client, admin_headers, make_user, login, assets
):
    user_id = make_user("viewer", "viewer")
    administrator = make_user("other-admin", "admin")
    disabled = make_user("disabled", "viewer", active=False)
    for subject, expected in ((administrator, 422), (disabled, 404), (uuid4(), 404)):
        assert (
            user_policy(client, admin_headers, subject, "server", assets["server"]).status_code
            == expected
        )
    headers = auth(login, "viewer")
    assert user_policy(client, headers, user_id, "server", assets["server"]).status_code == 403
    assert (
        client.get(
            f"/api/v1/access-policies/server/{assets['server']}",
            params={"user_id": str(user_id)},
            headers=headers,
        ).status_code
        == 403
    )
    assert user_policy(client, admin_headers, user_id, "global", "*").status_code == 422


def test_user_denial_inherits_to_children_and_remains_after_child_policy_is_cleared(
    client, admin_headers, make_user, login, assets
):
    user_id = make_user("viewer", "viewer")
    headers = auth(login, "viewer")
    assert (
        user_policy(client, admin_headers, user_id, "server", assets["server"]).status_code == 200
    )
    child = user_policy(client, admin_headers, user_id, "container", assets["container"], False)
    assert child.status_code == 200 and not child.json()["allowed"]
    assert client.get("/api/v1/servers", headers=headers).json() == []
    assert client.get("/api/v1/containers", headers=headers).json() == []
    assert (
        client.get(f"/api/v1/applications/{assets['application']}", headers=headers).status_code
        == 200
    )


def test_user_dashboard_denial_updates_navigation_and_blocks_api(
    client, admin_headers, make_user, login
):
    user_id = make_user("viewer", "viewer")
    make_user("other-viewer", "viewer")
    headers = auth(login, "viewer")
    for board, path in (("overview", "/dashboard/summary"), ("predictions", "/predictions")):
        response = user_policy(client, admin_headers, user_id, "dashboard", board)
        assert response.status_code == 200, response.text
        me = client.get("/api/v1/me", headers=headers).json()
        assert me["dashboard_access"][board] is False
        assert client.get("/api/v1" + path, headers=headers).status_code == 403
        assert client.get("/api/v1" + path, headers=auth(login, "other-viewer")).status_code == 200


def test_child_user_denial_cannot_be_hidden_by_parent_grant(
    client, admin_headers, make_user, login, assets
):
    user_id = make_user("viewer", "viewer")
    headers = auth(login, "viewer")
    requested = apply(client, headers, "application", assets["application"]).json()
    approve(client, admin_headers, requested["id"])
    assert (
        user_policy(client, admin_headers, user_id, "container", assets["container"]).status_code
        == 200
    )
    response = client.post(
        "/api/v1/access/check",
        headers=headers,
        json={
            "items": [
                {
                    "permission": "analyses:run",
                    "resource_kind": "application",
                    "resource_id": assets["application"],
                    "include_descendants": True,
                }
            ]
        },
    )
    assert response.status_code == 200 and not response.json()[0]["allowed"]
    with SessionLocal() as db:
        monitor = ServiceMonitor(
            application_id=UUID(assets["application"]),
            container_id=UUID(assets["container"]),
            name="容器检查",
            target="http://example.invalid/health",
        )
        db.add(monitor)
        db.commit()
        monitor_id = str(monitor.id)
    response = client.get(
        f"/api/v1/access-policies/service_monitor/{monitor_id}",
        params={"user_id": str(user_id)},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    assert not response.json()["allowed"]


@pytest.mark.parametrize("days", [1, 7, 30, None])
def test_durations_and_duplicate_approval(client, admin_headers, make_user, login, assets, days):
    make_user("viewer", "viewer")
    response = apply(client, auth(login, "viewer"), "application", assets["application"], days=days)
    assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    approve(client, admin_headers, identifier)
    again = client.post(
        f"/api/v1/access-requests/{identifier}/approve", headers=admin_headers, json={}
    )
    assert again.status_code == 409
    with SessionLocal() as db:
        row = db.scalar(select(AccessGrant))
        assert (row.expires_at is None) == (days is None)
        assert db.scalar(select(func.count()).select_from(AccessGrant)) == len(
            response.json()["items"][0]["permissions"]
        )


def test_role_change_invalidates_grants_and_pending(
    client, admin_headers, make_user, login, assets
):
    user_id = make_user("viewer", "viewer")
    headers = auth(login, "viewer")
    first = apply(client, headers, "application", assets["application"]).json()
    approve(client, admin_headers, first["id"])
    pending = apply(client, headers, "application", assets["other"]).json()
    result = client.patch(
        f"/api/v1/users/{user_id}", headers=admin_headers, json={"role": "operator"}
    )
    assert result.status_code == 200, result.text
    with SessionLocal() as db:
        assert all(g.revoked_at for g in db.scalars(select(AccessGrant)))
        assert db.get(AccessRequest, UUID(pending["id"])).status == "invalidated"


def test_reject_withdraw_ownership_self_approval_and_expansion(
    client, admin_headers, make_user, login, assets
):
    make_user("viewer", "viewer")
    make_user("other", "viewer")
    headers = auth(login, "viewer")
    row = apply(client, headers, "application", assets["application"]).json()
    assert (
        client.get(f"/api/v1/access-requests/{row['id']}", headers=auth(login, "other")).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/access-requests/{row['id']}/approve", headers=headers, json={}
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/access-requests/{row['id']}/approve",
            headers=admin_headers,
            json={"duration_days": 30},
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/v1/access-requests/{row['id']}/approve",
            headers=admin_headers,
            json={"items": []},
        ).status_code
        == 422
    )
    assert (
        client.post(f"/api/v1/access-requests/{row['id']}/withdraw", headers=headers).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/access-requests/{row['id']}/approve", headers=admin_headers, json={}
        ).status_code
        == 409
    )
    second = apply(client, headers, "application", assets["application"]).json()
    assert (
        client.post(
            f"/api/v1/access-requests/{second['id']}/reject",
            headers=admin_headers,
            json={"comment": "职责不符"},
        ).status_code
        == 200
    )
    assert apply(client, admin_headers, "application", assets["application"]).status_code == 409
    # An administrator can still have a historical request; self-approval stays forbidden.
    with SessionLocal() as db:
        own = AccessRequest(
            user_id=UUID(client.get("/api/v1/me", headers=admin_headers).json()["id"]),
            idempotency_key=uuid4(),
            items=second["items"],
            reason="历史申请",
            duration_days=7,
        )
        db.add(own)
        db.commit()
        own_id = own.id
    assert (
        client.post(
            f"/api/v1/access-requests/{own_id}/approve", headers=admin_headers, json={}
        ).status_code
        == 403
    )


def test_owned_default_and_approved_permissions_cannot_be_requested_again(
    client, admin_headers, make_user, login, assets
):
    make_user("viewer", "viewer")
    headers = auth(login, "viewer")
    owned = apply(client, headers, "application", assets["application"], ["applications.view"])
    assert owned.status_code == 409, owned.text
    key = uuid4()
    result = apply(client, headers, "application", assets["application"], key=key)
    assert result.status_code == 201, result.text
    approve(client, admin_headers, result.json()["id"])
    assert apply(client, headers, "application", assets["application"]).status_code == 409
    # Retrying the original write returns its existing record even after approval.
    repeated = apply(client, headers, "application", assets["application"], key=key)
    assert repeated.status_code == 201 and repeated.json()["id"] == result.json()["id"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AccessRequest)) == 1


def test_request_checks_include_restricted_children_and_expired_grants(
    client, admin_headers, make_user, login, assets
):
    make_user("viewer", "viewer")
    headers = auth(login, "viewer")
    restrict(client, admin_headers, "container", assets["container"])
    check = {
        "permission": "containers:read",
        "resource_kind": "application",
        "resource_id": assets["application"],
    }
    response = client.post(
        "/api/v1/access/check",
        headers=headers,
        json={"items": [check, {**check, "include_descendants": True}]},
    )
    assert response.status_code == 200, response.text
    assert [item["allowed"] for item in response.json()] == [True, False]
    result = apply(client, headers, "application", assets["application"], ["applications.view"])
    assert result.status_code == 201, result.text
    approve(client, admin_headers, result.json()["id"])
    assert (
        apply(
            client, headers, "application", assets["application"], ["applications.view"]
        ).status_code
        == 409
    )
    with SessionLocal() as db:
        for grant in db.scalars(select(AccessGrant)):
            grant.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert (
        apply(
            client, headers, "application", assets["application"], ["applications.view"]
        ).status_code
        == 201
    )


def test_exact_grants_do_not_hide_missing_descendant_scope(
    client, admin_headers, make_user, login, assets
):
    make_user("viewer", "viewer")
    headers = auth(login, "viewer")
    restrict(client, admin_headers, "application", assets["application"])
    result = apply(client, headers, "application", assets["application"], ["applications.view"])
    approve(client, admin_headers, result.json()["id"])
    with SessionLocal() as db:
        for grant in db.scalars(select(AccessGrant)):
            grant.include_descendants = False
        db.commit()
    response = apply(client, headers, "application", assets["application"], ["applications.view"])
    assert response.status_code == 201, response.text


def test_inheritance_changes_expiry_and_no_reverse_scope(
    client, admin_headers, make_user, login, assets
):
    user_id = make_user("viewer", "viewer")
    row = apply(client, auth(login, "viewer"), "application", assets["application"]).json()
    approve(client, admin_headers, row["id"])
    with SessionLocal() as db:
        user = active_user(db, user_id)
        assert Authorization(db, user).allows("analyses:run", "container", assets["container"])
        assert not Authorization(db, user).allows("analyses:run", "server", assets["server"])
        container = db.get(Container, UUID(assets["container"]))
        container.application_id = UUID(assets["other"])
        db.commit()
        assert not Authorization(db, user).allows("analyses:run", "container", assets["container"])
        container.application_id = UUID(assets["application"])
        db.commit()
        for grant in db.scalars(select(AccessGrant)):
            grant.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
        assert not Authorization(db, user).has("analyses:run")


def test_cross_resource_incident_is_hidden_whole(client, admin_headers, make_user, login, assets):
    user_id = make_user("viewer", "viewer")
    with SessionLocal() as db:
        server = Server(
            name="主机B", host="localhost", node_exporter_target="b:9100", cadvisor_target="b:8080"
        )
        db.add(server)
        db.flush()
        incident = Incident(
            title="跨资源事故",
            severity="critical",
            status="open",
            started_at=datetime.now(UTC),
            last_alert_started_at=datetime.now(UTC),
        )
        db.add(incident)
        db.flush()
        for index, server_id in enumerate((UUID(assets["server"]), server.id)):
            alert = AlertEvent(
                fingerprint=str(index),
                severity="critical",
                status="firing",
                server_id=server_id,
                starts_at=datetime.now(UTC),
                labels={},
                annotations={},
            )
            db.add(alert)
            db.flush()
            db.add(IncidentAlert(incident_id=incident.id, alert_event_id=alert.id))
        db.commit()
        incident_id, server_id = incident.id, str(server.id)
    restrict(client, admin_headers, "server", server_id)
    headers = auth(login, "viewer")
    result = client.get("/api/v1/incidents", headers=headers)
    assert result.status_code == 200, result.text
    assert result.json() == []
    assert result.headers["X-Total-Count"] == "0"
    assert client.get(f"/api/v1/incidents/{incident_id}", headers=headers).status_code in (403, 404)
    with SessionLocal() as db:
        assert not record_allowed(
            db, active_user(db, user_id), Incident, incident_id, "analyses:run"
        )


def test_role_defaults_version_checks_and_admin_boundary(client, admin_headers, make_user, login):
    make_user("viewer", "viewer")
    headers = auth(login, "viewer")
    result = client.put(
        "/api/v1/roles/viewer/permissions",
        headers=admin_headers,
        json={"expected_version": 1, "permissions": ["market:read"]},
    )
    assert result.status_code == 200, result.text
    assert client.get("/api/v1/servers", headers=headers).status_code == 403
    assert client.get("/api/v1/me", headers=headers).json()["permissions"] == [
        "market:read",
        "profile:read",
    ]
    assert (
        client.put(
            "/api/v1/roles/viewer/permissions",
            headers=admin_headers,
            json={"expected_version": 1, "permissions": []},
        ).status_code
        == 409
    )
    assert (
        client.put(
            "/api/v1/roles/viewer/permissions",
            headers=admin_headers,
            json={"expected_version": 2, "permissions": ["approvals:decide"]},
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/v1/roles/admin/permissions",
            headers=admin_headers,
            json={"expected_version": 1, "permissions": []},
        ).status_code
        == 422
    )


def test_frozen_report_resources_are_checked_before_counts(
    client, admin_headers, make_user, assets
):
    from app.models import PostmortemReport

    user_id = make_user("viewer", "viewer")
    with SessionLocal() as db:
        incident = Incident(
            title="历史事故",
            severity="warning",
            status="open",
            started_at=datetime.now(UTC),
            last_alert_started_at=datetime.now(UTC),
        )
        db.add(incident)
        db.flush()
        report = PostmortemReport(
            incident_id=incident.id,
            requested_by=user_id,
            status="draft",
            snapshot={"records": [{"data": {"server_id": assets["server"]}}]},
            snapshot_hash="a" * 64,
            config={},
        )
        db.add(report)
        db.commit()
    restrict(client, admin_headers, "server", assets["server"])
    with SessionLocal() as db:
        bind(db, active_user(db, user_id))
        assert db.scalars(select(PostmortemReport)).all() == []
        assert db.scalar(select(func.count()).select_from(PostmortemReport)) == 0


def test_application_diagnosis_worker_revocation_and_host_isolation(
    client, admin_headers, make_user, login, assets
):
    from app.models import AgentAnalysis, ServiceMonitor
    from app.services.diagnosis.analyses import requester_valid, scope

    user_id = make_user("viewer", "viewer")
    with SessionLocal() as db:
        monitor = ServiceMonitor(
            application_id=UUID(assets["application"]),
            container_id=UUID(assets["container"]),
            name="业务健康",
            protocol="http",
            target="https://example.test/health",
        )
        db.add(monitor)
        incident = Incident(
            title="应用事故",
            severity="warning",
            status="open",
            started_at=datetime.now(UTC),
            last_alert_started_at=datetime.now(UTC),
        )
        db.add(incident)
        db.flush()
        alert = AlertEvent(
            fingerprint="monitor",
            service_monitor_id=monitor.id,
            severity="warning",
            status="firing",
            starts_at=datetime.now(UTC),
            labels={},
            annotations={},
        )
        db.add(alert)
        db.flush()
        db.add(IncidentAlert(incident_id=incident.id, alert_event_id=alert.id))
        analysis = AgentAnalysis(
            incident_id=incident.id,
            requested_by=user_id,
            input_hash="a" * 64,
            config={},
            status="queued",
        )
        db.add(analysis)
        db.commit()
        analysis_id = analysis.id
    request = apply(client, auth(login, "viewer"), "application", assets["application"]).json()
    approve(client, admin_headers, request["id"])
    with SessionLocal() as db:
        row = db.get(AgentAnalysis, analysis_id)
        assert requester_valid(db, row)
        servers, containers, _ = scope(db, row.incident_id)
        assert servers == []
        assert [str(c.id) for c in containers] == [assets["container"]]
        repository_grant = db.scalar(
            select(AccessGrant).where(AccessGrant.request_id == UUID(request["id"]))
        )
        grant_id = repository_grant.id
    client.post(f"/api/v1/access-grants/{grant_id}/revoke", headers=admin_headers)
    with SessionLocal() as db:
        assert not requester_valid(db, db.get(AgentAnalysis, analysis_id))


def test_batch_idempotency_shortening_and_atomic_approval(
    client, admin_headers, make_user, login, assets
):
    user_id = make_user("viewer", "viewer")
    headers = auth(login, "viewer")
    body = {
        "idempotency_key": str(uuid4()),
        "reason": "批量应用交接",
        "duration_days": 7,
        "items": [
            {
                "bundle_ids": ["resources.operate", "diagnosis.run"],
                "resource_kind": "application",
                "resource_id": assets[key],
            }
            for key in ("application", "other")
        ],
    }
    first = client.post("/api/v1/access-requests", headers=headers, json=body)
    assert first.status_code == 201, first.text
    assert (
        client.post("/api/v1/access-requests", headers=headers, json=body).json()["id"]
        == first.json()["id"]
    )
    changed = {**body, "reason": "不同内容"}
    assert client.post("/api/v1/access-requests", headers=headers, json=changed).status_code == 409
    approve(client, admin_headers, first.json()["id"], duration_days=1)
    with SessionLocal() as db:
        rows = db.scalars(select(AccessGrant).where(AccessGrant.user_id == user_id)).all()
        assert {g.resource_id for g in rows} == {assets["application"], assets["other"]}
        assert all(
            datetime.now(UTC) < g.expires_at <= datetime.now(UTC) + timedelta(days=1) for g in rows
        )
    next_body = {
        **body,
        "idempotency_key": str(uuid4()),
        "reason": "资源删除后不能部分批准",
        "items": [{**item, "bundle_ids": ["reports.run"]} for item in body["items"]],
    }
    next_request = client.post("/api/v1/access-requests", headers=headers, json=next_body).json()
    with SessionLocal() as db:
        db.get(Application, UUID(assets["other"])).deleted_at = datetime.now(UTC)
        db.commit()
    result = client.post(
        f"/api/v1/access-requests/{next_request['id']}/approve", headers=admin_headers, json={}
    )
    assert result.status_code == 404
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(AccessGrant)
                .where(AccessGrant.request_id == UUID(next_request["id"]))
            )
            == 0
        )
        assert db.get(AccessRequest, UUID(next_request["id"])).status == "pending"


def test_application_grant_does_not_unlock_host_or_shared_llm(
    client, admin_headers, make_user, login, assets
):
    from app.models import LLMEndpoint, LLMServiceBinding

    user_id = make_user("viewer", "viewer")
    with SessionLocal() as db:
        endpoint = LLMEndpoint(
            name="共享模型",
            base_url="https://example.test",
            model="test",
            encrypted_api_key="test-only",
        )
        db.add(endpoint)
        db.flush()
        db.add(
            LLMServiceBinding(
                application_id=UUID(assets["application"]), llm_endpoint_id=endpoint.id
            )
        )
        db.commit()
        endpoint_id = str(endpoint.id)
    restrict(client, admin_headers, "server", assets["server"])
    restrict(client, admin_headers, "llm_endpoint", endpoint_id)
    headers = auth(login, "viewer")
    request = apply(
        client, headers, "application", assets["application"], ["applications.view"]
    ).json()
    approve(client, admin_headers, request["id"])
    containers = client.get("/api/v1/containers", headers=headers)
    assert containers.status_code == 200, containers.text
    assert [r["id"] for r in containers.json()] == [assets["container"]]
    assert containers.headers["X-Total-Count"] == "1"
    with SessionLocal() as db:
        user = active_user(db, user_id)
        assert not Authorization(db, user).allows("servers:read", "server", assets["server"])
        assert not Authorization(db, user).allows("llm:read", "llm_endpoint", endpoint_id)
        bind(db, user)
        assert db.scalars(select(LLMServiceBinding)).all() == []


def test_directory_discloses_only_application_names(
    client, admin_headers, make_user, login, assets
):
    make_user("viewer", "viewer")
    restrict(client, admin_headers, "application", assets["application"])
    result = client.get("/api/v1/access-targets?kind=application", headers=auth(login, "viewer"))
    assert result.status_code == 200
    assert result.headers["X-Total-Count"] == "2"
    assert all(set(row) == {"id", "name", "kind"} for row in result.json())


def test_container_event_requires_its_own_resource_scope(
    client, admin_headers, make_user, login, assets
):
    make_user("viewer", "viewer")
    with SessionLocal() as db:
        row = AlertEvent(
            fingerprint="container",
            server_id=UUID(assets["server"]),
            severity="warning",
            status="firing",
            starts_at=datetime.now(UTC),
            labels={"container_id": assets["container"]},
            annotations={},
        )
        db.add(row)
        db.commit()
        event_id = str(row.id)
    restrict(client, admin_headers, "container", assets["container"])
    headers = auth(login, "viewer")
    assert client.get("/api/v1/alerts", headers=headers).json() == []
    request = apply(client, headers, "container", assets["container"], ["alerts.view"]).json()
    approve(client, admin_headers, request["id"])
    assert [row["id"] for row in client.get("/api/v1/alerts", headers=headers).json()] == [event_id]


def test_approved_business_edit_stays_on_the_selected_container(
    client, admin_headers, make_user, login, assets
):
    make_user("viewer", "viewer")
    headers = auth(login, "viewer")
    body = {"service_name": "业务容器", "purpose": "负责该容器的业务资料", "depends_on": []}
    path = f"/api/v1/containers/{assets['container']}/service"
    assert client.put(path, headers=headers, json=body).status_code == 403
    request = apply(client, headers, "container", assets["container"], ["resources.operate"]).json()
    approve(client, admin_headers, request["id"])
    result = client.put(path, headers=headers, json=body)
    assert result.status_code == 200, result.text
    assert result.json()["purpose"] == body["purpose"]
    assert (
        client.post(
            f"/api/v1/servers/{assets['server']}/sync-containers", headers=headers
        ).status_code
        == 403
    )


def test_extra_diagnosis_permission_does_not_open_other_private_conversations(
    client, admin_headers, make_user, login, assets
):
    from app.models import AnalysisConversation, ServiceMonitor

    user_id = make_user("viewer", "viewer")
    with SessionLocal() as db:
        monitor = ServiceMonitor(
            application_id=UUID(assets["other"]),
            name="其他应用",
            protocol="http",
            target="https://example.test",
        )
        incident = Incident(
            title="其他应用事故",
            severity="warning",
            status="open",
            started_at=datetime.now(UTC),
            last_alert_started_at=datetime.now(UTC),
        )
        db.add_all([monitor, incident])
        db.flush()
        alert = AlertEvent(
            fingerprint="other-private",
            service_monitor_id=monitor.id,
            severity="warning",
            status="firing",
            starts_at=datetime.now(UTC),
            labels={},
            annotations={},
        )
        db.add(alert)
        db.flush()
        db.add(IncidentAlert(incident_id=incident.id, alert_event_id=alert.id))
        conversation = AnalysisConversation(
            incident_id=incident.id, owner_id=user_id, title="以前的私有诊断"
        )
        db.add(conversation)
        db.commit()
        incident_id, conversation_id = incident.id, conversation.id
    headers = auth(login, "viewer")
    request = apply(client, headers, "application", assets["application"]).json()
    approve(client, admin_headers, request["id"])
    result = client.get(f"/api/v1/incidents/{incident_id}/analysis-conversations", headers=headers)
    assert result.status_code == 200, result.text
    assert result.json() == []
    assert client.get(
        f"/api/v1/analysis-conversations/{conversation_id}", headers=headers
    ).status_code in (403, 404)


def test_database_exception_grant_does_not_require_parent_application_access(
    client, admin_headers, make_user, login, assets
):
    from app.models import ServiceDatabase
    from app.services.service_databases import active

    user_id = make_user("viewer", "viewer")
    with SessionLocal() as db:
        connection = ServiceDatabase(
            application_id=UUID(assets["application"]),
            name="只读连接",
            engine="postgresql",
            host="localhost",
            port=5432,
            database="fixture",
            username="reader",
            tls_mode="verify-full",
            encrypted_config="fixture-only",
            environment="test",
        )
        db.add(connection)
        db.commit()
        connection_id = connection.id
    restrict(client, admin_headers, "application", assets["application"])
    request = apply(
        client, auth(login, "viewer"), "service_database", str(connection_id), ["applications.view"]
    ).json()
    approve(client, admin_headers, request["id"])
    with SessionLocal() as db:
        user = active_user(db, user_id)
        bind(db, user)
        assert active(db, connection_id).id == connection_id
        assert (
            db.scalar(select(Application).where(Application.id == UUID(assets["application"])))
            is None
        )


def test_queued_incident_email_rechecks_resource_access(
    client, admin_headers, make_user, login, assets
):
    from app.models import Notification, NotificationPolicy, User
    from app.services.operations.notifications import delivery_authorized

    user_id = make_user("viewer", "viewer")
    with SessionLocal() as db:
        administrator = db.scalar(select(User).where(User.username == "admin"))
        db.get(User, user_id).notification_email = "viewer@example.test"
        db.add(
            NotificationPolicy(
                id=1, managed=True, email_enabled=True, version=1, configured_by=administrator.id
            )
        )
        incident = Incident(
            title="受限事故",
            severity="warning",
            status="open",
            started_at=datetime.now(UTC),
            last_alert_started_at=datetime.now(UTC),
        )
        alert = AlertEvent(
            fingerprint="mail-scope",
            server_id=UUID(assets["server"]),
            severity="warning",
            status="firing",
            starts_at=datetime.now(UTC),
            labels={},
            annotations={},
        )
        db.add_all([incident, alert])
        db.flush()
        db.add(IncidentAlert(incident_id=incident.id, alert_event_id=alert.id))
        notification = Notification(
            user_id=user_id,
            incident_id=incident.id,
            kind="incident_opened",
            resource_kind="incident",
            resource_id=incident.id,
            dedupe_key="mail-scope",
            title="受限事故",
            detail={},
            email="viewer@example.test",
            policy_version=1,
            mail_status="queued",
        )
        db.add(notification)
        db.commit()
        notification_id = notification.id
    restrict(client, admin_headers, "server", assets["server"])
    with SessionLocal() as db:
        assert not delivery_authorized(db, db.get(Notification, notification_id))
    request = apply(
        client, auth(login, "viewer"), "server", assets["server"], ["alerts.view"]
    ).json()
    approve(client, admin_headers, request["id"])
    with SessionLocal() as db:
        assert delivery_authorized(db, db.get(Notification, notification_id))
