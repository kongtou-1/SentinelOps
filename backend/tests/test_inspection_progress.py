"""Runtime facts, stable history paging, legacy compatibility and read boundaries."""

from datetime import UTC, datetime
from uuid import UUID

from test_inspection_checks import agent_check, create, execute

from app.db.session import SessionLocal
from app.models import InspectionCheckResult, InspectionItem, InspectionRun, InspectionTrace
from app.services.inspections import check_agent


def progress(client, headers, identifier, **params):
    response = client.get(
        f"/api/v1/inspection-runs/{identifier}/progress", headers=headers, params=params
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_progress_visible_during_check_and_kept_after_finish(client, admin_headers, monkeypatch):
    observed = []

    def inspect(run_id, result_id):
        tools = check_agent.CheckTools(run_id, result_id)
        evidence = tools.call("get_inspection_object", {})
        live = progress(client, admin_headers, run_id)
        assert live["status"] == "evaluating"
        assert live["current"]["check_name"] == "材料核验"
        assert live["current"]["object_key"] == "a"
        assert any(e["tool"] == "get_inspection_object" for e in live["events"])
        assert not any(e["kind"] == "finished" for e in live["events"])
        observed.append(live)
        tools.call(
            "submit_inspection_result",
            {"status": "pass", "summary": "材料检查通过", "evidence_ids": [evidence["id"]]},
        )

    monkeypatch.setattr(check_agent, "evaluate", inspect)
    run, _ = execute(
        client,
        admin_headers,
        create(client, admin_headers, check_items=[agent_check(client, admin_headers)]),
    )
    final = progress(client, admin_headers, run["id"])
    assert observed and final["status"] == "completed"
    assert final["current"] is None
    assert [e["kind"] for e in final["events"]] == [
        "queued",
        "started",
        "object",
        "check_started",
        "tool",
        "check_finished",
        "finished",
    ]
    assert final["events"][-2]["summary"] == "材料检查通过"


def test_history_cursor_has_no_duplicates_or_gaps_and_excludes_deleted_records(
    client, admin_headers
):
    run, checks = execute(
        client,
        admin_headers,
        create(
            client,
            admin_headers,
            fixed_objects=[{"id": str(index), "facts": {"amount": index}} for index in range(30)],
        ),
    )
    identifier = run["id"]
    expected = progress(client, admin_headers, identifier, limit=100)["events"]
    page = progress(client, admin_headers, identifier, limit=7)
    chunks = [page["events"]]
    while page["next_cursor"]:
        page = progress(client, admin_headers, identifier, limit=7, before=page["next_cursor"])
        chunks.insert(0, page["events"])
    actual = [event for chunk in chunks for event in chunk]
    assert actual == expected
    assert len({e["id"] for e in actual}) == len(actual) == 93
    with SessionLocal() as db:
        item = db.get(InspectionItem, UUID(checks[0]["item_id"]))
        item.deleted_at = item.updated_at = datetime.now(UTC)
        db.commit()
    visible = progress(client, admin_headers, identifier, limit=100)["events"]
    assert all(e["object_key"] != checks[0]["object_key"] for e in visible)


def test_tool_failures_and_check_errors_are_distinct_and_redacted(
    client, admin_headers, monkeypatch
):
    def inspect(run_id, result_id):
        with SessionLocal() as db:
            row = db.get(InspectionCheckResult, result_id)
            db.add(
                InspectionTrace(
                    run_id=run_id,
                    plan_id=row.plan_id,
                    check_result_id=result_id,
                    tool="execute_inspection_python",
                    success=False,
                    data={
                        "arguments": {"api_key": "test-private-value"},
                        "code": "result = 1 / 0",
                        "value": {"error_code": "python_execution_failed"},
                    },
                )
            )
            db.commit()
        raise ValueError("inspection_sandbox_unavailable")

    monkeypatch.setattr(check_agent, "evaluate", inspect)
    run, _ = execute(
        client,
        admin_headers,
        create(client, admin_headers, check_items=[agent_check(client, admin_headers)]),
    )
    events = progress(client, admin_headers, run["id"])["events"]
    tool = next(e for e in events if e["kind"] == "tool")
    assert tool["status"] == "error"
    assert tool["data"]["arguments"]["api_key"] == "[REDACTED]"
    assert tool["data"]["code"] == "result = 1 / 0"
    assert (
        next(e for e in events if e["kind"] == "check_finished")["error_code"]
        == "inspection_sandbox_unavailable"
    )
    assert events[-1]["status"] == "partial"


def test_legacy_tool_calls_share_timeline_and_read_access(client, admin_headers):
    run, checks = execute(client, admin_headers, create(client, admin_headers))
    with SessionLocal() as db:
        row = db.get(InspectionRun, UUID(run["id"]))
        row.snapshot = {"plan": {"name": "旧版巡检"}}
        item = db.get(InspectionItem, UUID(checks[0]["item_id"]))
        at = row.started_at.isoformat()
        item.evidence = {
            **item.evidence,
            "tool_calls": [
                {
                    "id": "call-1",
                    "tool": "request_inspection_api",
                    "success": True,
                    "observed_at": at,
                    "arguments": {"query": {"page": 1}},
                    "data": {"count": 3},
                }
            ],
        }
        db.commit()
    events = progress(client, admin_headers, run["id"])["events"]
    call = next(e for e in events if e["id"].startswith("legacy_tool:"))
    assert call["kind"] == "tool" and call["data"]["value"] == {"count": 3}
    path = f"/api/v1/inspection-runs/{run['id']}/progress"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=admin_headers, params={"before": "invalid"}).status_code == 422
    assert client.get(path, headers=admin_headers, params={"limit": 101}).status_code == 422
    with SessionLocal() as db:
        row = db.get(InspectionRun, UUID(run["id"]))
        row.deleted_at = row.updated_at = datetime.now(UTC)
        db.commit()
    assert client.get(path, headers=admin_headers).status_code == 404
