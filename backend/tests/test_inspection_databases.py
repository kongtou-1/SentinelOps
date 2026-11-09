"""Run with scripts/accept_service_databases.py against isolated MySQL/PostgreSQL."""

from test_inspections import drain
from test_service_databases import application as application
from test_service_databases import create, enable
from test_service_databases import payload as payload


def test_reusable_database_source_and_paginated_rules(client, admin_headers, application, payload):
    connection = create(client, admin_headers, application, payload)
    connection, _, table = enable(client, admin_headers, connection, payload)
    source = client.post(
        "/api/v1/inspection-data-sources",
        headers=admin_headers,
        json={
            "name": payload["engine"] + " 巡检数据源",
            "application_id": application,
            "environment": "test",
            "source_kind": "database",
            "source_config": {
                "connection_id": connection["id"],
                "table": table,
                "id_column": "id",
                "fact_columns": ["id", "status", "retries"],
            },
        },
    )
    assert source.status_code == 201, source.text
    identifier = source.json()["id"]
    tested = client.post(
        f"/api/v1/inspection-data-sources/{identifier}/test", headers=admin_headers
    )
    assert tested.status_code == 200 and tested.json()["success"], tested.text
    plan = client.post(
        "/api/v1/inspection-plans",
        headers=admin_headers,
        json={
            "name": "数据库任务巡检",
            "application_id": application,
            "environment": "test",
            "data_source_id": identifier,
            "enabled": True,
            "schedule_kind": "manual",
            "max_records": 1000,
            "rules": [
                {
                    "code": "retries",
                    "title": "任务重试次数",
                    "kind": "numeric_range",
                    "path": "retries",
                    "maximum": 1,
                    "basis": "最多重试一次",
                }
            ],
        },
    )
    assert plan.status_code == 201, plan.text
    queued = client.post(
        f"/api/v1/inspection-plans/{plan.json()['id']}/runs", headers=admin_headers
    )
    assert queued.status_code == 202, queued.text
    drain()
    run_id = queued.json()["id"]
    run = client.get(f"/api/v1/inspection-runs/{run_id}", headers=admin_headers).json()
    assert run["status"] == "completed", run
    assert run["summary"]["objects"] == 300
    assert run["summary"]["health_counts"] == {"fail": 300}
    results = client.get(
        f"/api/v1/inspection-runs/{run_id}/items?result=fail&offset=280&limit=20",
        headers=admin_headers,
    )
    assert results.headers["X-Total-Count"] == "300" and len(results.json()) == 20
    assert results.json()[0]["evidence"]["evidence"][0]["source"] == "database"
    assert all(r["rule_verdict"] == r["final_status"] == "fail" for r in results.json())
    assert payload["password"] not in str(run) + source.text + plan.text + results.text
