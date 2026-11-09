import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest

from app.core.secrets import cipher
from app.db.session import SessionLocal
from app.models import Application, InspectionPlan, KnowledgeSkill
from app.services.inspections import runtime
from app.services.inspections.checkers import check


@pytest.fixture
def dataset():
    at = datetime.now(UTC) - timedelta(hours=2)
    main = {
        "id": "C001",
        "version": "1",
        "updated_at": at.isoformat(),
        "original_verdict": "pass",
        "facts": {"instrument": "A"},
        "evidence": [],
    }
    pages = [
        {
            "id": "P" + str(i),
            "version": "1",
            "updated_at": (at - timedelta(days=60)).isoformat(),
            "facts": {"certificate_id": "C001", "instrument": "A", "error": "0.04", "unit": "mm"},
        }
        for i in range(2)
    ]
    state = {"main": main, "pages": pages, "requests": [], "status": 200}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            state["requests"].append((parsed.path, query))
            related = parsed.path == "/pages"
            if related and state.get("hook"):
                state["hook"]()
            rows = state["pages"] if related else [state["main"]]
            offset, limit = int(query.get("cursor", [0])[0]), int(query["limit"][0])
            selected = rows[offset : offset + limit]
            body = {
                "items": selected,
                "next_cursor": str(offset + limit) if offset + limit < len(rows) else None,
            }
            self.send_response(state["status"] if related else 200)
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{server.server_port}"
    yield state
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def body(data):
    return {
        "name": "Independent objects",
        "environment": "test",
        "enabled": True,
        "source_kind": "api",
        "source_config": {"url": data["url"] + "/main"},
        "related_sources": [
            {
                "name": "pages",
                "source_kind": "api",
                "source_config": {"url": data["url"] + "/pages"},
                "match_field": "certificate_id",
                "cardinality": "many",
            }
        ],
        "rules": [
            {
                "code": "range",
                "title": "Each point",
                "kind": "numeric_range",
                "path": "pages.*.error",
                "maximum": "0.05",
                "unit": "mm",
                "unit_path": "pages.*.unit",
                "basis": "Fixture limit v1",
            },
            {
                "code": "instrument",
                "title": "Same instrument",
                "kind": "field_equals",
                "path": "pages.*.instrument",
                "compare_path": "main.instrument",
                "basis": "Same object",
            },
        ],
    }


def create(client, headers, config):
    response = client.post("/api/v1/inspection-plans", headers=headers, json=config)
    assert response.status_code == 201, response.text
    return response.json()


def inspect(client, headers, plan):
    response = client.post(f"/api/v1/inspection-plans/{plan['id']}/runs", headers=headers)
    assert response.status_code == 202, response.text
    run = response.json()
    for _ in range(15):
        if not runtime.run_once():
            break
    detail = client.get(f"/api/v1/inspection-runs/{run['id']}", headers=headers).json()
    items = client.get(f"/api/v1/inspection-runs/{run['id']}/items", headers=headers).json()
    return detail, items


def test_independent_join_versions_and_latest_results(client, admin_headers, dataset):
    plan = create(client, admin_headers, body(dataset))
    assert plan["application_id"] is None
    first, items = inspect(client, admin_headers, plan)
    assert first["status"] == "completed" and items[0]["verdict"] == "supported"
    assert len(items[0]["checks"]) == 4
    assert len(items[0]["evidence"]["facts"]["_inspection"]["sources"]) == 3
    lookups = [query for path, query in dataset["requests"] if path == "/pages"]
    assert lookups[0]["match_value"] == ["C001"] and "updated_after" not in lookups[0]
    dataset["pages"][1]["facts"]["error"] = "0.09"
    dataset["pages"][1]["version"] = "2"
    second, changed = inspect(client, admin_headers, plan)
    assert second["status"] == "completed" and changed[0]["verdict"] == "contradicted"
    assert items[0]["record_version"] != changed[0]["record_version"]
    latest = client.get("/api/v1/inspection-items?record_id=C001", headers=admin_headers).json()
    assert [row["id"] for row in latest] == [changed[0]["id"]]
    assert (
        len(client.get("/api/v1/inspection-items?latest_only=false", headers=admin_headers).json())
        == 2
    )
    assert (
        client.get("/api/v1/inspection-items?verdict=supported", headers=admin_headers).json() == []
    )
    assert client.get("/api/v1/inspection-items?record_id=%25", headers=admin_headers).json() == []
    third, duplicates = inspect(client, admin_headers, plan)
    assert len(duplicates) == 1 and third["summary"]["duplicate_records"] == 0
    assert duplicates[0]["id"] != changed[0]["id"]
    summary = client.get("/api/v1/inspections/summary", headers=admin_headers).json()
    assert summary["plans_enabled"] == 1 and summary["services_covered"] == 0


@pytest.mark.parametrize(
    "problem", ["missing", "duplicate", "wrong_key", "missing_key", "unavailable", "limit"]
)
def test_incomplete_joins_never_pass(client, admin_headers, dataset, problem):
    config = body(dataset)
    if problem == "missing":
        dataset["pages"] = []
    elif problem == "duplicate":
        config["related_sources"][0]["cardinality"] = "one"
    elif problem == "wrong_key":
        dataset["pages"][0]["facts"]["certificate_id"] = "OTHER"
    elif problem == "missing_key":
        del dataset["pages"][0]["facts"]["certificate_id"]
    elif problem == "unavailable":
        dataset["status"] = 503
    else:
        config["related_sources"][0]["max_records"] = 1
    plan = create(client, admin_headers, config)
    run, items = inspect(client, admin_headers, plan)
    assert run["status"] == "completed"
    assert items[0]["verdict"] == "unassessable"
    assert items[0]["evidence"]["facts"]["_inspection"]["issues"]
    if problem in {"wrong_key", "missing_key"}:
        assert not items[0]["evidence"]["facts"]["pages"]


def test_join_revocation_discards_results(client, admin_headers, dataset):
    plan = create(client, admin_headers, body(dataset))

    def revoke():
        with SessionLocal() as db:
            row = db.get(InspectionPlan, UUID(plan["id"]))
            row.enabled = False
            row.version += 1
            db.commit()

    dataset["hook"] = revoke
    run, items = inspect(client, admin_headers, plan)
    assert run["status"] == "failed" and run["error_code"] == "inspection_plan_changed"
    assert not items


def test_related_credentials_are_encrypted_and_never_returned(client, admin_headers, dataset):
    config = body(dataset)
    config["related_sources"][0]["headers"] = {"Authorization": "fixture-private-value"}
    plan = create(client, admin_headers, config)
    assert plan["related_sources"][0]["headers_configured"]
    assert "fixture-private-value" not in json.dumps(plan)
    with SessionLocal() as db:
        row = db.get(InspectionPlan, UUID(plan["id"]))
        assert "fixture-private-value" not in json.dumps(row.related_sources)
        assert "fixture-private-value" not in row.encrypted_related_headers
        assert json.loads(cipher().decrypt(row.encrypted_related_headers.encode()))["pages"]
    config["related_sources"][0].pop("headers")
    config["related_sources"][0]["source_config"]["url"] += "/changed"
    config["expected_version"] = plan["version"]
    updated = client.put(
        f"/api/v1/inspection-plans/{plan['id']}", headers=admin_headers, json=config
    )
    assert updated.status_code == 200, updated.text
    assert not updated.json()["related_sources"][0]["headers_configured"]


def test_custom_query_parameters_reach_main_and_related_pages(client, admin_headers, dataset):
    config = body(dataset)
    config["source_config"]["query_params"] = [{"name": "status", "value": "已完成 & ready"}]
    config["related_sources"][0]["source_config"]["query_params"] = [
        {"name": "tag", "value": "a"},
        {"name": "tag", "value": "b"},
    ]
    plan = create(client, admin_headers, config)
    assert plan["source_config"]["query_params"] == config["source_config"]["query_params"]
    run, items = inspect(client, admin_headers, plan)
    assert run["status"] == "completed"
    assert items
    for path, params in dataset["requests"]:
        if path == "/main":
            assert params["status"] == ["已完成 & ready"]
            assert "updated_after" in params and "updated_before" in params
        else:
            assert params["tag"] == ["a", "b"]
            assert params["match_value"] == ["C001"]
            assert "updated_after" not in params


@pytest.mark.parametrize(
    "name",
    [
        "limit",
        "cursor",
        "updated_after",
        "updated_before",
        "match_field",
        "match_value",
        "LIMIT",
        "access_token",
        "api_key",
        "password",
        "bad\nname",
    ],
)
def test_invalid_custom_query_parameters_rejected(client, admin_headers, dataset, name):
    config = body(dataset)
    config["source_config"]["query_params"] = [{"name": name, "value": "fixture"}]
    assert (
        client.post("/api/v1/inspection-plans", headers=admin_headers, json=config).status_code
        == 422
    )


def test_duplicate_alias_and_scoped_skill_rejected(client, admin_headers, dataset):
    config = body(dataset)
    config["related_sources"].append(deepcopy(config["related_sources"][0]))
    assert (
        client.post("/api/v1/inspection-plans", headers=admin_headers, json=config).status_code
        == 422
    )
    config["related_sources"][0]["name"] = "main"
    assert (
        client.post("/api/v1/inspection-plans", headers=admin_headers, json=config).status_code
        == 422
    )
    assert client.get("/api/v1/inspection-items").status_code == 401
    assert client.get("/api/v1/inspection-connections").status_code == 401
    with SessionLocal() as db:
        app = Application(code="scoped-skill", name="Scoped", enabled=True)
        db.add(app)
        db.flush()
        skill = KnowledgeSkill(
            code="scoped", title="Scoped", content="Fixture", application_ids=[str(app.id)]
        )
        db.add(skill)
        db.commit()
        identifier = str(skill.id)
    config = body(dataset)
    config.update(agent_enabled=True, skill_id=identifier)
    assert (
        client.post("/api/v1/inspection-plans", headers=admin_headers, json=config).status_code
        == 409
    )


def test_wildcard_checks_keep_missing_and_failed_points_separate():
    record = {"original_verdict": "pass", "facts": {"pages": [{}, {"error": "0.09"}]}}
    rules = [
        {
            "code": "range",
            "title": "Each point",
            "kind": "numeric_range",
            "path": "pages.*.error",
            "maximum": "0.05",
            "basis": "Fixture v1",
        }
    ]
    checks, verdict = check(record, rules)
    assert [row["path"] for row in checks] == ["pages.0.error", "pages.1.error"]
    assert [row["status"] for row in checks] == ["unknown", "fail"]
    assert verdict == "contradicted"
    record["facts"] = {"left": None, "right": None}
    checks, verdict = check(
        record,
        [
            {
                "code": "same",
                "title": "Same identifier",
                "kind": "field_equals",
                "path": "left",
                "compare_path": "right",
                "basis": "Same object",
            }
        ],
    )
    assert checks[0]["status"] == "unknown" and verdict == "unassessable"
