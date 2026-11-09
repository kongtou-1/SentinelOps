from uuid import UUID

from sqlalchemy import select
from test_inspection_checks import create, rule_check
from test_inspections import drain

from app.db.session import SessionLocal
from app.models import InspectionCheckResult, InspectionRun
from app.services.inspections import runtime
from app.services.inspections.recovery import recover_interrupted


def start(client, headers, plan):
    response = client.post(f"/api/v1/inspection-plans/{plan['id']}/runs", headers=headers)
    assert response.status_code == 202, response.text
    return response.json()["id"]


def detail(client, headers, identifier):
    return client.get(f"/api/v1/inspection-runs/{identifier}", headers=headers).json()


def test_pause_keeps_active_run(client, admin_headers):
    plan = create(client, admin_headers)
    identifier = start(client, admin_headers, plan)
    response = client.put(
        f"/api/v1/inspection-plans/{plan['id']}/state",
        headers=admin_headers,
        json={"enabled": False, "expected_version": plan["version"]},
    )
    assert response.status_code == 200, response.text
    drain()
    assert detail(client, admin_headers, identifier)["status"] == "completed"


def test_cancel_blocks_late_results(client, admin_headers, monkeypatch):
    plan = create(client, admin_headers)
    identifier = start(client, admin_headers, plan)
    runtime.run_once()
    from app.services.inspections import checks_runtime

    original = checks_runtime.check

    def cancel_during_check(*args):
        response = client.post(
            f"/api/v1/inspection-runs/{identifier}/cancel", headers=admin_headers
        )
        assert response.status_code == 200
        return original(*args)

    monkeypatch.setattr(checks_runtime, "check", cancel_during_check)
    runtime.run_once()
    run = detail(client, admin_headers, identifier)
    assert run["status"] == "cancelled"
    assert run["summary"]["coverage"]["checks_completed"] == 0


def test_worker_interruption_and_resume_preserve_completed(client, admin_headers):
    plan = create(client, admin_headers, check_items=[rule_check(), rule_check(check_id="other")])
    identifier = start(client, admin_headers, plan)
    runtime.run_once()
    with SessionLocal() as db:
        rows = db.scalars(select(InspectionCheckResult)).all()
        rows[0].execution_status, rows[0].status = "completed", "fail"
        rows[0].summary = "原始异常"
        rows[1].execution_status = "running"
        db.commit()
        assert recover_interrupted(db) == 1
    before = detail(client, admin_headers, identifier)
    assert before["status"] == "partial"
    response = client.post(f"/api/v1/inspection-runs/{identifier}/resume", headers=admin_headers)
    assert response.status_code == 202, response.text
    child = response.json()["id"]
    duplicate = client.post(f"/api/v1/inspection-runs/{identifier}/resume", headers=admin_headers)
    assert duplicate.status_code == 409
    drain()
    after = detail(client, admin_headers, child)
    assert after["status"] == "completed"
    assert after["summary"]["coverage"]["conclusion"] == "fail"
    assert after["snapshot"]["resume"]["parent_run_id"] == identifier
    assert detail(client, admin_headers, identifier) == before
    with SessionLocal() as db:
        assert (
            db.get(InspectionRun, UUID(child)).window_end
            == db.get(InspectionRun, UUID(identifier)).window_end
        )


def test_resume_rejects_unfinished_discovery(client, admin_headers):
    plan = create(client, admin_headers)
    identifier = start(client, admin_headers, plan)
    client.post(f"/api/v1/inspection-runs/{identifier}/cancel", headers=admin_headers)
    response = client.post(f"/api/v1/inspection-runs/{identifier}/resume", headers=admin_headers)
    assert response.status_code == 409
    assert response.json()["detail"] == "inspection_resume_discovery_incomplete"


def test_trial_retention_previews_then_soft_deletes(client, admin_headers):
    from datetime import UTC, datetime, timedelta

    from test_inspection_trials import definition

    from app.models import InspectionPlan
    from app.services.inspections.retention import clean_trials

    response = client.post(
        "/api/v1/inspection-trials",
        headers=admin_headers,
        json={"plan": definition(), "sample_size": 1},
    )
    assert response.status_code == 202
    identifier = UUID(response.json()["id"])
    drain()
    with SessionLocal() as db:
        run = db.get(InspectionRun, identifier)
        run.finished_at = datetime.now(UTC) - timedelta(days=8)
        db.commit()
        assert clean_trials(db) == [str(identifier)]
        assert run.deleted_at is None
        assert clean_trials(db, apply=True) == [str(identifier)]
        assert run.deleted_at is not None
        assert db.get(InspectionPlan, run.plan_id).deleted_at is not None
    assert (
        client.get(f"/api/v1/inspection-runs/{identifier}", headers=admin_headers).status_code
        == 404
    )
