from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from alembic import command
from cryptography.fernet import Fernet
from sqlalchemy import inspect, select

from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.models import Application, LLMEndpoint, LLMServiceBinding, SourceAuditLog


@pytest.fixture
def binding_targets(client, admin_headers, tmp_path, monkeypatch):
    key = tmp_path / "binding-key"
    key.write_bytes(Fernet.generate_key())
    monkeypatch.setattr(get_settings(), "credential_key_file", key)
    apps = []
    endpoints = []
    for index in range(2):
        response = client.post(
            "/api/v1/applications",
            headers=admin_headers,
            json={
                "code": f"binding-app-{index}",
                "name": f"业务服务 {index}",
            },
        )
        assert response.status_code == 201, response.text
        apps.append(response.json()["id"])
        response = client.post(
            "/api/v1/llm-endpoints",
            headers=admin_headers,
            json={
                "name": f"binding-endpoint-{index}",
                "base_url": "https://api.example.invalid/v1",
                "model": "test-model",
                "api_key": "binding-test-secret",
                "probe_enabled": False,
            },
        )
        assert response.status_code == 201, response.text
        endpoints.append(response.json()["id"])
    return apps, endpoints


def path(endpoint, application):
    return f"/api/v1/llm-endpoints/{endpoint}/applications/{application}"


def test_binding_migration_roundtrip_and_timestamp_fields(migrated_database):
    try:
        command.downgrade(migrated_database, "0019_discover_market")
        assert "llm_service_bindings" not in inspect(engine).get_table_names()
    finally:
        command.upgrade(migrated_database, "head")
    columns = {
        column["name"]: column for column in inspect(engine).get_columns("llm_service_bindings")
    }
    for field in ("created_at", "updated_at", "deleted_at"):
        assert columns[field]["type"].timezone
        assert columns[field]["nullable"] == (field == "deleted_at")
    assert columns["created_at"]["default"] and columns["updated_at"]["default"]


def test_shared_endpoints_notes_soft_delete_and_probe_preservation(
    client, admin_headers, binding_targets
):
    apps, endpoints = binding_targets
    with SessionLocal() as db:
        endpoint = db.get(LLMEndpoint, UUID(endpoints[0]))
        endpoint.last_probe_at = datetime.now(UTC)
        endpoint.last_probe_success = True
        db.commit()
        probe_at, epoch, encrypted = (
            endpoint.last_probe_at,
            endpoint.probe_config_changed_at,
            endpoint.encrypted_api_key,
        )
    for app, endpoint, note in (
        (apps[0], endpoints[0], "问答生成"),
        (apps[1], endpoints[0], "故障诊断"),
        (apps[0], endpoints[1], "结果复核"),
    ):
        response = client.put(path(endpoint, app), headers=admin_headers, json={"purpose": note})
        assert response.status_code == 200, response.text
        assert response.json()["purpose"] == note
    listing = client.get("/api/v1/llm-bindings", headers=admin_headers)
    assert listing.headers["X-Total-Count"] == "3"
    assert "binding-test-secret" not in listing.text and "encrypted_api_key" not in listing.text
    assert (
        len(
            client.get(
                f"/api/v1/llm-bindings?application_id={apps[0]}", headers=admin_headers
            ).json()
        )
        == 2
    )
    assert (
        len(
            client.get(
                f"/api/v1/llm-bindings?llm_endpoint_id={endpoints[0]}", headers=admin_headers
            ).json()
        )
        == 2
    )
    first = client.put(
        path(endpoints[0], apps[0]), headers=admin_headers, json={"purpose": "更新备注"}
    ).json()
    assert first["purpose"] == "更新备注"
    assert client.delete(path(endpoints[0], apps[0]), headers=admin_headers).status_code == 204
    assert len(client.get("/api/v1/llm-bindings", headers=admin_headers).json()) == 2
    with SessionLocal() as db:
        row = db.get(LLMServiceBinding, UUID(first["id"]))
        assert row.deleted_at and row.updated_at >= row.created_at
    restored = client.put(
        path(endpoints[0], apps[0]), headers=admin_headers, json={"purpose": "重新关联"}
    ).json()
    assert restored["id"] == first["id"]
    assert restored["updated_at"] > first["updated_at"]
    with SessionLocal() as db:
        endpoint = db.get(LLMEndpoint, UUID(endpoints[0]))
        assert (
            endpoint.last_probe_at,
            endpoint.probe_config_changed_at,
            endpoint.encrypted_api_key,
        ) == (probe_at, epoch, encrypted)
        assert db.scalar(
            select(SourceAuditLog.id).where(SourceAuditLog.action == "llm_service_binding_removed")
        )


@pytest.mark.parametrize("role", ["viewer", "operator"])
def test_binding_permissions(client, admin_headers, binding_targets, make_user, login, role):
    apps, endpoints = binding_targets
    make_user(role, role)
    headers = {"Authorization": f"Bearer {login(role)['access_token']}"}
    assert client.get("/api/v1/llm-bindings", headers=headers).status_code == 200
    assert (
        client.put(
            path(endpoints[0], apps[0]), headers=headers, json={"purpose": "不可修改"}
        ).status_code
        == 403
    )
    assert client.delete(path(endpoints[0], apps[0]), headers=headers).status_code == 403


def test_binding_validation_and_deleted_targets(client, admin_headers, binding_targets):
    apps, endpoints = binding_targets
    assert (
        client.put(path(endpoints[0], uuid4()), headers=admin_headers, json={}).status_code == 404
    )
    assert client.put(path(uuid4(), apps[0]), headers=admin_headers, json={}).status_code == 404
    assert (
        client.put(
            path(endpoints[0], apps[0]), headers=admin_headers, json={"purpose": "长" * 2001}
        ).status_code
        == 422
    )
    assert (
        client.put(path(endpoints[0], apps[0]), headers=admin_headers, json={}).status_code == 200
    )
    with SessionLocal() as db:
        db.get(Application, UUID(apps[0])).deleted_at = datetime.now(UTC)
        db.commit()
    assert client.get("/api/v1/llm-bindings", headers=admin_headers).json() == []
    assert (
        client.put(path(endpoints[0], apps[0]), headers=admin_headers, json={}).status_code == 404
    )
    assert (
        client.get(
            f"/api/v1/llm-bindings?application_id={apps[0]}", headers=admin_headers
        ).status_code
        == 404
    )
