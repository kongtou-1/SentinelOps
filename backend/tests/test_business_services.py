from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import func, select

from app.core.secrets import cipher
from app.db.session import SessionLocal
from app.models import (
    Application,
    ApplicationDeployment,
    ApplicationRelease,
    Container,
    Server,
    SourceAuditLog,
)


@pytest.fixture
def inventory(monkeypatch):
    monkeypatch.setattr("app.services.resources.overview.fetch", lambda *_args: None)
    with SessionLocal() as db:
        servers = [
            Server(
                name=f"business-host-{i}",
                host="127.0.0.1",
                node_exporter_target="node:9100",
                cadvisor_target="cadvisor:8080",
            )
            for i in range(2)
        ]
        db.add_all(servers)
        db.flush()
        containers = [
            Container(
                server_id=servers[i // 2].id,
                docker_id="/docker/" + str(i + 1) * 64,
                name=f"component-{i}",
                image="test:1",
                status="running",
                labels={},
                purpose="原有容器备注",
                last_seen_at=datetime.now(UTC),
            )
            for i in range(4)
        ]
        db.add_all(containers)
        db.commit()
        return [str(s.id) for s in servers], [str(c.id) for c in containers]


def create(client, headers, server_id, ids, code="qa-service"):
    return client.post(
        f"/api/v1/servers/{server_id}/business-services",
        headers=headers,
        json={
            "code": code,
            "name": "知识问答服务",
            "purpose": "面向用户的问答业务",
            "container_ids": ids,
        },
    )


@pytest.mark.parametrize("entry", ["application", "server"])
def test_service_owner_requires_active_system_user(
    client, admin_headers, inventory, make_user, entry
):
    from app.models import User

    servers, _ = inventory
    make_user("enabled-owner", role="viewer")
    make_user("disabled-owner", active=False)
    deleted_id = make_user("deleted-owner")
    with SessionLocal() as db:
        db.get(User, deleted_id).deleted_at = datetime.now(UTC)
        db.commit()
    path = (
        "/api/v1/applications"
        if entry == "application"
        else f"/api/v1/servers/{servers[0]}/business-services"
    )
    body = {"code": "owner-service", "name": "负责人校验"}
    for owner in ("随意填写的姓名", "disabled-owner", "deleted-owner"):
        response = client.post(path, headers=admin_headers, json={**body, "owner": owner})
        assert response.status_code == 422, response.text
    response = client.post(path, headers=admin_headers, json={**body, "owner": " enabled-owner "})
    assert response.status_code == 201, response.text
    app_id = response.json()["id"]
    assert response.json()["owner"] == "enabled-owner"
    path += f"/{app_id}"
    if entry == "server":
        body.pop("code")
    response = client.put(path, headers=admin_headers, json={**body, "owner": "陌生姓名"})
    assert response.status_code == 422, response.text
    assert (
        client.get(f"/api/v1/applications/{app_id}", headers=admin_headers).json()["owner"]
        == "enabled-owner"
    )

    # Keep historical values during unrelated edits; they cannot be newly assigned.
    with SessionLocal() as db:
        db.get(Application, UUID(app_id)).owner = "历史负责人"
        db.commit()
    for owner in ("历史负责人", "enabled-owner", ""):
        response = client.put(path, headers=admin_headers, json={**body, "owner": owner})
        assert response.status_code == 200, response.text
        assert response.json()["owner"] == owner


def test_one_service_contains_multiple_containers_and_reuses_application(
    client, admin_headers, inventory
):
    servers, ids = inventory
    response = create(client, admin_headers, servers[0], ids[:2])
    assert response.status_code == 201, response.text
    app_id = response.json()["id"]
    application = client.get(f"/api/v1/applications/{app_id}", headers=admin_headers).json()
    assert application["name"] == "知识问答服务"
    overview = client.get(
        f"/api/v1/servers/{servers[0]}/container-overview", headers=admin_headers
    ).json()
    assert len(overview["services"]) == 1
    assert set(overview["services"][0]["container_ids"]) == set(ids[:2])
    assert overview["unassigned_container_ids"] == []
    assert all(
        c["application_id"] == app_id and c["purpose"] == "原有容器备注"
        for c in overview["containers"]
    )
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Application)) == 1
        assert db.scalar(select(func.count()).select_from(ApplicationRelease)) == 0
        assert db.scalar(select(func.count()).select_from(ApplicationDeployment)) == 0


def test_membership_is_atomic_single_owner_and_server_scoped(client, admin_headers, inventory):
    servers, ids = inventory
    first = create(client, admin_headers, servers[0], ids[:2]).json()["id"]
    conflict = create(client, admin_headers, servers[0], [ids[0]], code="conflict")
    assert conflict.status_code == 409, conflict.text
    with SessionLocal() as db:
        assert db.scalar(select(Application).where(Application.code == "conflict")) is None
    path = f"/api/v1/servers/{servers[0]}/business-services/{first}/containers"
    assert (
        client.put(
            path, headers=admin_headers, json={"container_ids": [ids[0], ids[2]]}
        ).status_code
        == 422
    )
    assert (
        client.put(path, headers=admin_headers, json={"container_ids": [str(uuid4())]}).status_code
        == 422
    )
    other = f"/api/v1/servers/{servers[1]}/business-services/{first}/containers"
    assert (
        client.put(other, headers=admin_headers, json={"container_ids": ids[2:]}).status_code == 200
    )
    assert (
        client.put(path, headers=admin_headers, json={"container_ids": [ids[0]]}).status_code == 200
    )
    overview = client.get(
        f"/api/v1/servers/{servers[0]}/container-overview", headers=admin_headers
    ).json()
    assert overview["services"][0]["container_ids"] == [ids[0]]
    assert overview["unassigned_container_ids"] == [ids[1]]
    overview = client.get(
        f"/api/v1/servers/{servers[1]}/container-overview", headers=admin_headers
    ).json()
    assert set(overview["services"][0]["container_ids"]) == set(ids[2:])
    client.delete(f"/api/v1/containers/{ids[1]}", headers=admin_headers)
    assert (
        client.put(path, headers=admin_headers, json={"container_ids": [ids[1]]}).status_code == 422
    )


def test_profile_preserves_source_settings_and_validates_service_dependencies(
    client, admin_headers, inventory
):
    servers, ids = inventory
    first = create(client, admin_headers, servers[0], ids[:1]).json()["id"]
    second = create(client, admin_headers, servers[0], ids[1:2], code="database").json()["id"]
    with SessionLocal() as db:
        row = db.get(Application, UUID(first))
        row.git_url = "https://example.invalid/repo"
        row.git_ref = "stable"
        row.encrypted_git_token = "opaque-ciphertext"
        db.commit()
    path = f"/api/v1/servers/{servers[0]}/business-services/{first}"
    body = {
        "name": "问答业务",
        "purpose": "提供问答",
        "container_ids": ids[:1],
        "depends_on": [second, second],
    }
    response = client.put(path, headers=admin_headers, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["depends_on"] == [second]
    assert "git_url" not in response.json() and "encrypted_git_token" not in response.json()
    with SessionLocal() as db:
        row = db.get(Application, UUID(first))
        assert (row.git_url, row.git_ref, row.encrypted_git_token) == (
            "https://example.invalid/repo",
            "stable",
            "opaque-ciphertext",
        )
    body["depends_on"] = [first]
    assert client.put(path, headers=admin_headers, json=body).status_code == 422
    body["depends_on"] = [str(uuid4())]
    assert client.put(path, headers=admin_headers, json=body).status_code == 404


def test_business_permissions(client, make_user, login, inventory):
    servers, ids = inventory
    for role in ("viewer", "operator", "admin"):
        make_user(role, role)
        headers = {"Authorization": "Bearer " + login(role)["access_token"]}
        assert client.get("/api/v1/business-services", headers=headers).status_code == 200
        response = create(client, headers, servers[0], ids[:2], code="role-service")
        assert response.status_code == (201 if role == "admin" else 403)


def test_service_source_connection_is_shared_encrypted_and_updated_atomically(
    client, admin_headers, inventory
):
    servers, ids = inventory
    connection = {
        "git_url": "https://example.invalid/project.git",
        "git_ref": "stable",
        "git_username": "reader",
        "git_token": "service-source-test-token",
        "source_subdir": "backend",
        "entrypoint": "app.main:app",
        "path_mappings": [{"runtime_prefix": "/app", "repository_prefix": "backend"}],
        "local_path": "backup",
        "enabled": False,
    }
    body = {
        "code": "connected-service",
        "name": "关联源码服务",
        "container_ids": ids[:1],
        "source_connection": connection,
    }
    response = client.post(
        f"/api/v1/servers/{servers[0]}/business-services", headers=admin_headers, json=body
    )
    assert response.status_code == 201, response.text
    app_id = response.json()["id"]
    app_path = f"/api/v1/applications/{app_id}"
    application = client.get(app_path, headers=admin_headers).json()
    assert application["has_git_token"]
    for key, value in connection.items():
        if key != "git_token":
            assert application[key] == value
    with SessionLocal() as db:
        row = db.get(Application, UUID(app_id))
        assert row.encrypted_git_token != connection["git_token"]
        assert (
            cipher().decrypt(row.encrypted_git_token.encode()).decode() == connection["git_token"]
        )
        assert all(
            connection["git_token"] not in str(log.detail)
            for log in db.scalars(select(SourceAuditLog))
        )
        assert db.get(Container, UUID(ids[0])).application_id == row.id
    assert "git_token" not in {k for k in application if k != "has_git_token"}
    assert connection["git_token"] not in response.text

    # Changes made in the application page are read back by the server editor.
    original = {k: application[k] for k in ("code", "name", "owner")}
    connection.pop("git_token")
    connection["git_ref"] = "release-2"
    response = client.put(app_path, headers=admin_headers, json={**original, **connection})
    assert response.status_code == 200, response.text
    assert response.json()["git_ref"] == "release-2" and response.json()["has_git_token"]

    path = f"/api/v1/servers/{servers[0]}/business-services/{app_id}"
    body.pop("code")
    body["name"] = "更新后的服务"
    body["container_ids"] = ids[:2]
    connection["git_ref"] = "release-3"
    response = client.put(path, headers=admin_headers, json=body)
    assert response.status_code == 200, response.text
    updated = client.get(app_path, headers=admin_headers).json()
    assert updated["name"] == body["name"] and updated["git_ref"] == "release-3"
    assert updated["has_git_token"]
    assert updated["updated_at"] > application["updated_at"]

    # A container validation failure must roll back source and profile changes too.
    body["name"] = "不应保存"
    body["container_ids"] = [ids[2]]
    connection["git_ref"] = "must-not-persist"
    assert client.put(path, headers=admin_headers, json=body).status_code == 422
    assert client.get(app_path, headers=admin_headers).json() == updated
    with SessionLocal() as db:
        assert all(db.get(Container, UUID(i)).application_id == UUID(app_id) for i in ids[:2])


@pytest.mark.parametrize("change", ["url", "username", "clear", "replace"])
def test_service_source_token_lifecycle(client, admin_headers, inventory, change):
    servers, ids = inventory
    app_id = create(client, admin_headers, servers[0], ids[:1]).json()["id"]
    with SessionLocal() as db:
        row = db.get(Application, UUID(app_id))
        row.git_url = "https://example.invalid/project.git"
        row.git_username = "reader"
        row.encrypted_git_token = cipher().encrypt(b"old-test-token").decode()
        db.commit()
    source = {"git_url": "https://example.invalid/project.git", "git_username": "reader"}
    if change == "url":
        source["git_url"] = "https://example.invalid/other.git"
    elif change == "username":
        source["git_username"] = "another-reader"
    elif change == "clear":
        source["clear_git_token"] = True
    else:
        source["git_token"] = "new-test-token"
    response = client.put(
        f"/api/v1/servers/{servers[0]}/business-services/{app_id}",
        headers=admin_headers,
        json={"name": "服务", "container_ids": ids[:1], "source_connection": source},
    )
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        token = db.get(Application, UUID(app_id)).encrypted_git_token
        if change == "replace":
            assert cipher().decrypt(token.encode()) == b"new-test-token"
        else:
            assert token is None


@pytest.mark.parametrize(
    "connection",
    [
        {"git_url": "http://example.invalid/repo"},
        {"git_url": "https://user:password@example.invalid/repo"},
        {"source_subdir": "../escape"},
        {"git_ref": "--upload-pack=invalid"},
        {"git_token": "test-token-without-url-and-username"},
    ],
)
def test_invalid_service_source_does_not_create_service_or_assign_containers(
    client, admin_headers, inventory, connection
):
    servers, ids = inventory
    response = client.post(
        f"/api/v1/servers/{servers[0]}/business-services",
        headers=admin_headers,
        json={
            "code": "invalid-source",
            "name": "无效源码配置",
            "container_ids": ids[:1],
            "source_connection": connection,
        },
    )
    assert response.status_code == 422, response.text
    with SessionLocal() as db:
        assert db.scalar(select(Application).where(Application.code == "invalid-source")) is None
        assert db.get(Container, UUID(ids[0])).application_id is None


def test_service_svn_connection_roundtrip(client, admin_headers, inventory):
    servers, ids = inventory
    response = client.post(
        f"/api/v1/servers/{servers[0]}/business-services",
        headers=admin_headers,
        json={
            "code": "svn-service",
            "name": "SVN 服务",
            "container_ids": ids[:1],
            "source_connection": {
                "svn_url": "https://svn.example/trunk",
                "svn_revision": "123",
                "svn_username": "reader",
                "svn_password": "svn-service-test-password",
            },
        },
    )
    assert response.status_code == 201, response.text
    app_id = response.json()["id"]
    updated = client.put(
        f"/api/v1/servers/{servers[0]}/business-services/{app_id}",
        headers=admin_headers,
        json={"name": "重命名", "container_ids": ids[:1]},
    )
    assert updated.status_code == 200
    connection = client.get(f"/api/v1/applications/{app_id}", headers=admin_headers).json()
    assert connection["svn_url"] == "https://svn.example/trunk"
    assert connection["svn_revision"] == "123" and connection["has_svn_password"]
    assert "svn-service-test-password" not in str(connection)


def test_inventory_sync_keeps_business_membership(client, admin_headers, inventory, monkeypatch):
    servers, ids = inventory
    app_id = create(client, admin_headers, servers[0], ids[:2]).json()["id"]

    def query(_path, params, **_kwargs):
        return (
            {"result": [{"value": [0, "1"]}]}
            if params["query"].startswith("up")
            else {
                "result": [
                    {
                        "metric": {
                            "id": "/docker/" + "1" * 64,
                            "name": "component-0",
                            "image": "test:2",
                        },
                        "value": [0, str(datetime.now(UTC).timestamp())],
                    }
                ]
            }
        )

    monkeypatch.setattr("app.services.resources.crud.prometheus", query)
    response = client.post(f"/api/v1/servers/{servers[0]}/sync-containers", headers=admin_headers)
    assert response.status_code == 200, response.text
    assert all(c["application_id"] == app_id for c in response.json())
    assert all(c["purpose"] == "原有容器备注" for c in response.json())


@pytest.mark.parametrize("conflict", [False, True])
def test_overview_uses_current_compose_instance_without_rewriting_history(
    client, admin_headers, inventory, monkeypatch, conflict
):
    servers, ids = inventory
    app_id = create(client, admin_headers, servers[0], ids[:1]).json()["id"]
    with SessionLocal() as db:
        previous = db.get(Container, UUID(ids[0]))
        previous.identity = {"compose_project": "project", "compose_service": "api"}
        previous.status = "stopped"
        current = db.get(Container, UUID(ids[1]))
        current.name = previous.name
        current.identity = dict(previous.identity)
        if conflict:
            other = Application(code="other", name="另一个业务")
            db.add(other)
            db.flush()
            current.application_id = other.id
        db.commit()

    def observed(_server, containers):
        return {
            "server_id": UUID(servers[0]),
            "collected_at": datetime.now(UTC),
            "containers": containers,
            "observations": [
                {"container_id": UUID(ids[0])},
                {"container_id": UUID(ids[1]), "state": "running", "health": "healthy"},
            ],
        }

    monkeypatch.setattr("app.services.resources.overview.overview", observed)
    response = client.get(f"/api/v1/servers/{servers[0]}/container-overview", headers=admin_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    service = next(s for s in data["services"] if s["id"] == app_id)
    assert service["container_ids"] == [ids[0] if conflict else ids[1]]
    assert len(data["containers"]) == (2 if conflict else 1)
    if not conflict:
        assert data["unassigned_container_ids"] == []
        assert data["containers"][0]["application_id"] == app_id
    with SessionLocal() as db:
        assert str(db.get(Container, UUID(ids[0])).application_id) == app_id
        if not conflict:
            assert db.get(Container, UUID(ids[1])).application_id is None


@pytest.mark.parametrize(
    "case", ["no_sample", "ambiguous", "different_project", "different_name", "no_identity"]
)
def test_overview_does_not_guess_a_replacement(client, admin_headers, inventory, monkeypatch, case):
    servers, ids = inventory
    app_id = create(client, admin_headers, servers[0], ids[:1]).json()["id"]
    with SessionLocal() as db:
        previous = db.get(Container, UUID(ids[0]))
        previous.identity = {"compose_project": "project", "compose_service": "api"}
        current = db.get(Container, UUID(ids[1]))
        current.name = previous.name if case != "different_name" else "another-instance"
        current.identity = dict(previous.identity)
        if case == "different_project":
            current.identity = {"compose_project": "another-project", "compose_service": "api"}
        if case == "no_identity":
            current.identity = {}
        db.commit()

    def observed(_server, containers):
        return {
            "server_id": UUID(servers[0]),
            "collected_at": datetime.now(UTC),
            "containers": containers,
            "observations": [
                {"container_id": UUID(ids[0]), "state": "running" if case == "ambiguous" else None},
                {"container_id": UUID(ids[1]), "state": None if case == "no_sample" else "running"},
            ],
        }

    monkeypatch.setattr("app.services.resources.overview.overview", observed)
    data = client.get(
        f"/api/v1/servers/{servers[0]}/container-overview", headers=admin_headers
    ).json()
    assert len(data["containers"]) == 2
    assert data["services"][0]["id"] == app_id
    assert data["services"][0]["container_ids"] == [ids[0]]
    assert data["unassigned_container_ids"] == [ids[1]]


def test_migration_backfills_only_unambiguous_active_deployment(inventory, migrated_database):
    servers, ids = inventory
    now = datetime.now(UTC)
    with SessionLocal() as db:
        apps = [Application(code=f"app-{i}", name=f"业务-{i}") for i in range(2)]
        db.add_all(apps)
        db.flush()
        releases = [ApplicationRelease(application_id=a.id, version="1") for a in apps]
        db.add_all(releases)
        db.flush()
        for container_index, app_index, ended in (
            (0, 0, False),
            (1, 0, False),
            (1, 1, False),
            (2, 0, True),
        ):
            db.add(
                ApplicationDeployment(
                    application_id=apps[app_index].id,
                    release_id=releases[app_index].id,
                    server_id=UUID(servers[container_index // 2]),
                    container_id=UUID(ids[container_index]),
                    environment="test",
                    started_at=now - timedelta(hours=1),
                    ended_at=now - timedelta(minutes=1) if ended else None,
                    binding={"evidence": "retained"},
                )
            )
        db.commit()
        first_id = apps[0].id
    try:
        command.downgrade(migrated_database, "0017_container_services")
        command.upgrade(migrated_database, "head")
        command.check(migrated_database)
        with SessionLocal() as db:
            assert db.get(Container, UUID(ids[0])).application_id == first_id
            assert db.get(Container, UUID(ids[1])).application_id is None
            assert db.get(Container, UUID(ids[2])).application_id is None
            assert all(
                c.purpose == "原有容器备注" and c.created_at.tzinfo and c.updated_at.tzinfo
                for c in db.scalars(select(Container))
            )
            assert all(
                d.binding == {"evidence": "retained"}
                for d in db.scalars(select(ApplicationDeployment))
            )
    finally:
        command.upgrade(migrated_database, "head")
