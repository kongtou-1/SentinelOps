from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from test_ai import ai_config as ai_config
from test_ai import diagnosis, proposal
from test_ai import incident as incident
from test_conversations import create, send

from app.db.session import SessionLocal
from app.integrations.diagnostic_gateway import DiagnosticGateway
from app.models import (
    AgentAnalysis,
    AlertEvent,
    AnalysisRun,
    Application,
    IncidentAlert,
    KnowledgeSkill,
    ServiceMonitor,
)
from app.services.diagnosis.analyses import validate_result
from app.services.operations import evaluation
from app.workers.conversations import ConversationTools, conversation_once, record


@pytest.fixture
def service_monitor(incident):
    with SessionLocal() as db:
        app = Application(code="agent-service", name="Agent service")
        db.add(app)
        db.flush()
        monitor = ServiceMonitor(
            application_id=app.id,
            name="Business result",
            protocol="http",
            target="http://isolated/work",
            route="/work",
            importance="critical",
        )
        db.add(monitor)
        db.flush()
        event = AlertEvent(
            fingerprint=uuid4().hex,
            service_monitor_id=monitor.id,
            severity="critical",
            status="firing",
            starts_at=datetime.now(UTC),
            labels={},
            annotations={},
        )
        db.add(event)
        db.flush()
        db.add(IncidentAlert(incident_id=UUID(incident["incident_id"]), alert_event_id=event.id))
        db.add(KnowledgeSkill(code="sre_diagnosis", title="Read only", content="只读检查"))
        db.commit()
        return monitor.id


def test_service_evidence_survives_rounds_adoption_and_frozen_replay(
    client,
    admin_headers,
    incident,
    ai_config,
    service_monitor,
    monkeypatch,
):
    gateways, observed = [], []

    @contextmanager
    def gateway(self):
        gateways.append(self)
        yield "http://isolated"

    def metrics(*args):
        observed.append(args)
        return {"result": [{"metric": {}, "value": [datetime.now(UTC).timestamp(), "0"]}]}

    def harness(self, *args, **kwargs):
        tools = gateways[-1].tools
        old = next((e for e in tools.evidence if e.get("source") == "get_service_metrics"), None)
        skill = tools.call("list_skills", {})["data"][0]
        tools.call("read_skill", {"skill_id": skill["id"], "version": skill["version"]})
        metric = old or tools.call(
            "get_service_metrics", {"asset_id": str(service_monitor), "metric": "service_probe_up"}
        )
        record(
            tools.analysis_id,
            "tool_finished",
            {
                "call_id": "private",
                "tool": "search_knowledge",
                "duration_ms": 1,
                "arguments": {"query": "private-query-marker"},
                "result": {
                    "id": "private-unused",
                    "source": "search_knowledge",
                    "data": "private-output-marker",
                },
            },
        )
        tools.call("submit_diagnosis", diagnosis(metric["id"]))
        kwargs["on_event"](
            "messages_accepted", {"message_ids": kwargs["initial_ids"], "sdk_message_id": "test"}
        )
        return None, None

    monkeypatch.setattr(DiagnosticGateway, "running", gateway)
    monkeypatch.setattr("app.integrations.harness.HarnessAdapter.run", harness)
    monkeypatch.setattr("app.services.monitoring.service_monitors.metrics", metrics)
    monkeypatch.setattr("app.services.diagnosis.tools.search", lambda *a, **k: [])
    conversation = create(client, admin_headers, incident)
    first = send(client, admin_headers, conversation, "private-chat-marker")
    assert conversation_once()
    assert (
        client.get(f"/api/v1/analysis-runs/{first['run_id']}", headers=admin_headers).json()[
            "status"
        ]
        == "completed"
    )
    second = send(client, admin_headers, conversation, "继续检查")
    assert conversation_once() and len(observed) == 1
    response = client.post(f"/api/v1/analysis-runs/{second['run_id']}/adopt", headers=admin_headers)
    assert response.status_code == 200, response.text
    published = response.json()
    assert all(
        word not in response.text
        for word in (
            "private-chat-marker",
            "private-query-marker",
            "private-output-marker",
            "private-unused",
        )
    )
    metric_call = next(c for c in published["tool_calls"] if c["tool"] == "get_service_metrics")
    assert metric_call["arguments"] == {
        "asset_id": str(service_monitor),
        "metric": "service_probe_up",
    }
    assert any(e["source"] == "read_skill" for e in published["evidence"])
    preview = client.get(
        f"/api/v1/analyses/{published['id']}/evaluation-preview", headers=admin_headers
    )
    assert preview.status_code == 200, preview.text
    body = {
        "analysis_id": published["id"],
        "title": "Service case",
        "human_label": "Business failure",
        "confirm_sanitized": True,
        "expected_snapshot_hash": preview.json()["content_hash"],
        "checks": {
            "required_tools": [
                "get_alert_context",
                "list_skills",
                "read_skill",
                "get_service_metrics",
            ],
            "cause_terms": [],
        },
    }
    assert (
        client.post(
            "/api/v1/evaluation-cases",
            headers=admin_headers,
            json={**body, "expected_snapshot_hash": "0" * 64},
        ).status_code
        == 409
    )
    case = client.post("/api/v1/evaluation-cases", headers=admin_headers, json=body)
    assert case.status_code == 201, case.text
    run = client.post(
        f"/api/v1/evaluation-cases/{case.json()['id']}/replays",
        headers=admin_headers,
        json={"idempotency_key": str(uuid4()), "provider_kind": "mock"},
    )
    assert run.status_code == 202 and evaluation.replay_once()
    result = client.get(f"/api/v1/evaluation-runs/{run.json()['id']}", headers=admin_headers).json()
    assert result["status"] == "completed"
    checks = result["result"]["checks"]
    assert checks["structure_valid"] and checks["references_valid"]
    assert all(checks["required_tools"].values()) and checks["data_gaps"] == []


def test_readonly_mode_rejects_proposal_tool_and_result_but_keeps_legacy_validation(
    client,
    admin_headers,
    incident,
    ai_config,
):
    message = send(client, admin_headers, create(client, admin_headers, incident))
    identifier = UUID(message["run_id"])
    with SessionLocal() as db:
        row = db.get(AnalysisRun, identifier)
        row.status, row.started_at = "running", datetime.now(UTC)
        assert row.config["diagnosis_mode"] == "read_only"
        db.commit()
    tools = ConversationTools(identifier, row_model=AnalysisRun)
    context = tools.call("get_alert_context", {}, internal=True)
    assert "propose_action_skill" not in tools.definitions
    assert "mcp__ops__propose_action_skill" not in DiagnosticGateway(tools).public_tools
    answer = diagnosis(context["id"], proposal=proposal(incident["container_id"]))
    with SessionLocal() as db:
        row = db.get(AnalysisRun, identifier)
        with pytest.raises(ValueError, match="Read-only"):
            validate_result(db, row, answer, [context], [])
        row.config = {k: v for k, v in row.config.items() if k != "diagnosis_mode"}
        assert validate_result(db, row, answer, [context], [])["proposal"]["executable"] is False
        db.rollback()
    with pytest.raises(ValueError, match="not allowed"):
        tools.call("propose_action_skill", proposal(incident["container_id"]))


def test_unrelated_monitor_is_rejected(client, admin_headers, incident, ai_config, service_monitor):
    message = send(client, admin_headers, create(client, admin_headers, incident))
    identifier = UUID(message["run_id"])
    with SessionLocal() as db:
        row = db.get(AnalysisRun, identifier)
        row.status, row.started_at = "running", datetime.now(UTC)
        app = Application(code="unrelated-service", name="Unrelated")
        db.add(app)
        db.flush()
        monitor = ServiceMonitor(
            application_id=app.id,
            name="Unrelated",
            protocol="http",
            target="http://isolated/health",
        )
        db.add(monitor)
        db.commit()
        outside = monitor.id
    tools = ConversationTools(identifier, row_model=AnalysisRun)
    with pytest.raises(ValueError, match="outside incident scope"):
        tools.call("get_service_metrics", {"asset_id": str(outside), "metric": "service_probe_up"})


def test_case_preview_requires_admin_and_handles_incomplete_legacy_arguments(
    client,
    admin_headers,
    incident,
    ai_config,
    make_user,
    login,
):
    message = send(client, admin_headers, create(client, admin_headers, incident))
    with SessionLocal() as db:
        run = db.get(AnalysisRun, UUID(message["run_id"]))
        row = AgentAnalysis(
            incident_id=run.incident_id,
            requested_by=run.requested_by,
            status="completed",
            input_hash="a" * 64,
            config={},
            result=diagnosis(),
            evidence=[{"id": "e1", "source": "get_service_metrics", "data": {"value": 0}}],
            tool_calls=[{"tool": "get_service_metrics", "evidence_id": "e1"}],
        )
        db.add(row)
        db.commit()
        identifier = row.id
    make_user("case-operator", "operator")
    headers = {"Authorization": "Bearer " + login("case-operator")["access_token"]}
    assert (
        client.get(f"/api/v1/analyses/{identifier}/evaluation-preview", headers=headers).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/v1/analyses/{message['run_id']}/evaluation-preview", headers=admin_headers
        ).status_code
        == 404
    )
    preview = client.get(f"/api/v1/analyses/{identifier}/evaluation-preview", headers=admin_headers)
    assert preview.status_code == 200
    assert preview.json()["snapshot"]["data_gaps"] == [
        {"tool": "get_service_metrics", "reason": "arguments_unavailable"}
    ]
