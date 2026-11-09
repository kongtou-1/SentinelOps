from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

from app.db.session import SessionLocal
from app.models import Application
from app.services.operations import objectives


def service():
    with SessionLocal() as db:
        row = Application(code="ops-test", name="Business", enabled=True)
        db.add(row)
        db.commit()
        return row.id


def test_platform_objective_uses_fixed_workflow_not_an_arbitrary_query(client, admin_headers):
    identifier = service()
    body = {
        "name": "Platform diagnosis",
        "kind": "business_success",
        "metric_source": "platform",
        "operation": "diagnosis",
        "target": 0.99,
    }
    response = client.post(
        f"/api/v1/business-services/{identifier}/objectives", headers=admin_headers, json=body
    )
    assert response.status_code == 201, response.text
    from app.models import ServiceObjective

    with SessionLocal() as db:
        row = db.get(ServiceObjective, UUID(response.json()["id"]))
        total, value, health = objectives.expressions(row, db.get(Application, identifier), None)
        assert 'job="platform"' in total and "application_id" not in total
        assert health == 'up{job="platform"}'
    body["operation"] = "arbitrary_promql"
    assert (
        client.post(
            f"/api/v1/business-services/{identifier}/objectives", headers=admin_headers, json=body
        ).status_code
        == 422
    )


def test_objective_permissions_versions_and_foreign_binding(
    client, admin_headers, make_user, login
):
    identifier = service()
    path = f"/api/v1/business-services/{identifier}/objectives"
    body = {"name": "Success", "kind": "business_success", "operation": "work", "target": 0.99}
    make_user("reader", "viewer")
    viewer = {"Authorization": "Bearer " + login("reader")["access_token"]}
    assert client.post(path, json=body, headers=viewer).status_code == 403
    created = client.post(path, json=body, headers=admin_headers)
    assert created.status_code == 201, created.text
    row = created.json()
    assert row["version"] == 1 and row["evaluation"] == {}
    assert client.get(path, headers=viewer).json()[0]["id"] == row["id"]
    assert client.put(path + "/" + row["id"], json=body, headers=admin_headers).status_code == 409
    body["expected_version"] = 1
    updated = client.put(path + "/" + row["id"], json=body, headers=admin_headers)
    assert updated.status_code == 200 and updated.json()["version"] == 2
    invalid = {**body, "expected_version": 0, "kind": "request_p95", "monitor_id": str(uuid4())}
    assert client.post(path, json=invalid, headers=admin_headers).status_code == 404
    assert (
        client.delete(
            f"/api/v1/service-objectives/{row['id']}?expected_version=1", headers=admin_headers
        ).status_code
        == 409
    )
    assert (
        client.delete(
            f"/api/v1/service-objectives/{row['id']}?expected_version=2", headers=admin_headers
        ).status_code
        == 204
    )
    assert client.get(path, headers=admin_headers).json() == []


def test_objective_missing_stale_history_and_error_budget(monkeypatch):
    now = datetime.now(UTC)
    row = SimpleNamespace(
        enabled=True, kind="business_success", operation="work", target=0.99, window_days=1
    )
    application = SimpleNamespace(
        enabled=True, traffic_target=None, id=uuid4(), traffic_config_changed_at=now
    )
    assert objectives.evaluate(row, application)["status"] == "unconfigured"
    application.traffic_target = "business:8080"

    def query(path, params, **kwargs):
        if path.endswith("query_range"):
            return {"result": [{"values": [[i, "1"] for i in range(1441)]}]}
        expression = params["query"]
        value = (
            now.timestamp()
            if "timestamp(" in expression
            else 1
            if expression.startswith("min(")
            else 1000
            if " / " not in expression
            else 0.98
        )
        return {"result": [{"value": [now.timestamp(), str(value)]}]}

    monkeypatch.setattr(objectives, "prometheus", query)
    result = objectives.evaluate(row, application, now=now)
    assert result["status"] == "breached"
    assert round(result["error_budget_remaining"]) == -10
    original = query

    def partial(path, params, **kwargs):
        return {"result": []} if path.endswith("query_range") else original(path, params)

    monkeypatch.setattr(objectives, "prometheus", partial)
    assert objectives.evaluate(row, application, now=now)["status"] == "provisional"
    monkeypatch.setattr(objectives, "scalar", lambda *args: None)
    assert objectives.evaluate(row, application, now=now)["status"] == "stale"
