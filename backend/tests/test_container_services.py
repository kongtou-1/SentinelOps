from datetime import UTC, datetime
from uuid import uuid4

from alembic import command
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import Container, Server


def assets():
    with SessionLocal() as db:
        servers = [
            Server(
                name=f"host-{i}",
                host="127.0.0.1",
                node_exporter_target="node:9100",
                cadvisor_target="cadvisor:8080",
                docker_state_target="state:9400",
            )
            for i in range(2)
        ]
        db.add_all(servers)
        db.flush()
        containers = [
            Container(
                server_id=servers[i // 2].id,
                docker_id="/docker/" + str(i + 1) * 64,
                name=f"service-{i}",
                image="app:1",
                status="running",
                labels={},
                last_seen_at=datetime.now(UTC),
            )
            for i in range(4)
        ]
        db.add_all(containers)
        db.commit()
        return [s.id for s in servers], [c.id for c in containers]


def test_service_metadata_validation_search_and_sync_preservation(
    client, admin_headers, monkeypatch
):
    servers, containers = assets()
    first, second, remote, _ = containers
    path = f"/api/v1/containers/{first}/service"
    for dependencies, status in (([first], 422), ([remote], 422), ([uuid4()], 404)):
        response = client.put(
            path, headers=admin_headers, json={"depends_on": [str(d) for d in dependencies]}
        )
        assert response.status_code == status, response.text
    response = client.put(
        path,
        headers=admin_headers,
        json={
            "service_name": " 推理入口 ",
            "purpose": "处理模型推理请求",
            "depends_on": [str(second)] * 2,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["depends_on"] == [str(second)]
    assert response.json()["service_name"] == "推理入口"
    found = client.get("/api/v1/containers?q=模型推理", headers=admin_headers).json()
    assert [c["id"] for c in found] == [str(first)]

    def prometheus(_path, params, **_kwargs):
        if "docker_container_identity_info" in params["query"]:
            return {"result": []}
        if "container_last_seen" in params["query"]:
            return {
                "result": [
                    {
                        "metric": {
                            "id": "/docker/" + "1" * 64,
                            "name": "service-0",
                            "image": "app:2",
                        },
                        "value": [
                            datetime.now(UTC).timestamp(),
                            str(datetime.now(UTC).timestamp()),
                        ],
                    }
                ]
            }
        return {"result": [{"value": [datetime.now(UTC).timestamp(), "1"]}]}

    monkeypatch.setattr("app.services.resources.crud.prometheus", prometheus)
    response = client.post(f"/api/v1/servers/{servers[0]}/sync-containers", headers=admin_headers)
    assert response.status_code == 200, response.text
    updated = next(c for c in response.json() if c["id"] == str(first))
    assert updated["service_name"] == "推理入口" and updated["image"] == "app:2"
    assert updated["purpose"] == "处理模型推理请求" and updated["depends_on"] == [str(second)]
    assert updated["updated_at"] >= updated["created_at"]
    assert client.delete(f"/api/v1/containers/{second}", headers=admin_headers).status_code == 204
    assert (
        client.put(path, headers=admin_headers, json={"depends_on": [str(second)]}).status_code
        == 404
    )


def test_service_permissions(client, make_user, login, monkeypatch):
    servers, containers = assets()
    monkeypatch.setattr("app.services.resources.overview.fetch", lambda *_args: None)
    for role in ("viewer", "operator", "admin"):
        make_user(role, role)
        headers = {"Authorization": "Bearer " + login(role)["access_token"]}
        response = client.get(f"/api/v1/servers/{servers[0]}/container-overview", headers=headers)
        assert response.status_code == 200, response.text
        assert len(response.json()["containers"]) == 2
        assert all(c["state"] is None for c in response.json()["observations"])
        response = client.put(
            f"/api/v1/containers/{containers[0]}/service",
            headers=headers,
            json={"purpose": "业务入口"},
        )
        assert response.status_code == (200 if role == "admin" else 403)


def test_current_overview_normalization_networks_and_stale_samples(
    client, admin_headers, monkeypatch
):
    servers, containers = assets()
    with SessionLocal() as db:
        server = db.get(Server, servers[0])
        epoch = server.collection_config_changed_at.isoformat()
    now = datetime.now(UTC).timestamp()
    queries = []

    def fetch(_path, params):
        expression = params["query"]
        queries.append(expression)
        metric = {"server_id": str(servers[0]), "source_epoch": epoch, "id": "/docker/" + "1" * 64}
        value = "1"
        if "docker_container_state_info" in expression:
            metric["state"] = "running"
        elif "docker_container_health_status" in expression:
            metric["health"] = "healthy"
        elif "docker_container_network_info" in expression:
            metric["network"] = "app-private"
        elif "container_cpu_usage_seconds_total" in expression:
            assert "count by (server_id, source_epoch)" in expression
            value = "12.5"  # Prometheus divides 1-core percentage by host core count.
        elif "container_memory_working_set_bytes" in expression:
            value = "1024"
        elif "node_cpu_seconds_total" in expression:
            value = "30"
        elif " - node_memory" in expression:
            value = "2048"
        else:
            value = "4096"
        valid = {"metric": metric, "value": [now, value]}
        old = {"metric": {**metric, "id": "/docker/" + "2" * 64}, "value": [now - 120, value]}
        other_epoch = {"metric": {**metric, "source_epoch": "old"}, "value": [now, "9999"]}
        return {"result": [valid, old, other_epoch]}

    monkeypatch.setattr("app.services.resources.overview.fetch", fetch)
    response = client.get(f"/api/v1/servers/{servers[0]}/container-overview", headers=admin_headers)
    assert response.status_code == 200, response.text
    result = response.json()
    observed = {o["container_id"]: o for o in result["observations"]}
    assert observed[str(containers[0])]["cpu_host_percent"] == 12.5
    assert observed[str(containers[0])]["networks"] == ["app-private"]
    assert observed[str(containers[0])]["state"] == "running"
    assert observed[str(containers[1])]["state"] is None
    assert observed[str(containers[1])]["memory_bytes"] is None
    assert result["host_memory_total_bytes"] == 4096
    assert len(queries) == 11
    assert all("source_epoch" in q and "up{" in q for q in queries)


def test_monitoring_paused_and_deleted_host(client, admin_headers, monkeypatch):
    servers, _ = assets()
    with SessionLocal() as db:
        db.get(Server, servers[0]).monitoring_enabled = False
        db.commit()

    def unexpected(*_args):
        raise AssertionError("Paused monitoring must not query Prometheus")

    monkeypatch.setattr("app.services.resources.overview.fetch", unexpected)
    path = f"/api/v1/servers/{servers[0]}/container-overview"
    response = client.get(path, headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["host_cpu_percent"] is None
    client.delete(f"/api/v1/servers/{servers[0]}", headers=admin_headers)
    assert client.get(path, headers=admin_headers).status_code == 404


def test_service_migration_preserves_existing_containers(migrated_database):
    _, containers = assets()
    try:
        command.downgrade(migrated_database, "0016_incident_closure")
        command.upgrade(migrated_database, "head")
        command.check(migrated_database)
        with SessionLocal() as db:
            container = db.scalar(select(Container).where(Container.id == containers[0]))
            assert container.service_name == "" and container.purpose == ""
            assert container.depends_on == []
            before = container.updated_at
            container.purpose = "业务入口"
            db.commit()
            assert container.updated_at > before
            assert container.created_at.tzinfo is not None and container.deleted_at is None
    finally:
        command.upgrade(migrated_database, "head")
