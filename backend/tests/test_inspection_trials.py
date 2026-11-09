"""Sample runs must execute the current form without changing saved task state."""

from uuid import UUID

from test_inspection_checks import agent_check, create, execute, rule_check
from test_inspections import drain

from app.db.session import SessionLocal
from app.models import InspectionPlan


def definition(**values):
    return {
        "name": "未保存的任务",
        "environment": "test",
        "object_mode": "fixed",
        "fixed_objects": [{"id": str(n), "facts": {"amount": n}} for n in range(5)],
        "check_items": [rule_check()],
        **values,
    }


def test_trial_uses_unsaved_snapshot_and_isolates_formal_state(client, admin_headers):
    saved = create(client, admin_headers)
    execute(client, admin_headers, saved)
    before = client.get(f"/api/v1/inspection-plans/{saved['id']}", headers=admin_headers).json()
    latest_before = client.get("/api/v1/inspection-plans", headers=admin_headers).json()[0]
    result = client.post(
        "/api/v1/inspection-trials",
        headers=admin_headers,
        json={"plan": definition(), "sample_size": 3},
    )
    assert result.status_code == 202, result.text
    trial = result.json()
    drain()
    run = client.get(f"/api/v1/inspection-runs/{trial['id']}", headers=admin_headers).json()
    assert run["run_kind"] == "trial"
    assert run["collected"] == 3
    assert run["status"] == "completed"
    assert run["summary"]["coverage"]["scope_is_sample"]
    assert run["summary"]["coverage"]["checks_expected"] == 3
    assert not run["summary"]["coverage"]["discovery_complete"]
    assert run["snapshot"]["trial"] == {"sample_limit": 3, "full_scope": False}
    assert run["snapshot"]["plan"]["name"] == "未保存的任务"
    after = client.get(f"/api/v1/inspection-plans/{saved['id']}", headers=admin_headers).json()
    assert after == before
    listing = client.get("/api/v1/inspection-plans", headers=admin_headers).json()
    assert len(listing) == 1
    assert listing[0]["last_run_id"] == latest_before["last_run_id"]
    assert listing[0]["group_total"] == 1
    trials = client.get("/api/v1/inspection-runs?run_kind=trial", headers=admin_headers).json()
    assert [r["id"] for r in trials] == [trial["id"]]
    with SessionLocal() as db:
        snapshot = db.get(InspectionPlan, UUID(trial["plan_id"]))
        assert snapshot.trial_only and not snapshot.enabled
        assert snapshot.watermark is None and snapshot.next_run_at is None
    denied = client.post(f"/api/v1/inspection-plans/{trial['plan_id']}/runs", headers=admin_headers)
    assert denied.status_code == 409


def test_trial_mixed_checks_keep_agent_failure_and_object_filter(
    client, admin_headers, monkeypatch
):
    from app.services.inspections import check_agent

    agent = agent_check(client, admin_headers)

    def fail(*_):
        raise ValueError("inspection_sandbox_unavailable")

    monkeypatch.setattr(check_agent, "evaluate", fail)
    result = client.post(
        "/api/v1/inspection-trials",
        headers=admin_headers,
        json={"plan": definition(check_items=[rule_check(), agent]), "sample_size": 1},
    )
    assert result.status_code == 202, result.text
    drain()
    identifier = result.json()["id"]
    run = client.get(f"/api/v1/inspection-runs/{identifier}", headers=admin_headers).json()
    assert run["summary"]["coverage"]["conclusion"] == "unknown"
    item = client.get(f"/api/v1/inspection-runs/{identifier}/items", headers=admin_headers).json()[
        0
    ]
    checks = client.get(
        f"/api/v1/inspection-runs/{identifier}/check-results?item_id={item['id']}",
        headers=admin_headers,
    ).json()
    assert len(checks) == 2
    assert {r["execution_status"] for r in checks} == {"completed", "error"}


def test_trial_validates_configuration_and_permissions(client, admin_headers, make_user, login):
    invalid = definition(fixed_objects=[{"id": "a", "facts": {}}, {"id": "a", "facts": {}}])
    response = client.post(
        "/api/v1/inspection-trials", headers=admin_headers, json={"plan": invalid}
    )
    assert response.status_code == 422
    assert client.get("/api/v1/inspection-plans", headers=admin_headers).json() == []
    make_user("viewer", "viewer")
    headers = {"Authorization": "Bearer " + login("viewer")["access_token"]}
    assert (
        client.post(
            "/api/v1/inspection-trials", headers=headers, json={"plan": definition()}
        ).status_code
        == 403
    )


def test_copy_is_paused_with_fresh_identity_and_no_history(client, admin_headers):
    saved = create(client, admin_headers)
    execute(client, admin_headers, saved)
    result = client.post(f"/api/v1/inspection-plans/{saved['id']}/copy", headers=admin_headers)
    assert result.status_code == 201, result.text
    copied = result.json()
    assert copied["id"] != saved["id"] and not copied["enabled"]
    assert copied["check_items"] == saved["check_items"]
    assert copied["watermark"] is None and copied["continuation"] == {}
    assert copied["next_run_at"] is None
    runs = client.get(f"/api/v1/inspection-runs?plan_id={copied['id']}", headers=admin_headers)
    assert runs.json() == []


def test_paused_unified_task_can_be_run_manually(client, admin_headers):
    saved = create(client, admin_headers, enabled=False)
    run, _ = execute(client, admin_headers, saved)
    assert run["status"] == "completed"
    assert not client.get(f"/api/v1/inspection-plans/{saved['id']}", headers=admin_headers).json()[
        "enabled"
    ]


def test_task_group_totals_respect_filters_and_pagination(client, admin_headers):
    for name in ("a", "b", "c"):
        create(client, admin_headers, name=name)
    create(client, admin_headers, name="d", environment="prod")
    result = client.get("/api/v1/inspection-plans?limit=2", headers=admin_headers)
    assert result.headers["x-total-count"] == "4"
    assert len(result.json()) == 2
    assert all(
        row["group_total"] == (3 if row["environment"] == "test" else 1) for row in result.json()
    )
    result = client.get("/api/v1/inspection-plans?environment=test&search=a", headers=admin_headers)
    assert len(result.json()) == 1 and result.json()[0]["group_total"] == 1


def test_schedule_preview_uses_backend_timezone_validation(client, admin_headers):
    result = client.post(
        "/api/v1/inspection-schedule-preview",
        headers=admin_headers,
        json={
            "schedule_kind": "cron",
            "cron_expression": "0 2 * * *",
            "schedule_timezone": "Asia/Shanghai",
        },
    )
    assert result.status_code == 200, result.text
    assert len(result.json()["times"]) == 3
    assert (
        client.post(
            "/api/v1/inspection-schedule-preview",
            headers=admin_headers,
            json={"schedule_kind": "cron", "cron_expression": "bad"},
        ).status_code
        == 422
    )
