"""Stage-one contracts: mixed verdicts, durable coverage and task-scoped Agent tools."""

from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from sqlglot import exp
from test_inspections import drain
from test_inspections import source as source
from test_service_databases import application as application
from test_service_databases import create as create_database
from test_service_databases import enable as enable_database
from test_service_databases import payload as payload

from app.db.session import SessionLocal
from app.models import AccessPolicy
from app.schemas.inspections import InspectionSource
from app.services.inspections import check_agent, queries


def condition(**values):
    return {
        "code": "amount",
        "title": "金额",
        "kind": "numeric_range",
        "path": "amount",
        "minimum": 0,
        "basis": "金额非负",
        **values,
    }


def rule_check(**values):
    return {
        "check_id": "amount",
        "name": "金额检查",
        "kind": "rule",
        "conditions": [condition()],
        **values,
    }


def create(client, headers, **values):
    body = {
        "name": "统一巡检",
        "environment": "test",
        "enabled": True,
        "schedule_kind": "manual",
        "object_mode": "fixed",
        "fixed_objects": [{"id": "a", "facts": {"amount": 10}}],
        "check_items": [rule_check()],
        **values,
    }
    response = client.post("/api/v1/inspection-plans", headers=headers, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def execute(client, headers, plan):
    response = client.post(f"/api/v1/inspection-plans/{plan['id']}/runs", headers=headers)
    assert response.status_code == 202, response.text
    drain()
    identifier = response.json()["id"]
    result = client.get(f"/api/v1/inspection-runs/{identifier}", headers=headers)
    assert result.status_code == 200, result.text
    rows = client.get(f"/api/v1/inspection-runs/{identifier}/check-results", headers=headers)
    assert rows.status_code == 200, rows.text
    return result.json(), rows.json()


def agent_check(client, headers):
    response = client.post(
        "/api/v1/skills",
        headers=headers,
        json={"code": "checks_test", "title": "业务校验", "content": "金额需要与材料一致。"},
    )
    assert response.status_code == 201, response.text
    return {
        "check_id": "materials",
        "name": "材料核验",
        "kind": "agent",
        "skill_id": response.json()["id"],
        "requirements": "验证金额和材料",
    }


def test_rules_run_without_model_and_preserve_multiple_checks(client, admin_headers):
    plan = create(
        client,
        admin_headers,
        check_items=[
            rule_check(),
            rule_check(
                check_id="description",
                name="名称检查",
                conditions=[condition(code="name", path="name", kind="required", minimum=None)],
            ),
        ],
    )
    run, rows = execute(client, admin_headers, plan)
    assert run["status"] == "completed"
    assert {r["status"] for r in rows} == {"pass", "unknown"}
    assert run["summary"]["coverage"]["checks_completed"] == 2
    assert run["summary"]["coverage"]["conclusion"] == "unknown"


def test_agent_fail_is_not_hidden_by_passing_rule(client, admin_headers, monkeypatch):
    agent = agent_check(client, admin_headers)

    def inspect(run_id, result_id):
        tools = check_agent.CheckTools(run_id, result_id)
        evidence = tools.call("get_inspection_object", {})
        tools.call(
            "submit_inspection_result",
            {
                "status": "fail",
                "summary": "材料金额不一致",
                "evidence_ids": [evidence["id"]],
                "findings": [{"code": "mismatch", "summary": "差额10元"}],
            },
        )

    monkeypatch.setattr(check_agent, "evaluate", inspect)
    plan = create(client, admin_headers, check_items=[rule_check(), agent])
    run, rows = execute(client, admin_headers, plan)
    assert run["status"] == "completed"
    assert {r["status"] for r in rows} == {"pass", "fail"}
    assert run["summary"]["health_counts"] == {"fail": 1}
    assert run["summary"]["coverage"]["conclusion"] == "fail"
    assert (
        client.get("/api/v1/inspections/summary", headers=admin_headers).json()["issues_total"] == 1
    )


def test_agent_execution_error_is_partial_unknown(client, admin_headers, monkeypatch):
    agent = agent_check(client, admin_headers)

    def fail(*_):
        raise ValueError("inspection_sandbox_unavailable")

    monkeypatch.setattr(check_agent, "evaluate", fail)
    run, rows = execute(
        client, admin_headers, create(client, admin_headers, check_items=[rule_check(), agent])
    )
    assert run["summary"]["coverage"]["checks_errors"] == 1
    assert run["status"] == "partial"
    assert run["summary"]["coverage"]["checks_unfinished"] == 1
    assert run["summary"]["coverage"]["conclusion"] == "unknown"
    assert run["summary"]["health_counts"] == {"unknown": 1}
    assert (
        next(r for r in rows if r["kind"] == "agent")["error_code"]
        == "inspection_sandbox_unavailable"
    )


def test_budget_limit_does_not_report_full_coverage(client, admin_headers):
    run, rows = execute(
        client,
        admin_headers,
        create(
            client,
            admin_headers,
            max_records=1,
            fixed_objects=[
                {"id": "a", "facts": {"amount": 1}},
                {"id": "b", "facts": {"amount": 2}},
            ],
        ),
    )
    assert len(rows) == 1
    assert run["status"] == "partial"
    assert not run["summary"]["coverage"]["discovery_complete"]
    assert run["summary"]["coverage"]["conclusion"] != "pass"


def test_or_condition_uses_three_value_logic(client, admin_headers):
    plan = create(
        client,
        admin_headers,
        check_items=[
            rule_check(
                operator="or", conditions=[condition(), condition(code="missing", path="absent")]
            )
        ],
    )
    run, rows = execute(client, admin_headers, plan)
    assert rows[0]["status"] == "pass"
    assert run["summary"]["coverage"]["conclusion"] == "pass"


def test_dynamic_discovery_zero_objects_does_not_pass(client, admin_headers, source):
    source["data"] = []
    plan = create(
        client,
        admin_headers,
        object_mode="dynamic",
        fixed_objects=[],
        source_config={"url": source["url"], "response_format": "json"},
    )
    run, rows = execute(client, admin_headers, plan)
    assert run["status"] == "completed" and not rows
    assert run["summary"]["coverage"]["no_objects"]
    assert run["summary"]["coverage"]["conclusion"] == "unknown"


def test_query_sql_binds_literals_and_reuses_authorization():
    row = SimpleNamespace(
        engine="postgresql",
        allowed_tables={"public.orders": ["id", "status"]},
        schema_cache={
            "tables": {"public.orders": {"columns": [{"name": "id"}, {"name": "status"}]}}
        },
    )
    source = InspectionSource(
        connection_id=UUID(int=1),
        query_sql="SELECT id, status FROM public.orders WHERE status = :state",
        query_parameters={"state": "x'; DELETE FROM public.orders; --"},
        id_column="id",
        fact_columns=["id", "status"],
    )
    tree, _ = queries.validate(source, row)
    assert len(list(tree.find_all(exp.Select))) == 1
    assert "DELETE" in tree.sql()  # It remains a quoted value, never a statement.
    source.query_sql = "SELECT id, password FROM public.orders"
    source.query_parameters = {}
    with pytest.raises(ValueError):
        queries.validate(source, row)


def test_shared_http_connection_preview_and_reference_protection(client, admin_headers, source):
    source["data"] = [{"id": "a", "amount": 1, "echo": "shared-connection-private"}]
    base = source["url"].removesuffix("/records")
    connection = client.post(
        "/api/v1/inspection-http-connections",
        headers=admin_headers,
        json={
            "name": "业务接口",
            "environment": "test",
            "base_url": base,
            "headers": {"X-API-Key": "shared-connection-private"},
        },
    )
    assert connection.status_code == 201, connection.text
    assert "shared-connection-private" not in connection.text
    query = client.post(
        "/api/v1/inspection-data-sources",
        headers=admin_headers,
        json={
            "name": "对象列表",
            "environment": "test",
            "source_kind": "api",
            "source_config": {
                "http_connection_id": connection.json()["id"],
                "path_template": "records",
                "response_format": "json",
            },
        },
    )
    assert query.status_code == 201, query.text
    preview = client.post(
        f"/api/v1/inspection-data-sources/{query.json()['id']}/preview",
        headers=admin_headers,
        json={"limit": 1},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["records"][0]["facts"]["echo"] == "[REDACTED]"
    assert "shared-connection-private" not in preview.text
    tested = client.post(
        f"/api/v1/inspection-data-sources/{query.json()['id']}/test", headers=admin_headers
    )
    assert tested.status_code == 200 and tested.json()["success"], tested.text
    assert (
        client.delete(
            f"/api/v1/inspection-http-connections/{connection.json()['id']}", headers=admin_headers
        ).status_code
        == 409
    )
    plan = create(
        client,
        admin_headers,
        object_mode="dynamic",
        fixed_objects=[],
        data_source_id=query.json()["id"],
    )
    run, rows = execute(client, admin_headers, plan)
    assert run["status"] == "completed" and rows[0]["status"] == "pass"
    assert source["headers"][-1]["X-API-Key"] == "shared-connection-private"


def test_three_hundred_objects_with_server_pagination(client, admin_headers, source):
    def page(path):
        args = parse_qs(urlsplit(path).query)
        size, number = int(args["page_size"][0]), int(args["page"][0])
        start = (number - 1) * size
        return {
            "data": [{"id": str(i), "amount": i} for i in range(start, min(start + size, 300))],
            "more": start + size < 300,
        }

    source["data"] = page
    plan = create(
        client,
        admin_headers,
        object_mode="dynamic",
        fixed_objects=[],
        max_records=1000,
        source_config={
            "url": source["url"],
            "response_format": "json",
            "items_path": "data",
            "pagination": "page",
            "has_more_path": "more",
        },
    )
    run, rows = execute(client, admin_headers, plan)
    assert run["status"] == "completed", run
    assert run["summary"]["coverage"]["objects_expected"] == 300
    assert run["summary"]["coverage"]["checks_completed"] == 300
    assert len(rows) == 50  # Result endpoint remains paginated.
    assert len(source["calls"]) == 6


def test_dynamic_objects_require_stable_ids(client, admin_headers, source):
    source["data"] = [{"amount": 1}]
    plan = create(
        client,
        admin_headers,
        object_mode="dynamic",
        fixed_objects=[],
        source_config={"url": source["url"], "response_format": "json"},
    )
    run, _ = execute(client, admin_headers, plan)
    assert run["status"] == "failed"
    assert run["error_code"] == "source_object_id_required"
    assert run["summary"]["coverage"]["conclusion"] == "unknown"


def test_collection_results_are_not_counted_as_objects(client, admin_headers):
    plan = create(
        client,
        admin_headers,
        fixed_objects=[
            {"id": "a", "facts": {"email": "same"}},
            {"id": "b", "facts": {"email": "same"}},
        ],
        check_items=[
            rule_check(
                scope="collection",
                conditions=[
                    condition(
                        kind="unique", path="objects", compare_path="facts.email", minimum=None
                    )
                ],
            )
        ],
    )
    run, rows = execute(client, admin_headers, plan)
    assert run["status"] == "completed"
    assert len(rows) == 1 and rows[0]["scope"] == "collection" and rows[0]["status"] == "fail"
    assert run["summary"]["objects"] == 2


@pytest.mark.parametrize("recover", [False, True])
def test_generated_python_is_preserved_and_failure_requires_repair(
    client, admin_headers, monkeypatch, recover
):
    def inspect(run_id, result_id):
        tools = check_agent.CheckTools(run_id, result_id)
        observed = tools.call("get_inspection_object", {})
        failed = tools.call(
            "execute_inspection_python", {"code": "result = 1 / 0", "input_ids": [observed["id"]]}
        )
        assert not failed["success"]
        evidence = [observed["id"]]
        if recover:
            computed = tools.call(
                "execute_inspection_python",
                {
                    "code": "result = next(iter(data['evidence'].values()))['facts']['amount'] * 2",
                    "input_ids": evidence,
                },
            )
            assert computed["success"] and computed["data"]["result"] == 20
            evidence.append(computed["id"])
        tools.call(
            "submit_inspection_result",
            {"status": "pass", "summary": "金额计算完成", "evidence_ids": evidence},
        )

    monkeypatch.setattr(check_agent, "evaluate", inspect)
    run, rows = execute(
        client,
        admin_headers,
        create(client, admin_headers, check_items=[agent_check(client, admin_headers)]),
    )
    assert run["status"] == ("completed" if recover else "partial"), rows
    assert rows[0]["status"] == ("pass" if recover else "unknown")
    traces = client.get(f"/api/v1/inspection-runs/{run['id']}/traces", headers=admin_headers).json()
    programs = [row for row in traces if row["tool"] == "execute_inspection_python"]
    assert programs[0]["data"]["code"] == "result = 1 / 0"
    assert not programs[0]["success"]


def test_cross_object_evidence_is_rejected(client, admin_headers, monkeypatch):
    first_evidence = []

    def inspect(run_id, result_id):
        tools = check_agent.CheckTools(run_id, result_id)
        evidence = tools.call("get_inspection_object", {})
        references = first_evidence or [evidence["id"]]
        tools.call(
            "submit_inspection_result",
            {"status": "pass", "summary": "校验通过", "evidence_ids": references},
        )
        first_evidence[:] = [evidence["id"]]

    monkeypatch.setattr(check_agent, "evaluate", inspect)
    run, rows = execute(
        client,
        admin_headers,
        create(
            client,
            admin_headers,
            fixed_objects=[{"id": "a", "facts": {}}, {"id": "b", "facts": {}}],
            check_items=[agent_check(client, admin_headers)],
        ),
    )
    assert run["status"] == "partial"
    assert {r["status"] for r in rows} == {"pass", "unknown"}
    assert any(r["error_code"] == "inspection_invalid_evidence" for r in rows)


def test_registered_sql_discovery_on_real_databases(
    client, admin_headers, application, payload, monkeypatch, make_user, login
):
    database = create_database(client, admin_headers, application, payload)
    database, _, table = enable_database(client, admin_headers, database, payload)
    query = client.post(
        "/api/v1/inspection-data-sources",
        headers=admin_headers,
        json={
            "name": "登记只读查询",
            "environment": "test",
            "application_id": application,
            "source_kind": "database",
            "source_config": {
                "connection_id": database["id"],
                "query_sql": f"SELECT id, retries FROM {table} WHERE retries >= :minimum",
                "query_parameters": {"minimum": 0},
                "allowed_query_params": ["minimum"],
                "id_column": "id",
                "fact_columns": ["retries"],
            },
        },
    )
    assert query.status_code == 201, query.text
    plan = create(
        client,
        admin_headers,
        application_id=application,
        object_mode="dynamic",
        fixed_objects=[],
        max_records=1000,
        data_source_id=query.json()["id"],
        check_items=[rule_check(conditions=[condition(path="retries", maximum=1)])],
    )
    run, _ = execute(client, admin_headers, plan)
    assert run["status"] == "completed", run
    assert run["summary"]["coverage"]["objects_expected"] == 300
    assert run["summary"]["coverage"]["conclusion"] == "fail"
    preview = client.post(
        f"/api/v1/inspection-data-sources/{query.json()['id']}/preview",
        headers=admin_headers,
        json={"limit": 1},
    )
    assert preview.status_code == 200, preview.text
    identifier = preview.json()["records"][0]["id"]

    def inspect(run_id, result_id):
        tools = check_agent.CheckTools(run_id, result_id)
        observed = tools.call(
            "query_inspection_database",
            {
                "source_id": query.json()["id"],
                "object_id": identifier,
                "parameters": {"minimum": 0},
            },
        )
        assert observed["success"] and len(observed["data"]["records"]) == 1
        tools.call(
            "submit_inspection_result",
            {"status": "pass", "summary": "已读取授权订单详情", "evidence_ids": [observed["id"]]},
        )

    monkeypatch.setattr(check_agent, "evaluate", inspect)
    agent = {**agent_check(client, admin_headers), "source_ids": [query.json()["id"]]}
    followup, rows = execute(
        client,
        admin_headers,
        create(
            client,
            admin_headers,
            application_id=application,
            fixed_objects=[{"id": identifier, "facts": {}}],
            check_items=[agent],
        ),
    )
    assert followup["status"] == "completed" and rows[0]["status"] == "pass", rows
    shared = client.post(
        "/api/v1/inspection-data-sources",
        headers=admin_headers,
        json={
            "name": "任务查询",
            "environment": "test",
            "source_kind": "database",
            "source_config": query.json()["source_config"],
        },
    )
    assert shared.status_code == 201, shared.text
    standalone = create(
        client, admin_headers, check_items=[{**agent, "source_ids": [shared.json()["id"]]}]
    )
    operator_id = make_user("checks-operator", "operator")
    headers = {"Authorization": "Bearer " + login("checks-operator")["access_token"]}
    assert (
        client.get(f"/api/v1/inspection-plans/{standalone['id']}", headers=headers).status_code
        == 200
    )
    administrator = UUID(client.get("/api/v1/me", headers=admin_headers).json()["id"])
    with SessionLocal() as db:
        db.add(
            AccessPolicy(
                resource_kind="service_database",
                resource_id=database["id"],
                user_id=operator_id,
                restricted=True,
                updated_by=administrator,
            )
        )
        db.commit()
    assert client.get(
        f"/api/v1/inspection-plans/{standalone['id']}", headers=headers
    ).status_code in {403, 404}
    assert client.post(
        f"/api/v1/inspection-plans/{standalone['id']}/runs", headers=headers
    ).status_code in {403, 404}


def test_http_status_is_a_business_verdict(client, admin_headers, source):
    source["status"], source["data"] = 503, {"message": "maintenance"}
    plan = create(
        client,
        admin_headers,
        object_mode="dynamic",
        fixed_objects=[],
        source_config={"url": source["url"], "response_format": "json"},
        check_items=[
            rule_check(
                conditions=[
                    condition(kind="equals", path="_http.status_code", expected=200, minimum=None)
                ]
            )
        ],
    )
    run, rows = execute(client, admin_headers, plan)
    assert run["status"] == "completed" and rows[0]["status"] == "fail"


def test_skill_resources_survive_legacy_edit_and_reject_unsafe_paths(client, admin_headers):
    body = {
        "code": "resource-check",
        "title": "资源示例",
        "content": "读取业务依据。",
        "resources": {"references/basis.md": "业务依据"},
    }
    created = client.post("/api/v1/skills", headers=admin_headers, json=body)
    assert created.status_code == 201, created.text
    legacy = {key: value for key, value in body.items() if key != "resources"}
    edited = client.put(
        f"/api/v1/skills/{created.json()['id']}", headers=admin_headers, json=legacy
    )
    assert edited.status_code == 200 and edited.json()["resources"] == body["resources"]
    invalid = client.post(
        "/api/v1/skills",
        headers=admin_headers,
        json={**body, "code": "unsafe", "resources": {"references/../config.txt": "x"}},
    )
    assert invalid.status_code == 422
