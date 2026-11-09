"""Independent connection management and legacy query editing."""

import json
from uuid import UUID

import pytest
from sqlalchemy import select
from test_inspections import source as source

from app.core.secrets import cipher
from app.db.session import SessionLocal
from app.models import InspectionDataSource, OperationsAuditLog, ServiceDatabase


def connection_body(**values):
    return {
        "name": "独立只读数据库",
        "engine": "postgresql",
        "host": "127.0.0.1",
        "port": 5432,
        "database": "business_test",
        "username": "reader",
        "environment": "test",
        "password": "fixture-private-password",
        **values,
    }


def test_independent_connection_lifecycle_and_optional_service(client, admin_headers):
    created = client.post(
        "/api/v1/service-databases", headers=admin_headers, json=connection_body()
    )
    assert created.status_code == 201, created.text
    row = created.json()
    identifier = row["id"]
    assert row["application_id"] is None and row["password_configured"]
    assert "fixture-private-password" not in created.text
    assert identifier in {
        item["id"] for item in client.get("/api/v1/service-databases", headers=admin_headers).json()
    }
    with SessionLocal() as db:
        saved = db.get(ServiceDatabase, UUID(identifier))
        assert "fixture-private-password" not in saved.encrypted_config
        assert db.scalar(
            select(OperationsAuditLog).where(
                OperationsAuditLog.resource_id == UUID(identifier),
                OperationsAuditLog.action == "database_configured",
            )
        )
    service = client.post(
        "/api/v1/applications",
        headers=admin_headers,
        json={"name": "订单服务", "code": "independent-db-service"},
    )
    assert service.status_code == 201, service.text
    path = f"/api/v1/service-databases/{identifier}"
    for application_id in (service.json()["id"], None):
        updated = client.put(
            path,
            headers=admin_headers,
            json={
                k: v
                for k, v in connection_body(application_id=application_id).items()
                if k != "password"
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["application_id"] == application_id
        assert updated.json()["password_configured"]
    assert client.delete(path, headers=admin_headers).status_code == 204
    assert client.get(path, headers=admin_headers).status_code == 404
    assert client.get("/api/v1/service-databases", headers=admin_headers).json() == []


@pytest.mark.parametrize("role", ["viewer", "operator"])
def test_independent_connection_keeps_management_permissions(
    client, admin_headers, make_user, login, role
):
    created = client.post(
        "/api/v1/service-databases", headers=admin_headers, json=connection_body()
    )
    assert created.status_code == 201, created.text
    identifier = created.json()["id"]
    make_user(role, role)
    headers = {"Authorization": "Bearer " + login(role)["access_token"]}
    assert client.get("/api/v1/service-databases", headers=headers).status_code == 200
    assert client.get(f"/api/v1/service-databases/{identifier}", headers=headers).status_code == 200
    assert (
        client.post(
            "/api/v1/service-databases", headers=headers, json=connection_body()
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"/api/v1/service-databases/{identifier}", headers=headers, json=connection_body()
        ).status_code
        == 403
    )
    assert (
        client.delete(f"/api/v1/service-databases/{identifier}", headers=headers).status_code == 403
    )


@pytest.mark.parametrize("edit", ["keep", "replace", "clear", "change_url"])
def test_inline_edit_of_shared_query_preserves_credentials_and_other_queries(
    client, admin_headers, source, edit
):
    source["data"] = [{"id": "one", "amount": 1}]
    created = client.post(
        "/api/v1/inspection-http-connections",
        headers=admin_headers,
        json={
            "name": "旧共享接口",
            "environment": "test",
            "base_url": source["url"],
            "headers": {"X-API-Key": "original-private-key"},
        },
    )
    assert created.status_code == 201, created.text
    connection = created.json()
    body = {
        "name": "订单查询",
        "environment": "test",
        "source_kind": "api",
        "source_config": {"http_connection_id": connection["id"], "response_format": "json"},
    }
    queries = [
        client.post("/api/v1/inspection-data-sources", headers=admin_headers, json=body)
        for _ in range(2)
    ]
    assert all(result.status_code == 201 for result in queries)
    first, second = [result.json() for result in queries]
    body["expected_version"] = first["version"]
    body["source_config"] = {"url": source["url"], "response_format": "json"}
    expected = "original-private-key"
    if edit == "replace":
        body["headers"] = {"X-API-Key": "replacement-private-key"}
        expected = "replacement-private-key"
    elif edit == "clear":
        body["headers"] = {}
        expected = None
    elif edit == "change_url":
        body["source_config"]["url"] += "/another"
        expected = None
    updated = client.put(
        f"/api/v1/inspection-data-sources/{first['id']}", headers=admin_headers, json=body
    )
    assert updated.status_code == 200, updated.text
    assert not updated.json()["source_config"]["http_connection_id"]
    assert updated.json()["headers_configured"] is bool(expected)
    assert "private-key" not in updated.text
    with SessionLocal() as db:
        saved = db.get(InspectionDataSource, UUID(first["id"]))
        headers = json.loads(cipher().decrypt(saved.encrypted_headers.encode())) if expected else {}
        assert headers.get("X-API-Key") == expected
        peer = db.get(InspectionDataSource, UUID(second["id"]))
        assert peer.source_config["http_connection_id"] == connection["id"]
        assert peer.version == second["version"]
    for identifier, expected_key in (
        (first["id"], expected),
        (second["id"], "original-private-key"),
    ):
        preview = client.post(
            f"/api/v1/inspection-data-sources/{identifier}/preview",
            headers=admin_headers,
            json={"limit": 1},
        )
        assert preview.status_code == 200, preview.text
        assert source["headers"][-1].get("X-API-Key") == expected_key
