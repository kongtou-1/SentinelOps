"""Business-scoped discovery, private history, bounded reads and deliberate publication."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_access import user_policy
from test_ai import diagnosis
from test_ai import incident as incident
from test_inspection_checks import create as create_plan

from app.db.session import SessionLocal
from app.models import (
    AgentAnalysis,
    AnalysisConversation,
    AnalysisMessage,
    AnalysisRun,
    Application,
    InspectionCheckResult,
    InspectionItem,
    InspectionRun,
    InspectionTrace,
    KnowledgeChunk,
    KnowledgeDocument,
    User,
)
from app.repositories.worker_conversations import conversation_once_query_3
from app.schemas.agent_results import HistoryRead, HistorySearch
from app.services.diagnosis.analyses import validate_result
from app.services.knowledge.agent_results import tool_call
from app.services.knowledge.context import carry_records, evidence_catalog, recent_messages


def completed(incident_id, *, conversation=False, owner=None, status="completed"):
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == (owner or "admin")))
        values = dict(
            incident_id=UUID(incident_id),
            requested_by=user.id,
            status=status,
            config={},
            finished_at=datetime.now(UTC),
        )
        if conversation:
            thread = AnalysisConversation(
                incident_id=UUID(incident_id), owner_id=user.id, title="private"
            )
            db.add(thread)
            db.flush()
            row = AnalysisRun(**values, conversation_id=thread.id, candidate=diagnosis())
        else:
            row = AgentAnalysis(**values, input_hash=uuid4().hex, result=diagnosis())
        row.evidence = [
            {
                "id": "e1",
                "source": "get_alert_context",
                "data": {"original": "详细证据" * 5000, "password": "never-expose"},
            }
        ]
        db.add(row)
        db.commit()
        return row.id, row.conversation_id if conversation else None


def test_scoped_search_pagination_bounded_sections_and_soft_delete(client, admin_headers, incident):
    ids = [completed(incident["incident_id"])[0] for _ in range(3)]
    completed(incident["incident_id"], status="failed")
    params = {"business": "diagnosis", "scope_id": incident["incident_id"], "limit": 2}
    response = client.get("/api/v1/agent-results", params=params, headers=admin_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total"] == 3 and len(data["items"]) == 2 and data["next_offset"] == 2
    assert "详细证据" not in response.text
    assert {r["id"] for r in data["items"]} <= set(map(str, ids))
    assert (
        client.get(
            "/api/v1/agent-results", params={**params, "query": "容器停止"}, headers=admin_headers
        ).json()["total"]
        == 3
    )
    assert (
        client.get(
            "/api/v1/agent-results", params={**params, "query": "%"}, headers=admin_headers
        ).json()["total"]
        == 0
    )
    path = f"/api/v1/agent-results/diagnosis/{ids[0]}"
    segments, offset = [], 0
    while True:
        response = client.get(
            path,
            params={"section": "evidence", "offset": offset, "limit": 8000},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        value = response.json()
        assert len(value["content"]) <= 8000 and value["record"]["historical"]
        segments.append(value["content"])
        if value["next_offset"] is None:
            break
        offset = value["next_offset"]
    evidence = json.loads("".join(segments))
    assert evidence[0]["data"]["password"] == "[REDACTED]"
    assert len(evidence[0]["data"]["original"]) == 20000
    with SessionLocal() as db:
        db.get(AgentAnalysis, ids[0]).deleted_at = datetime.now(UTC)
        db.commit()
    assert client.get(path, headers=admin_headers).status_code == 404
    assert (
        client.get("/api/v1/agent-results", params=params, headers=admin_headers).json()["total"]
        == 2
    )


def test_private_sessions_and_resource_denials_are_preserved(
    client,
    admin_headers,
    incident,
    make_user,
    login,
):
    first = make_user("op1", "operator")
    make_user("op2", "operator")
    auth1 = {"Authorization": "Bearer " + login("op1")["access_token"]}
    auth2 = {"Authorization": "Bearer " + login("op2")["access_token"]}
    private_id, conversation_id = completed(incident["incident_id"], conversation=True, owner="op1")
    public_id, _ = completed(incident["incident_id"])
    params = {"business": "diagnosis", "scope_id": incident["incident_id"]}
    assert client.get("/api/v1/agent-results", params=params, headers=auth2).json()["total"] == 1
    assert (
        client.get(
            "/api/v1/agent-results",
            params={**params, "conversation_id": conversation_id},
            headers=auth2,
        ).status_code
        == 403
    )
    path = f"/api/v1/agent-results/conversation/{private_id}"
    assert client.get(path, headers=auth2).status_code == 403
    assert client.get(path, headers=auth1).status_code == 200
    assert (
        user_policy(client, admin_headers, first, "server", incident["server_id"]).status_code
        == 200
    )
    assert (
        client.get(f"/api/v1/agent-results/diagnosis/{public_id}", headers=auth1).status_code == 404
    )
    assert client.get("/api/v1/agent-results", params=params, headers=auth1).status_code == 404


def test_draft_is_disabled_idempotent_redacted_and_publication_is_explicit(
    client,
    admin_headers,
    incident,
    make_user,
    login,
):
    identifier, _ = completed(incident["incident_id"])
    path = f"/api/v1/agent-results/diagnosis/{identifier}/knowledge-draft"
    body = {"title": "容器退出排查", "content": "人工核验：检查退出码。password=do-not-store"}
    make_user("operator", "operator")
    operator = {"Authorization": "Bearer " + login("operator")["access_token"]}
    assert client.post(path, headers=operator, json=body).status_code == 403
    response = client.post(path, headers=admin_headers, json=body)
    assert response.status_code == 201, response.text
    doc = response.json()
    assert not doc["enabled"] and doc["source"] == f"agent-result:diagnosis:{identifier}"
    assert "do-not-store" not in doc["content"]
    assert client.post(path, headers=admin_headers, json=body).json()["id"] == doc["id"]
    with SessionLocal() as db:
        assert (
            db.scalar(select(KnowledgeChunk).where(KnowledgeChunk.document_id == UUID(doc["id"])))
            is None
        )
        assert db.get(KnowledgeDocument, UUID(doc["id"])).enabled is False
    update = {key: doc[key] for key in ("title", "content", "source", "tags", "enabled")}
    endpoint = "/api/v1/knowledge/" + doc["id"]
    assert (
        client.put(endpoint, headers=admin_headers, json={**update, "source": ""}).status_code
        == 422
    )
    response = client.put(endpoint, headers=admin_headers, json={**update, "enabled": True})
    assert response.status_code == 202 and response.json()["enabled"]


def test_inspection_results_keep_scope_and_history_never_counts_as_current_evidence(
    client,
    admin_headers,
    incident,
):
    with SessionLocal() as db:
        application = Application(code="memory-test", name="巡检服务")
        db.add(application)
        db.commit()
        application_id = application.id
    plan = create_plan(client, admin_headers, application_id=str(application_id))
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "admin"))
        now = datetime.now(UTC)
        run = InspectionRun(
            plan_id=UUID(plan["id"]),
            plan_version=1,
            trigger="manual",
            requested_by=user.id,
            status="completed",
            snapshot={},
            window_start=now - timedelta(hours=1),
            window_end=now,
        )
        db.add(run)
        db.flush()
        check = InspectionCheckResult(
            run_id=run.id,
            plan_id=run.plan_id,
            object_key="a",
            check_id="semantic",
            kind="agent",
            execution_status="completed",
            status="fail",
            summary="金额不一致",
            result={"name": "金额核验", "summary": "金额不一致"},
        )
        db.add(check)
        db.flush()
        db.add(
            InspectionTrace(
                run_id=run.id,
                plan_id=run.plan_id,
                check_result_id=check.id,
                tool="get_inspection_object",
                success=True,
                data={"amount": 10},
            )
        )
        db.commit()
        check_id, run_id = check.id, run.id
        assert (
            tool_call(db, run, "search_agent_history", HistorySearch(), inspection=True)["total"]
            == 0
        )
        current = SimpleNamespace(id=uuid4(), requested_by=run.requested_by, plan_id=run.plan_id)
        found = tool_call(db, current, "search_agent_history", HistorySearch(), inspection=True)
        assert found["items"][0]["id"] == str(check_id)
        assert found["items"][0]["outcome"] == "fail"
        reading = tool_call(
            db,
            current,
            "read_agent_history",
            HistoryRead(kind="inspection_check", result_id=check_id, section="evidence"),
            inspection=True,
        )
        assert "get_inspection_object" in reading["content"]
        diagnostic_id, _ = completed(incident["incident_id"])
        with pytest.raises(ValueError, match="history_outside_business_scope"):
            tool_call(
                db,
                run,
                "read_agent_history",
                HistoryRead(kind="diagnosis", result_id=diagnostic_id),
                inspection=True,
            )
    response = client.post(
        f"/api/v1/agent-results/inspection_check/{check_id}/knowledge-draft",
        headers=admin_headers,
        json={"title": "金额核验", "content": "已核验规则"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["application_ids"] == [str(application_id)]
    assert (
        client.get(
            "/api/v1/agent-results",
            headers=admin_headers,
            params={
                "business": "inspection",
                "scope_id": plan["id"],
                "run_id": str(run_id),
            },
        ).json()["total"]
        == 1
    )
    with SessionLocal() as db:
        row = db.get(AgentAnalysis, diagnostic_id)
        row.config = {"diagnosis_mode": "read_only"}
        with pytest.raises(ValueError, match="Unverified evidence"):
            validate_result(
                db,
                row,
                diagnosis("history-id"),
                [
                    {
                        "id": "history-id",
                        "source": "read_agent_history",
                        "data": {},
                    }
                ],
                [],
            )


def test_inspection_history_does_not_count_object_rollups_as_agent_results(client, admin_headers):
    plan = create_plan(client, admin_headers)
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "admin"))
        now = datetime.now(UTC)
        run_ids, check_ids = [], []
        for unified in (True, False):
            run = InspectionRun(
                plan_id=UUID(plan["id"]),
                plan_version=1,
                trigger="manual",
                requested_by=user.id,
                status="completed",
                snapshot={},
                window_start=now - timedelta(hours=1),
                window_end=now,
            )
            db.add(run)
            db.flush()
            run_ids.append(str(run.id))
            for index in range(2 if unified else 1):
                item = InspectionItem(
                    run_id=run.id,
                    plan_id=run.plan_id,
                    identity_hash=uuid4().hex,
                    record_id=f"qa-{index}",
                    record_version="1",
                    observed_at=now,
                    evidence={},
                    verdict="supported",
                    check_status="pass" if unified else None,
                    agent_status="completed",
                    agent_result={"summary": "全部检查通过"},
                )
                db.add(item)
                db.flush()
                if unified:
                    check = InspectionCheckResult(
                        run_id=run.id,
                        plan_id=run.plan_id,
                        item_id=item.id,
                        object_key=item.record_id,
                        check_id="semantic",
                        kind="agent",
                        execution_status="completed",
                        status="pass",
                        summary="回答切题",
                        result={"name": "回答质量评估", "summary": "回答切题"},
                    )
                    db.add(check)
                    db.flush()
                    check_ids.append(str(check.id))
        db.commit()
    params = {"business": "inspection", "scope_id": plan["id"]}
    response = client.get("/api/v1/agent-results", params=params, headers=admin_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total"] == 3
    assert {r["id"] for r in data["items"] if r["kind"] == "inspection_check"} == set(check_ids)
    for run_id, expected in zip(run_ids, (2, 1), strict=True):
        data = client.get(
            "/api/v1/agent-results", params={**params, "run_id": run_id}, headers=admin_headers
        ).json()
        assert data["total"] == expected
        assert {r["run_id"] for r in data["items"]} == {run_id}
    with SessionLocal() as db:
        db.get(InspectionCheckResult, UUID(check_ids[0])).deleted_at = now
        db.commit()
    data = client.get(
        "/api/v1/agent-results", params={**params, "run_id": run_ids[0]}, headers=admin_headers
    ).json()
    assert data["total"] == 1
    assert all(r["kind"] == "inspection_check" for r in data["items"])


def test_context_budgets_bound_database_reads_and_preserve_complete_records(
    client,
    admin_headers,
    incident,
):
    run_id, conversation_id = completed(incident["incident_id"], conversation=True)
    with SessionLocal() as db:
        run = db.get(AnalysisRun, run_id)
        now = datetime.now(UTC)
        for index in range(40):
            db.add(
                AnalysisMessage(
                    conversation_id=conversation_id,
                    role="user",
                    content="x" * 16000,
                    status="accepted",
                    run_id=run_id,
                    created_at=now + timedelta(seconds=index),
                )
            )
        db.commit()
        conversation = db.get(AnalysisConversation, conversation_id)
        history, previous = conversation_once_query_3(conversation, db, SimpleNamespace(id=uuid4()))
        assert len(history) == 12 and len(previous) == 1
        projected = recent_messages(history)
        assert sum(len(m["content"]) for m in projected) <= 12000
        assert len(history[0].content) == 16000
        assert len(json.dumps(evidence_catalog(run.evidence))) < 8000
        assert "详细证据" not in json.dumps(evidence_catalog(run.evidence), ensure_ascii=False)
        assert len(run.evidence[0]["data"]["original"]) == 20000


def test_older_user_constraints_remain_searchable_and_readable(client, admin_headers, incident):
    identifier, conversation_id = completed(incident["incident_id"], conversation=True)
    with SessionLocal() as db:
        row = db.get(AnalysisRun, identifier)
        row.candidate = {}
        sent_at = datetime.now(UTC)
        db.add_all(
            [
                AnalysisMessage(
                    conversation_id=conversation_id,
                    run_id=identifier,
                    role="user",
                    status="accepted",
                    content="特殊约束：仅核查测试服务",
                    created_at=sent_at,
                ),
                AnalysisMessage(
                    conversation_id=conversation_id,
                    run_id=identifier,
                    role="assistant",
                    status="committed",
                    content="收到范围约束",
                    created_at=sent_at + timedelta(seconds=1),
                ),
            ]
        )
        db.commit()
    response = client.get(
        "/api/v1/agent-results",
        headers=admin_headers,
        params={
            "business": "diagnosis",
            "scope_id": incident["incident_id"],
            "conversation_id": conversation_id,
            "query": "特殊约束",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total"] == 1 and data["items"][0]["outcome"] == "conversation"
    path = f"/api/v1/agent-results/conversation/{identifier}"
    response = client.get(path, headers=admin_headers, params={"section": "messages"})
    assert response.status_code == 200, response.text
    assert json.loads(response.json()["content"])[0]["content"] == "特殊约束：仅核查测试服务"
    assert (
        client.post(
            path + "/knowledge-draft",
            headers=admin_headers,
            json={"title": "无结论", "content": "不应发布"},
        ).status_code
        == 409
    )


def test_carry_budget_preserves_cited_evidence_without_growing_each_round():
    records = [{"id": str(i), "data": "原始证据" * 1000} for i in range(100)]
    result = carry_records(records, ["0"], max_items=10, max_chars=25000)
    assert result[0]["id"] == "0"
    assert len(result) <= 10 and len(json.dumps(result, ensure_ascii=False)) <= 25002
    assert len(result) < len(records) and records[99]["data"] == "原始证据" * 1000


def test_summary_queries_filter_denied_frozen_sources(
    client, admin_headers, incident, make_user, login
):
    actor = make_user("scoped", "operator")
    headers = {"Authorization": "Bearer " + login("scoped")["access_token"]}
    visible_id, _ = completed(incident["incident_id"])
    hidden_id, _ = completed(incident["incident_id"])
    with SessionLocal() as db:
        hidden = Application(code="hidden-source", name="受限资料")
        db.add(hidden)
        db.flush()
        db.get(AgentAnalysis, hidden_id).source_context = {"application_id": str(hidden.id)}
        db.commit()
        hidden_app_id = hidden.id
    assert (
        user_policy(client, admin_headers, actor, "application", hidden_app_id).status_code == 200
    )
    response = client.get(
        "/api/v1/agent-results",
        headers=headers,
        params={
            "business": "diagnosis",
            "scope_id": incident["incident_id"],
        },
    )
    assert response.status_code == 200, response.text
    assert [r["id"] for r in response.json()["items"]] == [str(visible_id)]
    assert response.json()["total"] == 1
    assert (
        client.get(f"/api/v1/agent-results/diagnosis/{hidden_id}", headers=headers).status_code
        == 404
    )
