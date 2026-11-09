import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from sqlalchemy import select, text

from app.db.session import SessionLocal, engine
from app.integrations.diagnostic_gateway import DiagnosticGateway
from app.models import (
    Application,
    InspectionItem,
    InspectionPlan,
    InspectionReview,
    InspectionRun,
    KnowledgeSkill,
    OperationsAuditLog,
    User,
)
from app.services.inspections import agent, plans, runtime
from app.services.inspections.checkers import check


@pytest.fixture
def source():
    state = {"calls": [], "status": 200, "data": {"items": [], "next_cursor": None}}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["calls"].append((self.command, parse_qs(urlsplit(self.path).query)))
            state.setdefault("headers", []).append(dict(self.headers))
            if state.get("hook"):
                state["hook"]()
            body = state["data"](self.path) if callable(state["data"]) else state["data"]
            raw = json.dumps(body).encode()
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{server.server_port}/records"
    yield state
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def record(identifier="cert-1", error="0.04", decision="pass"):
    return {
        "id": identifier,
        "version": "1",
        "updated_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        "facts": {"error": error, "unit": "mm"},
        "original_verdict": decision,
        "evidence": [{"page": 2, "text": "已核对的证书内页测试片段"}],
    }


def rule(**values):
    return {
        "code": "error",
        "title": "示值误差",
        "kind": "numeric_range",
        "path": "error",
        "minimum": "-0.05",
        "maximum": "0.05",
        "unit": "mm",
        "unit_path": "unit",
        "basis": "隔离样例规则 v1，仅用于验证",
        **values,
    }


def create(client, auth_headers, source, **values):
    with SessionLocal() as db:
        app = Application(code="inspection-app", name="Inspection business", enabled=True)
        db.add(app)
        db.commit()
        application_id = str(app.id)
    body = {
        "name": "Hourly",
        "application_id": application_id,
        "environment": "test",
        "enabled": True,
        "source_kind": "api",
        "source_config": {"url": source["url"]},
        "rules": [rule()],
        **values,
    }
    response = client.post("/api/v1/inspection-plans", headers=auth_headers, json=body)
    assert response.status_code == 201, response.text
    return response.json(), body


def drain():
    for _ in range(30):
        if not runtime.run_once():
            return
    pytest.fail("Inspection did not finish within bounded worker iterations")


def enqueue(client, headers, plan):
    response = client.post(f"/api/v1/inspection-plans/{plan['id']}/runs", headers=headers)
    assert response.status_code == 202, response.text
    return response.json()


def test_summary_global_totals_per_run_and_soft_deletion(client, admin_headers, source):
    path = "/api/v1/inspections/summary"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=admin_headers).json() == {
        "runs_total": 0,
        "runs_active": 0,
        "items_total": 0,
        "issues_total": 0,
        "services_covered": 0,
        "plans_enabled": 0,
    }
    source["data"]["items"] = [record("bad", "0.09"), record("good")]
    plan, body = create(client, admin_headers, source)
    first = enqueue(client, admin_headers, plan)
    drain()
    enqueue(client, admin_headers, plan)
    drain()
    paused = client.post(
        "/api/v1/inspection-plans",
        headers=admin_headers,
        json={**body, "name": "Paused plan", "enabled": False},
    )
    assert paused.status_code == 201
    with SessionLocal() as db:
        other_service = Application(code="inspection-other", name="Other service", enabled=True)
        db.add(other_service)
        now = datetime.now(UTC)
        db.add_all(
            InspectionRun(
                plan_id=UUID(plan["id"]),
                plan_version=1,
                requested_by=UUID(plan["configured_by"]),
                trigger="manual",
                status="completed",
                snapshot={},
                window_start=now,
                window_end=now,
            )
            for _ in range(24)
        )
        db.commit()
        other_id = str(other_service.id)
    other_plan = client.post(
        "/api/v1/inspection-plans",
        headers=admin_headers,
        json={**body, "application_id": other_id, "name": "Other plan"},
    )
    assert other_plan.status_code == 201
    enqueue(client, admin_headers, other_plan.json())
    listing = client.get("/api/v1/inspection-runs?limit=20", headers=admin_headers)
    assert len(listing.json()) == 20
    expected = {
        "runs_total": 27,
        "runs_active": 1,
        "items_total": 4,
        "issues_total": 2,
        "services_covered": 2,
        "plans_enabled": 2,
    }
    assert client.get(path, headers=admin_headers).json() == expected
    assert (
        client.delete(
            f"/api/v1/inspection-plans/{plan['id']}?expected_version=1", headers=admin_headers
        ).status_code
        == 204
    )
    expected["plans_enabled"] = 1
    assert client.get(path, headers=admin_headers).json() == expected
    with SessionLocal() as db:
        item = db.scalar(select(InspectionItem).where(InspectionItem.verdict == "contradicted"))
        item.deleted_at = datetime.now(UTC)
        db.commit()
    expected.update(items_total=3, issues_total=1)
    assert client.get(path, headers=admin_headers).json() == expected
    with SessionLocal() as db:
        db.get(InspectionRun, UUID(first["id"])).deleted_at = datetime.now(UTC)
        db.get(InspectionPlan, UUID(paused.json()["id"])).deleted_at = datetime.now(UTC)
        db.get(Application, UUID(other_id)).deleted_at = datetime.now(UTC)
        db.commit()
    expected.update(
        runs_total=26, items_total=2, issues_total=1, services_covered=0, plans_enabled=0
    )
    assert client.get(path, headers=admin_headers).json() == expected


def test_automatic_schedule_readonly_results_and_batch_history(client, admin_headers, source):
    source["data"]["items"] = [
        record(),
        record("cert-2", "0.07", "pass"),
        record("cert-3", "0.01", "fail"),
    ]
    plan, _ = create(
        client, admin_headers, source, headers={"Authorization": "Bearer sample-secret"}
    )
    assert plan["headers_configured"] and "sample-secret" not in json.dumps(plan)
    with SessionLocal() as db:
        plans.schedule_due(db)
        plans.schedule_due(db)
        rows = db.scalars(select(InspectionRun)).all()
        assert len(rows) == 1 and rows[0].trigger == "automatic"
        run_id = str(rows[0].id)
        encrypted = db.get(InspectionPlan, UUID(plan["id"])).encrypted_headers
        assert encrypted and "sample-secret" not in encrypted
    drain()
    run = client.get(f"/api/v1/inspection-runs/{run_id}", headers=admin_headers).json()
    assert run["status"] == "completed"
    assert run["summary"]["counts"] == {"supported": 1, "contradicted": 2}
    assert all(method == "GET" for method, _ in source["calls"])
    repeated = enqueue(client, admin_headers, plan)
    drain()
    run = client.get(f"/api/v1/inspection-runs/{repeated['id']}", headers=admin_headers).json()
    assert run["summary"]["unique_records"] == 3
    assert run["summary"]["duplicate_records"] == 0


@pytest.mark.parametrize(
    "header,value",
    [
        ("Authorization", "Bearer fixture-token"),
        ("Authorization", "Basic dXNlcjpwYXNz"),
        ("X-API-Key", "fixture-api-key"),
    ],
)
def test_api_auth_is_sent_preserved_and_cleared(client, admin_headers, source, header, value):
    plan, body = create(client, admin_headers, source, headers={header: value})
    enqueue(client, admin_headers, plan)
    drain()
    assert source["headers"][-1][header] == value
    assert value not in json.dumps(plan)
    body.pop("headers")
    body["expected_version"] = plan["version"]
    updated = client.put(f"/api/v1/inspection-plans/{plan['id']}", headers=admin_headers, json=body)
    assert updated.status_code == 200, updated.text
    assert updated.json()["headers_configured"]
    enqueue(client, admin_headers, updated.json())
    drain()
    assert source["headers"][-1][header] == value
    body["expected_version"] = updated.json()["version"]
    body["headers"] = {}
    cleared = client.put(f"/api/v1/inspection-plans/{plan['id']}", headers=admin_headers, json=body)
    assert cleared.status_code == 200, cleared.text
    assert not cleared.json()["headers_configured"]
    enqueue(client, admin_headers, cleared.json())
    drain()
    assert header not in source["headers"][-1]


def test_delete_result_keeps_run_other_results_and_review_evidence(client, admin_headers, source):
    source["data"]["items"] = [record("keep"), record("remove", error="0.08")]
    plan, _ = create(client, admin_headers, source)
    run = enqueue(client, admin_headers, plan)
    drain()
    items = client.get(f"/api/v1/inspection-runs/{run['id']}/items", headers=admin_headers).json()
    removed = next(i for i in items if i["record_id"] == "remove")
    kept = next(i for i in items if i["record_id"] == "keep")
    path = f"/api/v1/inspection-items/{removed['id']}"
    review = client.post(
        path + "/reviews",
        headers=admin_headers,
        json={"verdict": "original_correct", "comment": "保留复核依据"},
    )
    assert review.status_code == 201
    assert client.delete(path, headers=admin_headers).status_code == 204
    assert client.delete(path, headers=admin_headers).status_code == 404
    assert client.get(path, headers=admin_headers).status_code == 404
    assert client.get(path + "/reviews", headers=admin_headers).status_code == 404
    assert (
        client.post(
            path + "/reviews",
            headers=admin_headers,
            json={
                "verdict": "original_correct",
                "comment": "不能复核已删除结果",
            },
        ).status_code
        == 404
    )
    assert (
        client.get(f"/api/v1/inspection-runs/{run['id']}", headers=admin_headers).status_code == 200
    )
    assert [
        i["id"]
        for i in client.get(
            f"/api/v1/inspection-runs/{run['id']}/items",
            headers=admin_headers,
        ).json()
    ] == [kept["id"]]
    assert [
        i["id"] for i in client.get("/api/v1/inspection-items", headers=admin_headers).json()
    ] == [kept["id"]]
    summary = client.get("/api/v1/inspections/summary", headers=admin_headers).json()
    assert (
        summary["runs_total"] == 1 and summary["items_total"] == 1 and summary["issues_total"] == 0
    )
    with SessionLocal() as db:
        row = db.get(InspectionItem, UUID(removed["id"]))
        assert row.deleted_at and row.updated_at >= row.deleted_at
        assert row.evidence["facts"]["error"] == "0.08"
        assert db.get(InspectionReview, UUID(review.json()["id"])).deleted_at is None
        assert db.scalar(
            select(OperationsAuditLog).where(
                OperationsAuditLog.action == "inspection_item_deleted",
                OperationsAuditLog.resource_id == row.id,
            )
        )
    enqueue(client, admin_headers, plan)
    drain()
    # A new batch records fresh observations, including previously removed objects.
    latest = client.get("/api/v1/inspection-items", headers=admin_headers).json()
    assert {i["record_id"] for i in latest} == {"remove", "keep"}
    assert all(i["id"] != removed["id"] for i in latest)


def test_delete_run_hides_its_results_and_preserves_plan_other_runs(client, admin_headers, source):
    source["data"]["items"] = [record("first"), record("second")]
    plan, _ = create(client, admin_headers, source)
    first = enqueue(client, admin_headers, plan)
    drain()
    first_items = client.get(
        f"/api/v1/inspection-runs/{first['id']}/items", headers=admin_headers
    ).json()
    source["data"]["items"] = [{**record("first"), "version": "2"}]
    second = enqueue(client, admin_headers, plan)
    drain()
    path = f"/api/v1/inspection-runs/{first['id']}"
    assert client.delete(path, headers=admin_headers).status_code == 204
    assert client.delete(path, headers=admin_headers).status_code == 404
    assert client.get(path, headers=admin_headers).status_code == 404
    assert client.get(path + "/items", headers=admin_headers).status_code == 404
    for item in first_items:
        assert (
            client.get(f"/api/v1/inspection-items/{item['id']}", headers=admin_headers).status_code
            == 404
        )
    assert [
        r["id"] for r in client.get("/api/v1/inspection-runs", headers=admin_headers).json()
    ] == [second["id"]]
    remaining = client.get(
        "/api/v1/inspection-items?latest_only=false", headers=admin_headers
    ).json()
    assert len(remaining) == 1 and remaining[0]["run_id"] == second["id"]
    summary = client.get("/api/v1/inspections/summary", headers=admin_headers).json()
    assert summary["runs_total"] == 1 and summary["items_total"] == 1
    with SessionLocal() as db:
        deleted = db.get(InspectionRun, UUID(first["id"]))
        assert deleted.deleted_at and deleted.updated_at >= deleted.deleted_at
        assert all(db.get(InspectionItem, UUID(i["id"])).deleted_at for i in first_items)
        assert db.get(InspectionRun, UUID(second["id"])).deleted_at is None
        assert db.get(InspectionPlan, UUID(plan["id"])).deleted_at is None
        log = db.scalar(
            select(OperationsAuditLog).where(
                OperationsAuditLog.action == "inspection_run_deleted",
                OperationsAuditLog.resource_id == deleted.id,
            )
        )
        assert log and log.detail["items"] == 2
    assert (
        client.post(
            f"/api/v1/inspection-plans/{plan['id']}/runs", headers=admin_headers
        ).status_code
        == 202
    )


@pytest.mark.parametrize("status", ["queued", "collecting", "evaluating"])
def test_active_runs_and_results_require_stopping_before_deletion(
    client, admin_headers, source, status
):
    source["data"]["items"] = [record()]
    plan, _ = create(client, admin_headers, source)
    run = enqueue(client, admin_headers, plan)
    drain()
    item = client.get(f"/api/v1/inspection-runs/{run['id']}/items", headers=admin_headers).json()[0]
    with SessionLocal() as db:
        db.get(InspectionRun, UUID(run["id"])).status = status
        db.commit()
    assert (
        client.delete(f"/api/v1/inspection-runs/{run['id']}", headers=admin_headers).status_code
        == 409
    )
    assert (
        client.delete(f"/api/v1/inspection-items/{item['id']}", headers=admin_headers).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/inspection-runs/{run['id']}/cancel", headers=admin_headers
        ).status_code
        == 200
    )
    assert (
        client.delete(f"/api/v1/inspection-runs/{run['id']}", headers=admin_headers).status_code
        == 204
    )


@pytest.mark.parametrize("role", ["viewer", "operator"])
def test_deletion_requires_manage_permission(client, admin_headers, make_user, login, source, role):
    source["data"]["items"] = [record()]
    plan, _ = create(client, admin_headers, source)
    run = enqueue(client, admin_headers, plan)
    drain()
    item = client.get(f"/api/v1/inspection-runs/{run['id']}/items", headers=admin_headers).json()[0]
    make_user(role, role)
    headers = {"Authorization": "Bearer " + login(role)["access_token"]}
    for path in [f"/api/v1/inspection-runs/{run['id']}", f"/api/v1/inspection-items/{item['id']}"]:
        assert client.delete(path).status_code == 401
        assert client.delete(path, headers=headers).status_code == 403
        assert client.get(path, headers=admin_headers).status_code == 200


def test_deleting_latest_result_exposes_previous_undeleted_version(client, admin_headers, source):
    source["data"]["items"] = [record()]
    plan, _ = create(client, admin_headers, source)
    enqueue(client, admin_headers, plan)
    drain()
    source["data"]["items"] = [{**record(), "version": "2"}]
    run = enqueue(client, admin_headers, plan)
    drain()
    latest = client.get("/api/v1/inspection-items", headers=admin_headers).json()[0]
    assert latest["run_id"] == run["id"]
    assert (
        client.delete(f"/api/v1/inspection-items/{latest['id']}", headers=admin_headers).status_code
        == 204
    )
    remaining = client.get("/api/v1/inspection-items", headers=admin_headers).json()
    assert len(remaining) == 1 and remaining[0]["record_version"] == "1"


def test_rbac_pause_and_append_only_reviews(client, admin_headers, make_user, login, source):
    source["data"]["items"] = [record("bad", "0.09")]
    plan, body = create(client, admin_headers, source)
    make_user("viewer", "viewer")
    viewer = {"Authorization": "Bearer " + login("viewer")["access_token"]}
    assert client.get("/api/v1/inspections/summary", headers=viewer).status_code == 200
    make_user("operator", "operator")
    operator = {"Authorization": "Bearer " + login("operator")["access_token"]}
    plan_list = client.get("/api/v1/inspection-plans", headers=viewer)
    assert plan_list.status_code == 200
    assert plan_list.json()[0]["application_name"] == "Inspection business"
    assert client.post("/api/v1/inspection-plans", headers=operator, json=body).status_code == 403
    assert (
        client.post(f"/api/v1/inspection-plans/{plan['id']}/runs", headers=viewer).status_code
        == 403
    )
    run = enqueue(client, operator, plan)
    assert enqueue(client, operator, plan)["id"] == run["id"]
    drain()
    item = client.get(f"/api/v1/inspection-runs/{run['id']}/items", headers=viewer).json()[0]
    path = f"/api/v1/inspection-items/{item['id']}/reviews"
    body = {"verdict": "false_accept", "comment": "人工复算确认测试限值超出"}
    assert client.post(path, headers=viewer, json=body).status_code == 403
    assert client.post(path, headers=operator, json=body).status_code == 201
    assert (
        client.post(path, headers=operator, json={**body, "comment": "追加证据"}).status_code == 201
    )
    assert len(client.get(path, headers=viewer).json()) == 2
    assert client.put(path, headers=operator, json=body).status_code == 405
    paused = client.put(
        f"/api/v1/inspection-plans/{plan['id']}/state",
        headers=admin_headers,
        json={"enabled": False, "expected_version": plan["version"]},
    )
    assert paused.status_code == 200
    path = f"/api/v1/inspection-plans/{plan['id']}"
    assert client.post(path + "/runs", headers=viewer).status_code == 403
    assert (
        client.post(path + "/trigger", headers=operator, json={"kind": "scan"}).status_code == 409
    )
    with SessionLocal() as db:
        plans.schedule_due(db)
        assert len(db.scalars(select(InspectionRun)).all()) == 1
    manual = enqueue(client, operator, plan)
    assert manual["trigger"] == "manual"
    assert enqueue(client, operator, plan)["id"] == manual["id"]
    drain()
    assert (
        client.get(f"/api/v1/inspection-runs/{manual['id']}", headers=operator).json()["status"]
        == "completed"
    )
    detail = client.get(path, headers=operator).json()
    assert not detail["enabled"] and detail["next_run_at"] is None
    assert (
        client.put(
            f"/api/v1/inspection-plans/{plan['id']}/state",
            headers=admin_headers,
            json={"enabled": True, "expected_version": 1},
        ).status_code
        == 409
    )


def test_pause_during_fetch_drops_evidence(client, admin_headers, source):
    source["data"]["items"] = [record()]
    plan, _ = create(client, admin_headers, source)
    run = enqueue(client, admin_headers, plan)

    def pause():
        with SessionLocal() as db:
            row = db.get(InspectionPlan, UUID(plan["id"]))
            row.enabled = False
            row.version += 1
            db.commit()

    source["hook"] = pause
    drain()
    result = client.get(f"/api/v1/inspection-runs/{run['id']}", headers=admin_headers).json()
    assert result["status"] == "failed" and result["error_code"] == "inspection_plan_changed"
    assert (
        client.get(f"/api/v1/inspection-runs/{run['id']}/items", headers=admin_headers).json() == []
    )


def test_admin_revocation_suspends_automatic_access(client, admin_headers, source):
    plan, _ = create(client, admin_headers, source)
    with SessionLocal() as db:
        owner = db.get(User, UUID(plan["configured_by"]))
        owner.is_active = False
        db.commit()
        plans.schedule_due(db)
        assert db.get(InspectionPlan, UUID(plan["id"])).enabled is False
        assert db.scalars(select(InspectionRun)).all() == []
    assert source["calls"] == []


def test_concurrent_starts_share_one_active_run(client, admin_headers, source):
    plan, _ = create(client, admin_headers, source)

    def start():
        with SessionLocal() as db:
            return plans.run_now(db, UUID(plan["id"]), db.get(User, UUID(plan["configured_by"]))).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        identifiers = list(pool.map(lambda _: start(), range(2)))
    assert identifiers[0] == identifiers[1]


def test_cancel_blocks_provider_before_model_request(client, admin_headers, source):
    from fastapi.testclient import TestClient

    plan, _ = create(client, admin_headers, source)
    run = enqueue(client, admin_headers, plan)
    tools = agent.InspectionTools(UUID(run["id"]), UUID(run["id"]))
    gateway = DiagnosticGateway(
        tools,
        expected_config=agent.runtime_config(),
        config_provider=agent.runtime_config,
        request_guard=tools.guard,
    )
    client.post(
        f"/api/v1/inspection-runs/{run['id']}/cancel",
        headers=admin_headers,
    )
    with TestClient(gateway.app()) as transport:
        response = transport.post(
            "/chat/completions", json={}, headers={"Authorization": "Bearer " + gateway.token}
        )
    assert response.status_code == 409 and gateway.requests == 0


def test_worker_restart_does_not_repeat_inflight_model(client, admin_headers, source):
    plan, _ = create(client, admin_headers, source)
    run = enqueue(client, admin_headers, plan)
    source["data"]["items"] = [record()]
    assert runtime.run_once()
    with SessionLocal() as db:
        item = db.scalar(select(InspectionItem))
        item.agent_status = "running"
        db.commit()
    drain()
    result = client.get(f"/api/v1/inspection-runs/{run['id']}", headers=admin_headers).json()
    assert result["status"] == "completed" and result["error_stage"] == "agent"
    with SessionLocal() as db:
        item = db.scalar(select(InspectionItem))
        assert item.agent_status == "failed"
        assert item.agent_result["error_code"] == "inspection_agent_interrupted"


def test_budget_continues_same_window_and_cursor(client, admin_headers, source):
    first, second = record("first"), record("second")
    source["data"] = lambda path: {
        "items": [second] if "cursor=next" in path else [first],
        "next_cursor": None if "cursor=next" in path else "next",
    }
    plan, _ = create(client, admin_headers, source, max_records=1)
    first_run = enqueue(client, admin_headers, plan)
    drain()
    result = client.get(f"/api/v1/inspection-runs/{first_run['id']}", headers=admin_headers).json()
    assert result["status"] == "partial" and result["error_code"] == "record_budget_exhausted"
    second_run = enqueue(client, admin_headers, plan)
    assert second_run["window_start"] == first_run["window_start"]
    assert second_run["window_end"] == first_run["window_end"]
    drain()
    result = client.get(f"/api/v1/inspection-runs/{second_run['id']}", headers=admin_headers).json()
    assert result["status"] == "completed"
    assert source["calls"][-1][1]["cursor"] == ["next"]


@pytest.mark.parametrize("fault", ["status", "cursor", "window", "large_record"])
def test_bad_source_never_advances_watermark(client, admin_headers, source, fault):
    item = record()
    if fault == "status":
        source["status"] = 302
    elif fault == "cursor":
        source["data"]["next_cursor"] = "next"
    elif fault == "window":
        item["updated_at"] = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        source["data"]["items"] = [item]
    else:
        item["facts"]["oversized"] = "x" * 33000
        source["data"]["items"] = [item]
    plan, _ = create(client, admin_headers, source)
    run = enqueue(client, admin_headers, plan)
    drain()
    assert (
        client.get(f"/api/v1/inspection-runs/{run['id']}", headers=admin_headers).json()["status"]
        == "failed"
    )
    with SessionLocal() as db:
        assert db.get(InspectionPlan, UUID(plan["id"])).watermark is None


def test_agent_budget_preserves_program_contradiction(client, admin_headers, source, monkeypatch):
    with SessionLocal() as db:
        skill = KnowledgeSkill(code="inspection_test", title="Review", content="Only read", tags=[])
        db.add(skill)
        db.commit()
        skill_id = str(skill.id)
    source["data"]["items"] = [record("one", "0.08"), record("two")]
    plan, _ = create(
        client, admin_headers, source, agent_enabled=True, skill_id=skill_id, max_agent_items=1
    )
    monkeypatch.setattr(
        agent,
        "evaluate",
        lambda run_id, item_id: {
            "verdict": "supported",
            "summary": "模型测试意见",
            "evidence_ids": [str(item_id)],
            "data_gaps": [],
        },
    )
    run = enqueue(client, admin_headers, plan)
    drain()
    result = client.get(f"/api/v1/inspection-runs/{run['id']}", headers=admin_headers).json()
    assert result["status"] == "completed" and result["agent_calls"] == 1
    assert result["error_stage"] == "agent"
    items = client.get(f"/api/v1/inspection-runs/{run['id']}/items", headers=admin_headers).json()
    by_record = {item["record_id"]: item for item in items}
    assert by_record["one"]["verdict"] == "contradicted"
    assert by_record["two"]["agent_status"] == "skipped_budget"
    assert by_record["two"]["rule_verdict"] == "pass"
    assert by_record["two"]["final_status"] == "pass"


def test_skill_changed_blocks_queued_run(client, admin_headers, source):
    with SessionLocal() as db:
        skill = KnowledgeSkill(code="inspection_test", title="Review", content="Only read", tags=[])
        db.add(skill)
        db.commit()
        skill_id = str(skill.id)
    plan, _ = create(client, admin_headers, source, agent_enabled=True, skill_id=skill_id)
    run = enqueue(client, admin_headers, plan)
    with SessionLocal() as db:
        db.get(KnowledgeSkill, UUID(skill_id)).version += 1
        db.commit()
    drain()
    assert (
        client.get(f"/api/v1/inspection-runs/{run['id']}", headers=admin_headers).json()[
            "error_code"
        ]
        == "inspection_skill_version_changed"
    )
    assert source["calls"] == []


def test_checker_missing_unit_and_decimal_boundary():
    definition = rule()
    values = record()
    values["facts"]["error"] = "0.050000000000000000001"
    checks, verdict = check(values, [definition])
    assert checks[0]["status"] == "fail" and verdict == "contradicted"
    values["facts"]["error"] = "0.05"
    assert check(values, [definition])[1] == "supported"
    values["facts"]["unit"] = "m"
    assert check(values, [definition])[1] == "unassessable"
    values["facts"] = {}
    assert check(values, [definition])[1] == "unassessable"
    values["facts"] = {"error": True, "unit": "mm"}
    assert check(values, [definition])[1] == "unassessable"


def test_get_contract_rejects_write_methods_and_credentials_in_url(client, admin_headers, source):
    plan, body = create(client, admin_headers, source)
    for url in [source["url"] + "?token=private", "http://user:pass@localhost/read"]:
        invalid = {**body, "source_config": {"url": url}}
        assert (
            client.post("/api/v1/inspection-plans", headers=admin_headers, json=invalid).status_code
            == 422
        )
    body["source_config"]["method"] = "POST"
    assert (
        client.post("/api/v1/inspection-plans", headers=admin_headers, json=body).status_code == 422
    )
    assert source["calls"] == []


def test_new_tables_have_utc_timestamps_and_update_triggers(client, admin_headers, source):
    plan, _ = create(client, admin_headers, source)
    with engine.begin() as connection:
        for name in (
            "inspection_plans",
            "inspection_runs",
            "inspection_items",
            "inspection_reviews",
        ):
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM pg_trigger WHERE tgname=:name"),
                    {"name": f"trg_{name}_updated_at"},
                )
                == 1
            )
        previous = connection.scalar(
            text("SELECT updated_at FROM inspection_plans WHERE id=:id"), {"id": plan["id"]}
        )
        connection.execute(
            text("UPDATE inspection_plans SET name='renamed' WHERE id=:id"), {"id": plan["id"]}
        )
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT updated_at FROM inspection_plans WHERE id=:id"), {"id": plan["id"]}
            )
            >= previous
        )
