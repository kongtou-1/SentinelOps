import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from test_ai import ai_config as ai_config
from test_ai import diagnosis
from test_ai import incident as incident
from test_ai import local_provider as local_provider

from app.db.session import SessionLocal
from app.models import (
    AgentAnalysis,
    AnalysisConversation,
    AnalysisEvent,
    AnalysisMessage,
    AnalysisRun,
    Role,
    User,
)
from app.utils.redaction import RedactedStream
from app.workers.conversations import conversation_once, interrupt_conversations, record


def create(client, headers, incident):
    response = client.post(
        f"/api/v1/incidents/{incident['incident_id']}/analysis-conversations",
        headers=headers,
        json={},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def send(client, headers, conversation, text="请诊断这次事故", key=None):
    response = client.post(
        f"/api/v1/analysis-conversations/{conversation}/messages",
        headers=headers,
        json={"client_message_id": key or str(uuid4()), "content": text},
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_owner_admin_and_viewer_boundaries(
    client, admin_headers, incident, make_user, login, ai_config
):
    for name, role in (("op1", "operator"), ("op2", "operator"), ("view", "viewer")):
        make_user(name, role)
    auth = {
        name: {"Authorization": "Bearer " + login(name)["access_token"]}
        for name in ("op1", "op2", "view")
    }
    identifier = create(client, auth["op1"], incident)
    message = send(client, auth["op1"], identifier)
    paths = [
        f"/analysis-conversations/{identifier}{suffix}"
        for suffix in ("", "/messages", "/events", "/runs", "/stream")
    ]
    paths.append("/analysis-runs/" + message["run_id"])
    for path in paths:
        assert client.get("/api/v1" + path, headers=auth["op2"]).status_code == 403
        assert client.get("/api/v1" + path, headers=auth["view"]).status_code == 403
    assert (
        client.get(
            f"/api/v1/analysis-conversations/{identifier}", headers=admin_headers
        ).status_code
        == 200
    )
    for headers in (admin_headers, auth["op2"]):
        assert (
            client.delete(
                f"/api/v1/analysis-conversations/{identifier}", headers=headers
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/api/v1/analysis-conversations/{identifier}/messages",
                headers=headers,
                json={"client_message_id": str(uuid4()), "content": "更改"},
            ).status_code
            == 403
        )
        for action in ("cancel", "adopt"):
            assert (
                client.post(
                    f"/api/v1/analysis-runs/{message['run_id']}/{action}", headers=headers
                ).status_code
                == 403
            )
    for name in ("op2", "view"):
        assert (
            client.get(
                f"/api/v1/incidents/{incident['incident_id']}/analysis-conversations",
                headers=auth[name],
            ).json()
            == []
        )
    assert (
        "analysis_conversations:read_all"
        in client.get("/api/v1/me", headers=admin_headers).json()["permissions"]
    )
    assert (
        client.delete(
            f"/api/v1/analysis-conversations/{identifier}", headers=auth["view"]
        ).status_code
        == 403
    )


@pytest.mark.parametrize("with_messages", [False, True])
def test_delete_hides_conversation_and_private_history(
    client, admin_headers, incident, ai_config, with_messages
):
    identifier = create(client, admin_headers, incident)
    run_id = None
    if with_messages:
        message = send(client, admin_headers, identifier)
        run_id = message["run_id"]
        client.post(f"/api/v1/analysis-runs/{run_id}/cancel", headers=admin_headers)
    with SessionLocal() as db:
        before = db.get(AnalysisConversation, UUID(identifier)).updated_at
    path = f"/api/v1/analysis-conversations/{identifier}"
    response = client.delete(path, headers=admin_headers)
    assert response.status_code == 204 and not response.content
    listing = client.get(
        f"/api/v1/incidents/{incident['incident_id']}/analysis-conversations",
        headers=admin_headers,
    )
    assert listing.json() == [] and listing.headers["X-Total-Count"] == "0"
    for suffix in ("", "/messages", "/runs", "/events", "/stream"):
        assert client.get(path + suffix, headers=admin_headers).status_code == 404
    assert client.delete(path, headers=admin_headers).status_code == 404
    assert (
        client.post(
            path + "/messages",
            headers=admin_headers,
            json={"client_message_id": str(uuid4()), "content": "继续"},
        ).status_code
        == 404
    )
    if run_id:
        assert (
            client.get(f"/api/v1/analysis-runs/{run_id}", headers=admin_headers).status_code
            == 404
        )
        for action in ("cancel", "adopt"):
            assert (
                client.post(
                    f"/api/v1/analysis-runs/{run_id}/{action}", headers=admin_headers
                ).status_code
                == 404
            )
    with SessionLocal() as db:
        row = db.get(AnalysisConversation, UUID(identifier))
        assert row.deleted_at is not None and row.updated_at >= before


@pytest.mark.parametrize("status", ["queued", "running", "cancelling"])
def test_delete_rejects_active_run(client, admin_headers, incident, ai_config, status):
    identifier = create(client, admin_headers, incident)
    message = send(client, admin_headers, identifier)
    with SessionLocal() as db:
        db.get(AnalysisRun, UUID(message["run_id"])).status = status
        db.commit()
    path = f"/api/v1/analysis-conversations/{identifier}"
    assert client.delete(path, headers=admin_headers).status_code == 409
    assert client.get(path, headers=admin_headers).status_code == 200
    with SessionLocal() as db:
        assert db.get(AnalysisConversation, UUID(identifier)).deleted_at is None
        assert db.get(AnalysisRun, UUID(message["run_id"])).status == status


def test_message_idempotency_pagination_and_cancel(client, admin_headers, incident, ai_config):
    identifier = create(client, admin_headers, incident)
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(
            pool.map(
                lambda _: send(
                    client, admin_headers, identifier, "password=should-not-be-stored", key
                ),
                range(3),
            )
        )
    assert len({m["id"] for m in rows}) == 1
    assert rows[0]["content"] == "password=[REDACTED]"
    response = client.get(
        f"/api/v1/analysis-conversations/{identifier}/messages?limit=1", headers=admin_headers
    )

    assert response.headers["X-Total-Count"] == "1"
    run_id = rows[0]["run_id"]
    for _ in range(2):
        result = client.post(f"/api/v1/analysis-runs/{run_id}/cancel", headers=admin_headers).json()
        assert result["status"] == "cancelled"
    assert not conversation_once()
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AnalysisRun)) == 1
        item = db.get(AnalysisConversation, UUID(identifier))
        item.deleted_at = datetime.now(UTC)
        db.commit()
    assert (
        client.get(
            f"/api/v1/analysis-conversations/{identifier}", headers=admin_headers
        ).status_code
        == 404
    )


def test_demoted_owner_loses_private_read_and_tool_access(
    client, incident, make_user, login, ai_config
):
    make_user("demoted", "operator")
    headers = {"Authorization": "Bearer " + login("demoted")["access_token"]}
    identifier = create(client, headers, incident)
    message = send(client, headers, identifier)
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "demoted"))
        user.role_id = db.scalar(select(Role.id).where(Role.code == "viewer"))
        db.commit()
    assert (
        client.get(f"/api/v1/analysis-conversations/{identifier}", headers=headers).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/v1/incidents/{incident['incident_id']}/analysis-conversations", headers=headers
        ).json()
        == []
    )
    assert conversation_once()
    with SessionLocal() as db:
        run = db.get(AnalysisRun, UUID(message["run_id"]))
        assert run.error_code == "requester_unavailable" and run.evidence == []


def test_adopt_is_immutable_idempotent_and_validated(client, admin_headers, incident, ai_config):
    identifier = create(client, admin_headers, incident)
    message = send(client, admin_headers, identifier, "私有聊天内容")
    run_id = UUID(message["run_id"])
    with SessionLocal() as db:
        run = db.get(AnalysisRun, run_id)
        run.status, run.started_at, run.finished_at = (
            "completed",
            datetime.now(UTC),
            datetime.now(UTC),
        )
        run.candidate = diagnosis()
        run.evidence = [
            {"id": "e1", "source": "get_container_status", "data": {"state": "stopped"}},
            {"id": "private-unreferenced", "data": "private"},
        ]
        db.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: client.post(
                    f"/api/v1/analysis-runs/{run_id}/adopt", headers=admin_headers
                ),
                range(2),
            )
        )
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    assert responses[0].json()["id"] == responses[1].json()["id"]
    published = responses[0].json()
    assert len(published["evidence"]) == 1 and "私有聊天" not in str(published)
    with SessionLocal() as db:
        db.get(AnalysisRun, run_id).candidate = diagnosis("invalid")
        db.commit()
        assert db.get(AgentAnalysis, UUID(published["id"])).result["summary"] == "容器停止"
    assert (
        client.delete(
            f"/api/v1/analysis-conversations/{identifier}", headers=admin_headers
        ).status_code
        == 204
    )
    with SessionLocal() as db:
        adopted = db.get(AgentAnalysis, UUID(published["id"]))
        assert adopted.deleted_at is None and adopted.result == published["result"]


def test_restart_keeps_partial_history_and_pending_input(
    client, admin_headers, incident, ai_config
):
    identifier = create(client, admin_headers, incident)
    message = send(client, admin_headers, identifier)
    run_id = UUID(message["run_id"])
    with SessionLocal() as db:
        db.get(AnalysisRun, run_id).status = "running"
        db.commit()
    record(run_id, "assistant_delta", {"request_index": 1, "text": "检查正在进行\n"})
    with SessionLocal() as db:
        interrupt_conversations(db)
        assert db.get(AnalysisRun, run_id).status == "interrupted"
        assert db.get(AnalysisMessage, UUID(message["id"])).status == "next_round"
        assert (
            db.scalar(select(AnalysisMessage).where(AnalysisMessage.role == "assistant")).status
            == "partial"
        )
    next_message = send(client, admin_headers, identifier, "继续诊断")
    assert next_message["run_id"] != str(run_id)
    events = client.get(
        f"/api/v1/analysis-conversations/{identifier}/events", headers=admin_headers
    ).json()
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    after = client.get(
        f"/api/v1/analysis-conversations/{identifier}/events?after_seq=2", headers=admin_headers
    ).json()
    assert after == events[2:]


def test_stream_redaction_across_fragments():
    stream = RedactedStream()
    assert stream.feed("正在读取\napi_key=sk-") == "正在读取\n"
    assert stream.feed("sensitive-secret") == ""
    assert stream.feed("\n") == "api_key=[REDACTED]\n"
    assert stream.feed("-----BEGIN PRIVATE KEY-----\n") == ""
    assert stream.feed("secret-line\n") == ""
    assert stream.feed("-----END PRIVATE KEY-----\n") == "[REDACTED PRIVATE KEY]\n"
    assert stream.feed('"password":\n') == '"'
    assert stream.feed('"hidden"\n') == 'password":\n"[REDACTED]"\n'


def test_natural_reply_without_candidate(
    client, admin_headers, incident, ai_config, local_provider
):
    local_provider.scenario = "chat_reply"
    identifier = create(client, admin_headers, incident)
    message = send(client, admin_headers, identifier, "解释用户补充与核验证据的区别")
    assert conversation_once()
    run = client.get(f"/api/v1/analysis-runs/{message['run_id']}", headers=admin_headers).json()
    assert run["status"] == "completed" and not run["candidate"]
    messages = client.get(
        f"/api/v1/analysis-conversations/{identifier}/messages", headers=admin_headers
    ).json()
    assert any("说明性回复" in m["content"] for m in messages if m["role"] == "assistant")
    assert (
        client.post(f"/api/v1/analysis-runs/{run['id']}/adopt", headers=admin_headers).status_code
        == 409
    )


@pytest.mark.parametrize(
    "source,asset",
    [
        ("propose_action_skill", None),
        ("list_skills", None),
        ("read_skill", None),
        ("get_container_status", str(uuid4())),
    ],
)
def test_adoption_rejects_proposal_as_proof_or_outside_asset(
    client, admin_headers, incident, ai_config, source, asset
):
    identifier = create(client, admin_headers, incident)
    message = send(client, admin_headers, identifier)
    with SessionLocal() as db:
        run = db.get(AnalysisRun, UUID(message["run_id"]))
        run.status = "completed"
        run.candidate = diagnosis()
        run.evidence = [{"id": "e1", "source": source, "asset_id": asset, "data": {}}]
        db.commit()
    assert (
        client.post(
            f"/api/v1/analysis-runs/{message['run_id']}/adopt", headers=admin_headers
        ).status_code
        == 409
    )


def test_real_sdk_default_readonly_diagnosis(client, admin_headers, incident, local_provider):
    from scripts.seed_knowledge import main

    main()
    identifier = create(client, admin_headers, incident)
    message = send(client, admin_headers, identifier)
    assert conversation_once()
    result = client.get(f"/api/v1/analysis-runs/{message['run_id']}", headers=admin_headers).json()
    assert result["status"] == "completed", result
    loaded = next(e for e in result["evidence"] if e["source"] == "read_skill")
    assert loaded["data"]["code"] == "sre_diagnosis"
    assert result["candidate"]["proposal"] is None
    assert "propose_action_skill" not in {c.get("tool") for c in result["tool_calls"]}
    assert loaded["id"] not in result["candidate"]["evidence_ids"]
    adopted = client.post(f"/api/v1/analysis-runs/{message['run_id']}/adopt", headers=admin_headers)
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["result"]["proposal"] is None


def test_real_sdk_live_supplement_stream_and_adoption(
    client, admin_headers, incident, ai_config, local_provider
):
    local_provider.scenario = "chat_slow"
    skill = client.post(
        "/api/v1/skills",
        headers=admin_headers,
        json={
            "code": "container_unavailable",
            "title": "容器不可用诊断",
            "content": "先核对容器状态，再读取日志；提案交由人工审批。",
        },
    ).json()
    identifier = create(client, admin_headers, incident)
    first = send(client, admin_headers, identifier)
    thread = threading.Thread(target=conversation_once)
    thread.start()
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        with SessionLocal() as db:
            if db.scalar(select(AnalysisEvent.id).where(AnalysisEvent.kind == "model_started")):
                break
        time.sleep(0.1)
    assert thread.is_alive(), "Worker ended before live supplement"
    supplement = send(client, admin_headers, identifier, "刚才已经发生两次进程退出")
    assert supplement["run_id"] == first["run_id"]
    saw_live_delta = False
    while thread.is_alive() and time.monotonic() < deadline:
        with SessionLocal() as db:
            saw_live_delta |= (
                db.scalar(select(AnalysisEvent.id).where(AnalysisEvent.kind == "assistant_delta"))
                is not None
            )
        time.sleep(0.1)
    thread.join(5)
    assert not thread.is_alive()
    result = client.get(f"/api/v1/analysis-runs/{first['run_id']}", headers=admin_headers).json()
    assert result["status"] == "completed", result
    assert result["candidate"]["summary"] and result["model_requests"] > 1
    loaded = next(e for e in result["evidence"] if e["source"] == "read_skill")
    assert loaded["data"]["id"] == skill["id"] and loaded["data"]["version"] == 1
    assert saw_live_delta
    messages = client.get(
        f"/api/v1/analysis-conversations/{identifier}/messages", headers=admin_headers
    ).json()
    assert next(m for m in messages if m["id"] == supplement["id"])["status"] == "accepted"
    assert any("两次进程退出" in m["content"] for m in messages if m["role"] == "assistant")
    assert (
        client.get(
            f"/api/v1/incidents/{incident['incident_id']}/analysis", headers=admin_headers
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/analysis-runs/{first['run_id']}/adopt", headers=admin_headers
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "scenario,code",
    [
        ("401", "authentication_failed"),
        ("malicious", "tool_not_allowed"),
        ("fake_evidence", "invalid_response"),
        ("context_limit", "context_limit"),
    ],
)
def test_real_sdk_rejects_errors_and_bad_evidence(
    client, admin_headers, incident, ai_config, local_provider, scenario, code
):
    local_provider.scenario = scenario
    identifier = create(client, admin_headers, incident)
    message = send(client, admin_headers, identifier)
    assert conversation_once()
    result = client.get(f"/api/v1/analysis-runs/{message['run_id']}", headers=admin_headers).json()
    assert result["status"] == "failed", result
    assert result["error_code"] == code, result
    assert (
        client.post(
            f"/api/v1/analysis-runs/{message['run_id']}/adopt", headers=admin_headers
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "scenario,feedback",
    [
        ("chat_retry_evidence", "invalid_evidence_ids"),
        ("chat_retry_knowledge", "invalid_knowledge_ids"),
        ("chat_retry_schema", "field_errors"),
    ],
)
def test_real_sdk_corrects_rejected_submission(
    client, admin_headers, incident, ai_config, local_provider, scenario, feedback
):
    local_provider.scenario = scenario
    identifier = create(client, admin_headers, incident)
    message = send(client, admin_headers, identifier)
    assert conversation_once()
    run = client.get(f"/api/v1/analysis-runs/{message['run_id']}", headers=admin_headers).json()
    assert run["status"] == "completed", run
    failed = next(e for e in run["evidence"] if e["source"] == "get_recent_logs")
    assert failed["error_code"] == "ssh_unavailable"
    refs = set(run["candidate"]["evidence_ids"])
    for cause in run["candidate"]["possible_causes"]:
        refs.update(cause["evidence_ids"])
    assert failed["id"] not in refs
    assert any(
        failed["id"] in gap and "ssh_unavailable" in gap for gap in run["candidate"]["data_gaps"]
    )
    events = client.get(
        f"/api/v1/analysis-conversations/{identifier}/events?limit=1000", headers=admin_headers
    ).json()
    submissions = [
        e
        for e in events
        if e["kind"] == "tool_finished" and e["data"].get("tool") == "submit_diagnosis"
    ]
    assert len(submissions) == 2
    assert submissions[0]["data"]["error_code"] == "invalid_response"
    assert submissions[1]["data"]["result"]["source"] == "submit_diagnosis"
    assert feedback in json.dumps(events)
    if scenario == "chat_retry_schema":
        assert '"input"' not in json.dumps(
            [
                e
                for e in events
                if e["kind"] == "sdk_event" and e["data"].get("type") == "tool/result"
            ]
        )
    assert sum(e["kind"] == "diagnosis_candidate" for e in events) == 1
    adopted = client.post(f"/api/v1/analysis-runs/{message['run_id']}/adopt", headers=admin_headers)
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["result"]["data_gaps"] == run["candidate"]["data_gaps"]


def test_unrepaired_submission_cannot_finish_or_be_adopted(
    client, admin_headers, incident, ai_config, local_provider
):
    local_provider.scenario = "chat_abandon_submission"
    identifier = create(client, admin_headers, incident)
    message = send(client, admin_headers, identifier)
    assert conversation_once()
    run = client.get(f"/api/v1/analysis-runs/{message['run_id']}", headers=admin_headers).json()
    assert run["status"] == "failed" and run["error_code"] == "invalid_response", run
    assert not run["candidate"]
    assert (
        client.post(
            f"/api/v1/analysis-runs/{message['run_id']}/adopt", headers=admin_headers
        ).status_code
        == 409
    )


def test_rejected_submissions_share_tool_budget(client, admin_headers, incident, ai_config):
    from app.services.diagnosis.analyses import InvalidDiagnosisReferences
    from app.workers.conversations import CHAT_TOOLS, ConversationTools

    identifier = create(client, admin_headers, incident)
    message = send(client, admin_headers, identifier)
    run_id = UUID(message["run_id"])
    with SessionLocal() as db:
        db.get(AnalysisRun, run_id).status = "running"
        db.commit()
    tools = ConversationTools(run_id, row_model=AnalysisRun, definitions=CHAT_TOOLS)
    for _ in range(12):
        with pytest.raises(InvalidDiagnosisReferences) as rejected:
            tools.call("submit_diagnosis", diagnosis("invented"))
        assert rejected.value.invalid_evidence_ids == ["invented"]
        assert not tools.blocked
    with pytest.raises(ValueError, match="Tool call limit exceeded"):
        tools.call("submit_diagnosis", diagnosis("invented"))
    assert tools.blocked == "tool_limit_exceeded"


def test_round_timeout_keeps_saved_records(
    client, admin_headers, incident, ai_config, local_provider, monkeypatch
):
    local_provider.scenario = "chat_slow"
    monkeypatch.setattr(ai_config, "ai_timeout_seconds", 2)
    identifier = create(client, admin_headers, incident)
    message = send(client, admin_headers, identifier)
    assert conversation_once()
    run = client.get(f"/api/v1/analysis-runs/{message['run_id']}", headers=admin_headers).json()
    assert run["status"] == "failed" and run["error_code"] == "analysis_timeout"
    events = client.get(
        f"/api/v1/analysis-conversations/{identifier}/events?limit=1000", headers=admin_headers
    ).json()
    assert events[-1]["kind"] == "run_failed"
    assert (
        client.get(
            f"/api/v1/analysis-conversations/{identifier}/messages", headers=admin_headers
        ).json()[0]["id"]
        == message["id"]
    )


@pytest.mark.parametrize("action,expected", [("cancel", "cancelled"), ("config", "failed")])
def test_running_stop_and_configuration_boundary(
    client, admin_headers, incident, ai_config, local_provider, monkeypatch, action, expected
):
    local_provider.scenario = "chat_slow"
    identifier = create(client, admin_headers, incident)
    message = send(client, admin_headers, identifier)
    thread = threading.Thread(target=conversation_once)
    thread.start()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        with SessionLocal() as db:
            if db.scalar(select(AnalysisEvent.id).where(AnalysisEvent.kind == "model_started")):
                break
        time.sleep(0.1)
    assert thread.is_alive()
    if action == "cancel":
        client.post(f"/api/v1/analysis-runs/{message['run_id']}/cancel", headers=admin_headers)
    else:
        from app.models import AgentConfig

        with SessionLocal() as db:
            db.get(AgentConfig, "diagnosis").version += 1
            db.commit()
    thread.join(8)
    assert not thread.is_alive()
    result = client.get(f"/api/v1/analysis-runs/{message['run_id']}", headers=admin_headers).json()
    assert result["status"] == expected, result
    assert result["error_code"] == ("cancelled" if action == "cancel" else "configuration_changed")
