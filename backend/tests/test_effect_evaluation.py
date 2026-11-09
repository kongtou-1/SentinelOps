from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import AgentAnalysis, EvaluationRun, Incident, User
from app.services.operations import evaluation, prediction_outcomes


def official_analysis(headers):
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "admin"))
        now = datetime.now(UTC)
        incident = Incident(
            title="Frozen case",
            severity="warning",
            status="closed",
            started_at=now,
            last_alert_started_at=now,
        )
        db.add(incident)
        db.flush()
        evidence = {
            "id": "e_test",
            "source": "get_alert_context",
            "data": {"title": "CPU saturation"},
        }
        answer = {
            "summary": "CPU saturation",
            "possible_causes": [{"description": "CPU saturation", "evidence_ids": ["e_test"]}],
            "impact": "slow",
            "evidence_ids": ["e_test"],
            "knowledge_ids": [],
            "suggested_checks": [],
            "data_gaps": [],
        }
        row = AgentAnalysis(
            incident_id=incident.id,
            requested_by=user.id,
            status="completed",
            input_hash="x" * 64,
            config={"model": "before"},
            evidence=[evidence],
            tool_calls=[],
            knowledge=[],
            result=answer,
        )
        db.add(row)
        db.commit()
        return row.id


def test_review_is_independent_and_private_run_cannot_be_exported(
    client, admin_headers, make_user, login
):
    identifier = official_analysis(admin_headers)
    review = {
        "verdict": "incorrect",
        "confirmed_cause": "capacity",
        "evidence": "verified by operator",
    }
    response = client.post(
        f"/api/v1/analyses/{identifier}/reviews", headers=admin_headers, json=review
    )
    assert response.status_code == 201, response.text
    assert response.json()["verdict"] == "incorrect"
    with SessionLocal() as db:
        assert db.get(AgentAnalysis, identifier).result["summary"] == "CPU saturation"
    make_user("reader", "viewer")
    viewer = {"Authorization": "Bearer " + login("reader")["access_token"]}
    assert (
        client.post(
            f"/api/v1/analyses/{identifier}/reviews", headers=viewer, json=review
        ).status_code
        == 403
    )
    assert client.get(f"/api/v1/analyses/{identifier}/reviews", headers=viewer).status_code == 200
    assert (
        client.post(
            f"/api/v1/analyses/{uuid4()}/reviews", headers=admin_headers, json=review
        ).status_code
        == 404
    )


def test_versioned_case_replay_checks_and_paid_confirmation(client, admin_headers):
    identifier = official_analysis(admin_headers)
    body = {
        "analysis_id": str(identifier),
        "title": "Capacity case",
        "checks": {"required_tools": ["get_alert_context"], "cause_terms": ["CPU"]},
        "human_label": "capacity",
        "confirm_sanitized": True,
    }
    case = client.post("/api/v1/evaluation-cases", headers=admin_headers, json=body)
    assert case.status_code == 201, case.text
    case = case.json()
    new = client.post(
        "/api/v1/evaluation-cases", headers=admin_headers, json={**body, "previous_id": case["id"]}
    )
    assert new.status_code == 201 and new.json()["version"] == 2
    assert (
        client.post(
            "/api/v1/evaluation-cases",
            headers=admin_headers,
            json={**body, "previous_id": case["id"]},
        ).status_code
        == 409
    )
    request = {"idempotency_key": str(uuid4()), "provider_kind": "mock"}
    path = f"/api/v1/evaluation-cases/{case['id']}/replays"
    run = client.post(path, headers=admin_headers, json=request)
    assert run.status_code == 202, run.text
    assert client.post(path, headers=admin_headers, json=request).json()["id"] == run.json()["id"]
    assert (
        client.post(
            path,
            headers=admin_headers,
            json={"idempotency_key": str(uuid4()), "provider_kind": "model"},
        ).status_code
        == 422
    )
    assert evaluation.replay_once()
    result = client.get("/api/v1/evaluation-runs/" + run.json()["id"], headers=admin_headers).json()
    assert result["provider_kind"] == "mock" and result["status"] == "completed"
    assert result["result"]["checks"]["references_valid"] is True
    assert result["result"]["checks"]["cause_terms"] == {"CPU": True}
    review = {
        "expected_version": 1,
        "verdict": "correct",
        "evidence": "fixed case check",
        "confirmed_cause": "CPU",
    }
    assert (
        client.post(
            "/api/v1/evaluation-runs/" + result["id"] + "/review",
            headers=admin_headers,
            json=review,
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/evaluation-runs/" + result["id"] + "/review",
            headers=admin_headers,
            json=review,
        ).status_code
        == 409
    )
    with SessionLocal() as db:
        row = db.get(EvaluationRun, UUID(result["id"]))
        assert row.config["case_hash"] == case["content_hash"]


def test_frozen_tools_never_fall_back_to_production(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Live access is forbidden during replay")

    monkeypatch.setattr("app.services.diagnosis.tools.SessionLocal", forbidden)
    monkeypatch.setattr("app.services.diagnosis.tools.prometheus", forbidden)
    monkeypatch.setattr("app.services.diagnosis.tools.read_container", forbidden)
    tools = evaluation.FrozenTools({"records": [], "knowledge": []})
    result = tools.call("get_alert_context", {})
    assert result["error_code"] == "case_data_gap"
    assert tools.gaps and not tools.evidence
    assert (
        evaluation.check_answer({}, tools, {"required_tools": [], "cause_terms": []})[
            "references_valid"
        ]
        is None
    )


def test_prediction_requires_complete_future_observation_and_positive_lead():
    now = datetime.now(UTC).replace(microsecond=0)
    now -= timedelta(seconds=int(now.timestamp()) % 300)
    result = SimpleNamespace(
        data_end_at=now,
        finished_at=now + timedelta(seconds=10),
        config={"threshold": 80, "source_epoch": now.isoformat()},
        horizons={
            "1": {
                "quality": "validated",
                "points": [{}],
                "risk": {"level": "expected", "first_breach_at": int(now.timestamp()) + 300},
            }
        },
    )
    points = [[int(now.timestamp()) + i * 300, 90 if i == 6 else 20] for i in range(1, 13)]
    status, observed = prediction_outcomes.assess(result, 1, points)
    assert status == "observed" and observed["threshold_match"] is True
    assert observed["lead_seconds"] == 1790
    status, unknown = prediction_outcomes.assess(result, 1, points[:-1])
    assert status == "incomplete" and "threshold_match" not in unknown
    result.horizons["1"]["risk"]["level"] = "unknown"
    assert prediction_outcomes.assess(result, 1, points)[0] == "excluded"
