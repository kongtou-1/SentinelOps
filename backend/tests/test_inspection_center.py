"""End-to-end contracts for the configurable inspection center."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select
from test_inspections import drain
from test_inspections import source as source

from app.db.session import SessionLocal
from app.models import (
    InspectionDataSource,
    InspectionItem,
    InspectionPlan,
    InspectionRun,
    KnowledgeSkill,
)
from app.services.inspections import agent, plans, runtime, scheduling
from app.services.inspections.checkers import check


def dataset(client, headers, source, **values):
    body = {
        "name": "共享接口",
        "environment": "test",
        "source_kind": "api",
        "source_config": {"url": source["url"], "response_format": "json", "items_path": "data"},
        "headers": {"Authorization": "Bearer test-center-private"},
        **values,
    }
    result = client.post("/api/v1/inspection-data-sources", headers=headers, json=body)
    assert result.status_code == 201, result.text
    return result.json(), body


def plan(client, headers, data_source, **values):
    body = {
        "name": "通用业务巡检",
        "environment": "test",
        "enabled": True,
        "data_source_id": data_source["id"],
        "schedule_kind": "manual",
        "rules": [
            {
                "code": "latency",
                "title": "响应耗时",
                "path": "latency",
                "kind": "numeric_range",
                "maximum": 100,
                "basis": "响应耗时不超过 100 ms",
            }
        ],
        **values,
    }
    result = client.post("/api/v1/inspection-plans", headers=headers, json=body)
    assert result.status_code == 201, result.text
    return result.json()


def execute(client, headers, value):
    result = client.post(f"/api/v1/inspection-plans/{value['id']}/runs", headers=headers)
    assert result.status_code == 202, result.text
    drain()
    identifier = result.json()["id"]
    detail = client.get(f"/api/v1/inspection-runs/{identifier}", headers=headers)
    assert detail.status_code == 200, detail.text
    return detail.json()


def test_reusable_source_secrets_connection_test_and_references(client, admin_headers, source):
    source["data"] = {"data": [{"id": "service-a", "latency": 25}]}
    data, body = dataset(client, admin_headers, source)
    assert data["headers_configured"] and "test-center-private" not in json.dumps(data)
    with SessionLocal() as db:
        row = db.get(InspectionDataSource, UUID(data["id"]))
        assert "test-center-private" not in row.encrypted_headers
    tested = client.post(
        f"/api/v1/inspection-data-sources/{data['id']}/test", headers=admin_headers
    )
    assert tested.json()["success"] and tested.json()["records_read"] == 1
    assert source["headers"][-1]["Authorization"] == "Bearer test-center-private"
    first = plan(client, admin_headers, data)
    second = plan(client, admin_headers, data, name="第二计划")
    assert first["next_run_at"] is None
    refs = client.get(f"/api/v1/inspection-data-sources/{data['id']}/plans", headers=admin_headers)
    assert {r["id"] for r in refs.json()} == {first["id"], second["id"]}
    assert (
        client.delete(
            f"/api/v1/inspection-data-sources/{data['id']}", headers=admin_headers
        ).status_code
        == 409
    )
    updated = client.put(
        f"/api/v1/inspection-data-sources/{data['id']}",
        headers=admin_headers,
        json={**body, "headers": None, "expected_version": 1, "name": "更新数据源"},
    )
    assert updated.status_code == 200 and updated.json()["headers_configured"]
    run = execute(client, admin_headers, first)
    assert run["snapshot"]["data_source_version"] == 2
    assert run["summary"]["health_counts"] == {"pass": 1}
    assert "test-center-private" not in json.dumps(run)


def test_generic_json_rules_batch_history_and_pagination(client, admin_headers, source):
    source["data"] = {
        "data": [{"id": f"object-{n:03}", "name": f"服务 {n}", "latency": n} for n in range(65)]
    }
    data, _ = dataset(client, admin_headers, source)
    value = plan(
        client,
        admin_headers,
        data,
        rules=[
            {
                "code": "latency",
                "title": "响应耗时",
                "path": "latency",
                "kind": "numeric_range",
                "maximum": 30,
                "basis": "小于等于 30 ms",
            }
        ],
    )
    first = execute(client, admin_headers, value)
    assert first["summary"]["objects"] == 65
    assert first["summary"]["health_counts"] == {"pass": 31, "fail": 34}
    path = f"/api/v1/inspection-runs/{first['id']}/items"
    results = client.get(path + "?result=fail&offset=20&limit=10", headers=admin_headers)
    assert results.headers["X-Total-Count"] == "34" and len(results.json()) == 10
    assert all(row["final_status"] == "fail" for row in results.json())
    found = client.get(path + "?search=服务%2064", headers=admin_headers).json()
    assert [row["record_id"] for row in found] == ["object-064"]
    second = execute(client, admin_headers, value)
    assert second["summary"]["objects"] == 65
    assert client.get(path, headers=admin_headers).headers["X-Total-Count"] == "65"
    filtered = client.get(
        "/api/v1/inspection-plans?search=通用&enabled=true", headers=admin_headers
    )
    assert filtered.json()[0]["last_result"] == "fail"
    assert filtered.json()[0]["last_run_at"] is not None
    assert (
        len(
            client.get(
                "/api/v1/inspection-runs?search=通用&status=completed", headers=admin_headers
            ).json()
        )
        == 2
    )


def test_run_object_filter_counts_before_pagination(client, admin_headers, source):
    source["data"] = {"data": [{"id": "object-1", "latency": 1}]}
    data, _ = dataset(client, admin_headers, source)
    value = plan(client, admin_headers, data)
    populated = execute(client, admin_headers, value)
    source["data"] = {"data": []}
    empty = execute(client, admin_headers, value)
    path = f"/api/v1/inspection-runs?plan_id={value['id']}&run_kind=standard&status=completed"

    listing = client.get(path + "&has_objects=true&limit=1", headers=admin_headers)
    assert listing.status_code == 200
    assert listing.headers["X-Total-Count"] == "1"
    assert [row["id"] for row in listing.json()] == [populated["id"]]
    next_page = client.get(path + "&has_objects=true&offset=1&limit=1", headers=admin_headers)
    assert next_page.headers["X-Total-Count"] == "1" and next_page.json() == []
    listing = client.get(path + "&has_objects=false", headers=admin_headers)
    assert listing.headers["X-Total-Count"] == "1"
    assert [row["id"] for row in listing.json()] == [empty["id"]]
    listing = client.get(path, headers=admin_headers)
    assert listing.headers["X-Total-Count"] == "2"
    assert {row["id"] for row in listing.json()} == {empty["id"], populated["id"]}
    assert client.get(path + "&has_objects=invalid", headers=admin_headers).status_code == 422


@pytest.mark.parametrize("agent_result", ["failure", "contradicted", "supported"])
def test_agent_never_overwrites_rule_outcome(
    client, admin_headers, source, monkeypatch, agent_result
):
    source["data"] = {"data": [{"id": "normal", "latency": 1}, {"id": "bad", "latency": 500}]}
    with SessionLocal() as db:
        skill = KnowledgeSkill(code="center", title="只读检查", content="根据证据检查耗时", tags=[])
        db.add(skill)
        db.commit()
        skill_id = str(skill.id)
    data, _ = dataset(client, admin_headers, source)
    value = plan(client, admin_headers, data, agent_enabled=True, skill_id=skill_id)

    def evaluate(run_id, item_id):
        if agent_result == "failure":
            raise ValueError("inspection_agent_failed")
        return {
            "verdict": agent_result,
            "summary": "独立分析",
            "evidence_ids": [str(item_id)],
            "data_gaps": [],
        }

    monkeypatch.setattr(agent, "evaluate", evaluate)
    run = execute(client, admin_headers, value)
    assert run["status"] == "completed"
    results = client.get(f"/api/v1/inspection-runs/{run['id']}/items", headers=admin_headers).json()
    assert {r["record_id"]: r["final_status"] for r in results} == {"normal": "pass", "bad": "fail"}
    if agent_result == "failure":
        assert run["error_stage"] == "agent"
        assert all("检查已完成，智能分析失败" in r["explanation"] for r in results)


def test_scheduler_once_cron_manual_and_event(client, admin_headers, source):
    source["data"] = {"data": [{"id": "one", "latency": 12}]}
    data, _ = dataset(client, admin_headers, source)
    now = datetime.now(UTC)
    manual = plan(client, admin_headers, data)
    once = plan(
        client,
        admin_headers,
        data,
        name="单次",
        schedule_kind="once",
        scheduled_at=(now - timedelta(minutes=1)).isoformat(),
    )
    cron = plan(
        client,
        admin_headers,
        data,
        name="Cron",
        schedule_kind="cron",
        cron_expression="*/5 * * * *",
    )
    event = plan(client, admin_headers, data, name="事件", schedule_kind="event")
    assert cron["next_run_at"] and manual["next_run_at"] is None and event["next_run_at"] is None
    with SessionLocal() as db:
        plans.schedule_due(db, now)
        plans.schedule_due(db, now)
        assert [str(r.plan_id) for r in db.scalars(select(InspectionRun))] == [once["id"]]
        assert db.get(InspectionPlan, UUID(once["id"])).next_run_at is None
    drain()
    with SessionLocal() as db:
        plans.schedule_due(db, now)
        assert len(db.scalars(select(InspectionRun)).all()) == 1
    response = client.post(
        f"/api/v1/inspection-plans/{event['id']}/trigger",
        headers=admin_headers,
        json={"kind": "event"},
    )
    assert response.status_code == 202 and response.json()["trigger"] == "event"
    assert (
        client.post(
            f"/api/v1/inspection-plans/{manual['id']}/trigger",
            headers=admin_headers,
            json={"kind": "event"},
        ).status_code
        == 409
    )
    drain()
    scan = client.post(
        f"/api/v1/inspection-plans/{manual['id']}/trigger",
        headers=admin_headers,
        json={"kind": "scan"},
    )
    assert scan.status_code == 202 and scan.json()["trigger"] == "scan"


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("0 9 * * 1-5", "2026-10-27T01:00:00+00:00"),
        ("*/15 * * * *", "2026-10-27T00:15:00+00:00"),
        ("0 0 1 * *", "2026-10-31T16:00:00+00:00"),
    ],
)
def test_cron_timezone_and_boundaries(expression, expected):
    assert (
        scheduling.next_cron(
            expression, "Asia/Shanghai", datetime(2026, 10, 27, tzinfo=UTC)
        ).isoformat()
        == expected
    )


def test_source_failure_retry_and_timeout_are_distinct(client, admin_headers, source):
    source["data"] = {"data": [{"id": "one", "latency": 12}]}
    data, _ = dataset(client, admin_headers, source)
    value = plan(client, admin_headers, data, max_retries=1, timeout_seconds=10)
    source["status"] = 503
    queued = client.post(
        f"/api/v1/inspection-plans/{value['id']}/runs", headers=admin_headers
    ).json()
    runtime.run_once()
    with SessionLocal() as db:
        row = db.get(InspectionRun, UUID(queued["id"]))
        assert row.retry_count == 1 and row.error_stage == "collection"
        row.retry_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    source["status"] = 200
    drain()
    completed = client.get(f"/api/v1/inspection-runs/{queued['id']}", headers=admin_headers).json()
    assert completed["status"] == "completed" and completed["retry_count"] == 1
    queued = client.post(
        f"/api/v1/inspection-plans/{value['id']}/runs", headers=admin_headers
    ).json()
    with SessionLocal() as db:
        row = db.get(InspectionRun, UUID(queued["id"]))
        row.started_at = datetime.now(UTC) - timedelta(seconds=11)
        db.commit()
    drain()
    failed = client.get(f"/api/v1/inspection-runs/{queued['id']}", headers=admin_headers).json()
    assert failed["status"] == "failed" and failed["error_stage"] == "timeout"


def test_source_change_during_read_discards_evidence(client, admin_headers, source):
    source["data"] = {"data": [{"id": "one", "latency": 12}]}
    data, _ = dataset(client, admin_headers, source)
    value = plan(client, admin_headers, data)

    def change():
        with SessionLocal() as db:
            row = db.get(InspectionDataSource, UUID(data["id"]))
            row.version += 1
            db.commit()

    source["hook"] = change
    run = execute(client, admin_headers, value)
    assert run["status"] == "failed" and run["error_code"] == "inspection_source_changed"
    with SessionLocal() as db:
        assert not db.scalar(select(InspectionItem))


def test_source_credentials_clear_on_url_change_and_version_conflicts(
    client, admin_headers, source
):
    data, body = dataset(client, admin_headers, source)
    path = f"/api/v1/inspection-data-sources/{data['id']}"
    changed = {
        **body,
        "headers": None,
        "expected_version": 1,
        "source_config": {**body["source_config"], "url": source["url"] + "/new"},
    }
    updated = client.put(path, headers=admin_headers, json=changed)
    assert updated.status_code == 200 and not updated.json()["headers_configured"]
    assert client.put(path, headers=admin_headers, json=changed).status_code == 409
    assert client.delete(path, headers=admin_headers).status_code == 204
    assert client.post(path + "/test", headers=admin_headers).status_code == 404


@pytest.mark.parametrize("role", ["viewer", "operator"])
def test_source_mutations_require_manage_permission(
    client, admin_headers, source, make_user, login, role
):
    data, body = dataset(client, admin_headers, source)
    make_user(role, role)
    headers = {"Authorization": "Bearer " + login(role)["access_token"]}
    assert client.get("/api/v1/inspection-data-sources", headers=headers).status_code == 200
    assert (
        client.post("/api/v1/inspection-data-sources", headers=headers, json=body).status_code
        == 403
    )

    assert (
        client.post(
            f"/api/v1/inspection-data-sources/{data['id']}/test", headers=headers
        ).status_code
        == 403
    )


def test_upgrade_keeps_legacy_credentials_evidence_and_history(
    client, admin_headers, source, migrated_database
):
    from alembic import command

    source["data"] = {"data": [{"id": "existing", "latency": 500}]}
    data, _ = dataset(client, admin_headers, source)
    value = plan(client, admin_headers, data, schedule_kind="interval")
    executed = execute(client, admin_headers, value)
    with SessionLocal() as db:
        before = db.scalar(select(InspectionItem))
        original = (before.id, before.evidence, before.checks, before.created_at)
        headers = db.get(InspectionPlan, UUID(value["id"])).encrypted_headers
        snapshot = db.get(InspectionRun, UUID(executed["id"])).snapshot
    try:
        command.downgrade(migrated_database, "0034_scoped_access")
    finally:
        command.upgrade(migrated_database, "head")
    with SessionLocal() as db:
        migrated = db.get(InspectionPlan, UUID(value["id"]))
        reused = db.get(InspectionDataSource, migrated.data_source_id)
        after = db.scalar(select(InspectionItem))
        assert reused.encrypted_headers == headers == migrated.encrypted_headers
        assert (after.id, after.evidence, after.checks, after.created_at) == original
        assert after.rule_verdict == "fail"
        assert db.get(InspectionRun, UUID(executed["id"])).snapshot == snapshot
    assert client.get("/health/ready").status_code == 200


@pytest.mark.parametrize(
    "value,expected",
    [("2", "pass"), (2, "pass"), (2.0, "pass"), (True, "fail"), ("bad", "unknown")],
)
def test_numeric_equality_accepts_database_numeric_text(value, expected):
    checks, _ = check(
        {"facts": {"retries": value}},
        [
            {
                "code": "value",
                "title": "重试次数",
                "path": "retries",
                "kind": "equals",
                "expected": 2,
                "basis": "等于 2",
            }
        ],
    )
    assert checks[0]["status"] == expected
