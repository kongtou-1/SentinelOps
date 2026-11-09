import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select
from tokenizers import Tokenizer

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.integrations.diagnostic_gateway import DiagnosticGateway
from app.models import (
    AgentAnalysis,
    AlertEvent,
    Container,
    Incident,
    IncidentAlert,
    IncidentTimeline,
    KnowledgeDocument,
    KnowledgeSkill,
    LLMEndpoint,
    Server,
    User,
)
from app.services.diagnosis.analyses import complete, interrupt_runs
from app.services.diagnosis.tools import PUBLIC_TOOLS, DiagnosticTools
from app.services.knowledge.index import build_index, search, split_content
from app.utils.redaction import redact
from app.workers.ai import analyze_once, index_once


@pytest.fixture
def ai_config(monkeypatch, tmp_path):
    settings = get_settings()
    key = tmp_path / "ai.key"
    key.write_text("local-dummy-key-" + "x" * 32)
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "ai_key_file", key)
    monkeypatch.setattr(settings, "ai_timeout_seconds", 30)
    from app.services.agent_config import import_environment

    with SessionLocal() as db:
        import_environment(db)
    return settings


@pytest.fixture
def incident(client, admin_headers):
    with SessionLocal() as db:
        now = datetime.now(UTC)
        server = Server(
            name="ai-test-server",
            host="127.0.0.1",
            node_exporter_target="local:9100",
            cadvisor_target="local:8080",
        )
        db.add(server)
        db.flush()
        container = Container(
            server_id=server.id,
            docker_id="/docker/" + "a" * 64,
            name="test-worker",
            image="local",
            status="stopped",
            labels={},
            last_seen_at=now,
        )
        row = Incident(
            title="Docker 容器停止不可用",
            severity="warning",
            status="open",
            started_at=now,
            last_alert_started_at=now,
        )
        db.add_all([container, row])
        db.flush()
        event = AlertEvent(
            fingerprint=uuid4().hex,
            server_id=server.id,
            severity="warning",
            status="firing",
            starts_at=now,
            labels={"container_id": str(container.id)},
            annotations={"summary": row.title},
        )
        db.add(event)
        db.flush()
        db.add(IncidentAlert(incident_id=row.id, alert_event_id=event.id))
        db.commit()
        return {
            "incident_id": str(row.id),
            "server_id": str(server.id),
            "container_id": str(container.id),
        }


def enqueue(client, auth, incident, force=False):
    response = client.post(
        f"/api/v1/incidents/{incident['incident_id']}/analyze", headers=auth, json={"force": force}
    )
    assert response.status_code in (200, 202), response.text
    return response


def running(client, auth, incident):
    row = enqueue(client, auth, incident).json()
    with SessionLocal() as db:
        record = db.get(AgentAnalysis, UUID(row["id"]))
        record.status, record.started_at = "running", datetime.now(UTC)
        db.commit()
    return UUID(row["id"])


def diagnosis(evidence="e1", knowledge=None, proposal=None):
    return {
        "summary": "容器停止",
        "possible_causes": [{"description": "进程退出", "evidence_ids": [evidence]}],
        "impact": "服务不可用",
        "evidence_ids": [evidence],
        "knowledge_ids": knowledge or [],
        "suggested_checks": ["检查退出码"],
        "data_gaps": [],
        "proposal": proposal,
        "requires_human_approval": True,
    }


def proposal(container_id):
    return {
        "skill": "restart_container",
        "parameters": {"container_id": container_id},
        "rationale": "人工确认恢复条件",
        "impact": "短暂中断请求",
    }


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
def test_ai_rbac(client, make_user, login, ai_config, role):
    make_user(role, role)
    auth = {"Authorization": "Bearer " + login(role)["access_token"]}
    for path in ("/knowledge", "/skills"):
        assert client.get("/api/v1" + path, headers=auth).status_code == 200
    body = {"title": "知识", "content": "证据"}
    assert client.post("/api/v1/knowledge", headers=auth, json=body).status_code == (
        202 if role == "admin" else 403
    )
    assert client.post(
        "/api/v1/skills", headers=auth, json={**body, "code": "sop"}
    ).status_code == (201 if role == "admin" else 403)
    assert client.post(
        f"/api/v1/incidents/{uuid4()}/analyze", headers=auth, json={}
    ).status_code == (403 if role == "viewer" else 404)


@pytest.mark.parametrize("path", ["/knowledge", "/skills", "/analyses/" + str(uuid4())])
def test_ai_requires_login(client, path):
    assert client.get("/api/v1" + path).status_code == 401


def test_disabled_or_missing_credentials(client, admin_headers, incident, ai_config, monkeypatch):
    monkeypatch.setattr(ai_config, "ai_enabled", False)
    assert (
        client.post(
            f"/api/v1/incidents/{incident['incident_id']}/analyze", headers=admin_headers, json={}
        ).status_code
        == 503
    )
    monkeypatch.setattr(ai_config, "ai_enabled", True)
    ai_config.ai_key_file.unlink()
    from app.models import AgentChannel, AgentConfig

    with SessionLocal() as db:
        for channel in db.scalars(select(AgentChannel).where(AgentChannel.module == "diagnosis")):
            channel.enabled = False
        db.get(AgentConfig, "diagnosis").version += 1
        db.commit()
    assert (
        client.post(
            f"/api/v1/incidents/{incident['incident_id']}/analyze", headers=admin_headers, json={}
        ).status_code
        == 503
    )


def test_queue_concurrency_and_unique_index(client, admin_headers, incident, ai_config):
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(lambda _: enqueue(client, admin_headers, incident).json(), range(3)))
    assert len({r["id"] for r in rows}) == 1
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AgentAnalysis)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(IncidentTimeline)
                .where(IncidentTimeline.event_type == "analysis_queued")
            )
            == 1
        )


def test_cache_force_and_input_change(client, admin_headers, incident, ai_config):
    identifier = running(client, admin_headers, incident)
    with SessionLocal() as db:
        complete(db, identifier, diagnosis(), [{"id": "e1"}], [], [])
    cached = enqueue(client, admin_headers, incident)
    assert cached.status_code == 200 and cached.json()["id"] == str(identifier)
    forced = enqueue(client, admin_headers, incident, True).json()
    assert forced["id"] != str(identifier)
    with SessionLocal() as db:
        row = db.get(AgentAnalysis, UUID(forced["id"]))
        row.status = "running"
        db.commit()
        complete(db, row.id, diagnosis(), [{"id": "e1"}], [], [])
    client.post(
        f"/api/v1/incidents/{incident['incident_id']}/comments",
        headers=admin_headers,
        json={"comment": "新证据"},
    )
    changed = enqueue(client, admin_headers, incident).json()
    assert changed["id"] != forced["id"]


@pytest.mark.parametrize("status", ["closed", "merged"])
def test_terminal_incident_blocks_start_and_completion(
    client, admin_headers, incident, ai_config, status
):
    identifier = running(client, admin_headers, incident)
    with SessionLocal() as db:
        db.get(Incident, UUID(incident["incident_id"])).status = status
        db.commit()
        complete(db, identifier, diagnosis(), [{"id": "e1"}], [], [])
        assert db.get(AgentAnalysis, identifier).error_code == "incident_changed"
        assert db.get(AgentAnalysis, identifier).result == {}
    assert (
        client.post(
            f"/api/v1/incidents/{incident['incident_id']}/analyze",
            headers=admin_headers,
            json={"force": True},
        ).status_code
        == 409
    )


@pytest.mark.parametrize("change", ["disable", "demote", "delete"])
def test_requester_loses_permission(client, admin_headers, incident, ai_config, change):
    identifier = running(client, admin_headers, incident)
    with SessionLocal() as db:
        actor = db.get(User, db.get(AgentAnalysis, identifier).requested_by)
        if change == "disable":
            actor.is_active = False
        elif change == "delete":
            actor.deleted_at = datetime.now(UTC)
        else:
            from app.models import Role

            actor.role_id = db.scalar(select(Role.id).where(Role.code == "viewer"))
        db.commit()
        complete(db, identifier, diagnosis(), [{"id": "e1"}], [], [])
        assert db.get(AgentAnalysis, identifier).error_code == "requester_unavailable"


@pytest.mark.parametrize("invalid", ["citation", "knowledge", "target", "risk", "execution"])
def test_invalid_result_is_never_completed(client, admin_headers, incident, ai_config, invalid):
    identifier = running(client, admin_headers, incident)
    result = diagnosis()
    if invalid == "citation":
        result["evidence_ids"] = ["invented"]
    elif invalid == "knowledge":
        result["knowledge_ids"] = ["invented"]
    else:
        result["proposal"] = proposal(incident["container_id"])
        if invalid == "target":
            result["proposal"]["parameters"]["container_id"] = str(uuid4())
        elif invalid == "risk":
            result["proposal"]["risk"] = "low"
        else:
            result["proposal"]["executable"] = True
    with SessionLocal() as db:
        complete(db, identifier, result, [{"id": "e1"}], [], [])
        row = db.get(AgentAnalysis, identifier)
        assert row.status == "failed" and row.error_code == "invalid_response" and not row.result


def test_interruption_recovers_queue(client, admin_headers, incident, ai_config):
    identifier = running(client, admin_headers, incident)
    with SessionLocal() as db:
        document = KnowledgeDocument(title="pending", content="text", index_status="running")
        db.add(document)
        db.commit()
        interrupt_runs(db)
        assert db.get(AgentAnalysis, identifier).status == "interrupted"
        assert db.get(KnowledgeDocument, document.id).index_status == "queued"
    assert enqueue(client, admin_headers, incident).json()["id"] != str(identifier)


def test_tools_scope_schema_and_budget(client, admin_headers, incident, ai_config):
    identifier = running(client, admin_headers, incident)
    tools = DiagnosticTools(identifier)
    with pytest.raises(ValueError):
        tools.call("bash", {"command": "touch a"})
    with pytest.raises(ValueError):
        tools.call("get_container_status", {"asset_id": str(uuid4())})
    with pytest.raises(ValidationError):
        tools.call("get_server_metrics", {"asset_id": incident["server_id"], "metric": "up; shell"})
    with pytest.raises(ValidationError):
        tools.call("get_recent_logs", {"asset_id": incident["container_id"], "command": "rm"})
    tools = DiagnosticTools(identifier)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: tools.call("get_alert_context", {}), range(12)))
    with pytest.raises(ValueError):
        tools.call("get_alert_context", {})
    assert len(tools.calls) == 12


def test_missing_ssh_is_explicit(client, admin_headers, incident, ai_config):
    tools = DiagnosticTools(running(client, admin_headers, incident))
    result = tools.call("get_recent_logs", {"asset_id": incident["container_id"]})
    assert result["error_code"] == "ssh_unavailable"
    status = tools.call("get_container_status", {"asset_id": incident["container_id"]})
    assert status["data"]["inventory_status"] == "stopped"
    assert status["data"]["ssh"]["error_code"] == "ssh_unavailable"
    with SessionLocal() as db:
        complete(db, tools.analysis_id, diagnosis(status["id"]), tools.evidence, [], tools.calls)
        row = db.get(AgentAnalysis, tools.analysis_id)
        assert row.status == "completed"
        for record in (result, status):
            assert any(
                record["id"] in gap and "ssh_unavailable" in gap for gap in row.result["data_gaps"]
            )


@pytest.mark.parametrize(
    "tool,asset,metric",
    [
        ("get_server_metrics", "server_id", "host_disk_percent"),
        ("get_container_metrics", "container_id", "container_memory_bytes"),
    ],
)
def test_metric_summary_keeps_peaks_gaps_and_raw_detail(
    client, admin_headers, incident, ai_config, monkeypatch, tool, asset, metric
):
    values = [[i, str(20 + i % 4)] for i in range(61)]
    values[13] = [13, "99.125"]
    values[24] = [24, None]
    values[-1] = [60, None]
    original = {
        "resultType": "matrix",
        "availability": "stale",
        "reason": "data_stale",
        "last_sample_at": 59,
        "result": [{"metric": {"device": "/dev/sdc", "mountpoint": "/var/lib"}, "values": values}],
    }
    monkeypatch.setattr("app.services.diagnosis.tools.metrics_result", lambda *_: original)
    tools = DiagnosticTools(running(client, admin_headers, incident))
    args = {"asset_id": incident[asset], "metric": metric}
    result = tools.call(tool, args)["data"]
    assert result["detail"] == "summary"
    series = result["series"]
    assert series["availability"] == "stale" and series["last_sample_at"] == 59
    assert series["result"][0]["metric"] == original["result"][0]["metric"]
    summary = series["result"][0]["summary"]
    assert summary["sample_count"] == 61 and summary["invalid_sample_count"] == 2
    assert summary["first"] == values[0] and summary["last"] == values[-1]
    assert summary["max"] == values[13] and summary["min"] == values[0]
    assert "values" not in series["result"][0]
    raw = tools.call(tool, {**args, "detail": "raw"})["data"]
    assert raw["series"] == original
    assert len(json.dumps(series)) < len(json.dumps(original)) / 2


def test_agent_skill_catalog_and_versioned_loading(client, admin_headers, incident, ai_config):
    body = {
        "code": "container_check",
        "title": "容器诊断",
        "tags": ["container"],
        "content": "## 步骤\n用 get_container_status 检查状态，再读取日志。",
    }
    skill = client.post("/api/v1/skills", headers=admin_headers, json=body).json()
    tools = DiagnosticTools(running(client, admin_headers, incident))
    catalog = tools.call("list_skills", {})["data"]
    assert catalog == [{key: skill[key] for key in ("id", "code", "version", "title", "tags")}]
    loaded = tools.call("read_skill", {"skill_id": skill["id"], "version": skill["version"]})
    assert loaded["data"]["content"] == body["content"]
    assert tools.calls[-1]["evidence_id"] == loaded["id"]
    assert tools.evidence[-1]["data"]["version"] == skill["version"]
    updated = client.put(
        "/api/v1/skills/" + skill["id"],
        headers=admin_headers,
        json={**body, "content": "## 步骤\n先核对最新告警时间。"},
    ).json()
    assert (
        tools.call("read_skill", {"skill_id": skill["id"], "version": skill["version"]})[
            "error_code"
        ]
        == "skill_version_changed"
    )
    assert (
        tools.call("read_skill", {"skill_id": skill["id"], "version": updated["version"]})["data"][
            "content"
        ]
        == updated["content"]
    )


@pytest.mark.parametrize("state", ["disabled", "deleted", "missing"])
def test_agent_skill_unavailable_cannot_be_loaded(
    client, admin_headers, incident, ai_config, state
):
    with SessionLocal() as db:
        skill = KnowledgeSkill(code="hidden", title="隐藏 Skill", content="Do not expose")
        skill.enabled = state != "disabled"
        if state == "deleted":
            skill.deleted_at = datetime.now(UTC)
        db.add(skill)
        db.commit()
        identifier = str(uuid4()) if state == "missing" else str(skill.id)
    tools = DiagnosticTools(running(client, admin_headers, incident))
    catalog = tools.call("list_skills", {})["data"]
    assert all(item["id"] != identifier for item in catalog)
    assert tools.call("read_skill", {"skill_id": identifier, "version": 1})["error_code"] == (
        "skill_unavailable"
    )


@pytest.mark.parametrize("source", ["list_skills", "read_skill"])
def test_skill_instructions_cannot_be_cited_as_observed_facts(
    client, admin_headers, incident, ai_config, source
):
    identifier = running(client, admin_headers, incident)
    with SessionLocal() as db:
        complete(db, identifier, diagnosis(), [{"id": "e1", "source": source}], [], [])
        assert db.get(AgentAnalysis, identifier).error_code == "invalid_response"


def test_builtin_agent_skills_upgrade_preserves_manual_edits():
    from scripts.seed_knowledge import SOPS, main

    code, title, content = SOPS[0]
    with SessionLocal() as db:
        original = KnowledgeSkill(code=code, title=title, content=content, enabled=False)
        custom = KnowledgeSkill(code=SOPS[1][0], title="人工修改", content="自定义工作步骤")
        db.add_all([original, custom])
        db.commit()
        original_id, custom_id = original.id, custom.id
    main()
    main()
    with SessionLocal() as db:
        original, custom = db.get(KnowledgeSkill, original_id), db.get(KnowledgeSkill, custom_id)
        assert original.version == 2 and original.enabled is False
        assert "## 何时使用" in original.content and "get_server_metrics" in original.content
        assert custom.version == 1 and custom.content == "自定义工作步骤"
        assert db.scalar(select(func.count()).select_from(KnowledgeSkill)) == 5


def test_default_diagnostic_skill_seed_preserves_admin_changes():
    from scripts.seed_knowledge import main

    main()
    with SessionLocal() as db:
        skill = db.scalar(select(KnowledgeSkill).where(KnowledgeSkill.code == "sre_diagnosis"))
        identifier = skill.id
        skill.content, skill.enabled, skill.version = "人工维护的只读诊断指引", False, 2
        db.commit()
    main()
    with SessionLocal() as db:
        skill = db.get(KnowledgeSkill, identifier)
        assert skill.content == "人工维护的只读诊断指引"
        assert skill.enabled is False and skill.version == 2
        skill.deleted_at = datetime.now(UTC)
        db.commit()
    main()
    with SessionLocal() as db:
        assert db.get(KnowledgeSkill, identifier).deleted_at is not None
        assert db.scalar(select(func.count()).select_from(KnowledgeSkill)) == 5


def test_gateway_auth_expiration_and_tool_roster(client, admin_headers, incident, ai_config):
    gateway = DiagnosticGateway(DiagnosticTools(running(client, admin_headers, incident)))
    with TestClient(gateway.app()) as local:
        assert local.post("/chat/completions", json={}).status_code == 401
        assert (
            local.post(
                "/chat/completions",
                headers={"Authorization": "Bearer " + gateway.token},
                json={"tools": [{"function": {"name": "bash"}}]},
            ).status_code
            == 400
        )
        assert gateway.error == "unexpected_tools"
        gateway.expires = 0
        assert (
            local.post(
                "/mcp", headers={"Authorization": "Bearer " + gateway.token}, json={}
            ).status_code
            == 401
        )


@pytest.mark.parametrize("metric", ["llm_latency_avg_seconds", "llm_latency_p95_seconds"])
def test_llm_latency_tool_returns_second_units(
    client, admin_headers, incident, ai_config, monkeypatch, metric
):
    with SessionLocal() as db:
        endpoint = LLMEndpoint(
            name="latency", base_url="http://local", model="test", encrypted_api_key="encrypted"
        )
        db.add(endpoint)
        db.flush()
        db.scalar(select(AlertEvent)).llm_endpoint_id = endpoint.id
        identifier = str(endpoint.id)
        db.commit()
    queries = []

    def prom(path, params):
        queries.append(params["query"])
        return {
            "resultType": "matrix",
            "result": [{"metric": {"endpoint_id": identifier}, "values": [[1, "0.25"]]}],
        }

    monkeypatch.setattr("app.services.diagnosis.tools.prometheus", prom)
    result = DiagnosticTools(running(client, admin_headers, incident)).call(
        "get_llm_metrics", {"asset_id": identifier, "metric": metric}
    )
    assert result["data"]["unit"] == "seconds"
    assert result["data"]["series"]["result"][0]["values"][0][1] == "0.25"
    assert "duration_seconds" in queries[0]
    assert ("histogram_quantile" in queries[0]) == (metric == "llm_latency_p95_seconds")


@pytest.mark.parametrize(
    "field,value",
    [
        ("ai_base_url", "http://changed-provider"),
        ("ai_model", "changed-model"),
        ("ai_timeout_seconds", 29),
    ],
)
def test_queued_analysis_configuration_change_stops_before_provider(
    client, admin_headers, incident, ai_config, monkeypatch, field, value
):
    identifier = enqueue(client, admin_headers, incident).json()["id"]
    if field == "ai_timeout_seconds":
        monkeypatch.setattr(ai_config, field, value)
    else:
        from app.models import AgentChannel, AgentConfig

        with SessionLocal() as db:
            channel = db.scalar(select(AgentChannel).where(AgentChannel.module == "diagnosis"))
            setattr(channel, "base_url" if field == "ai_base_url" else "model", value)
            db.get(AgentConfig, "diagnosis").version += 1
            db.commit()
    monkeypatch.setattr(
        "app.workers.ai.HarnessAdapter.run",
        lambda *_a, **_k: pytest.fail("Provider must not be called"),
    )
    assert analyze_once()
    row = client.get("/api/v1/analyses/" + identifier, headers=admin_headers).json()
    assert row["status"] == "failed" and row["error_code"] == "configuration_changed"
    assert not row["tool_calls"]


def test_redaction():
    value = {
        "Authorization": "Bearer very-secret",
        "nested": ["password=abc sk-123456", {"api_key": "x"}],
        "log": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----",
    }
    text = json.dumps(redact(value))
    for secret in ("very-secret", "abc", "123456"):
        assert secret not in text


def test_real_vectors_versioning_and_tags(client, admin_headers):
    docs = []
    for title, content, tags in (
        ("磁盘耗尽", "磁盘空间不足，检查文件系统剩余空间和 inode。", ["disk"]),
        ("容器故障", "Docker 容器停止不可用，检查退出码与日志。", ["container"]),
        ("LLM 认证", "API 401 认证失败，人工检查密钥。", ["llm"]),
    ):
        row = client.post(
            "/api/v1/knowledge",
            headers=admin_headers,
            json={"title": title, "content": content, "tags": tags},
        ).json()
        docs.append(row)
        assert index_once()
    results = client.get(
        "/api/v1/knowledge/search",
        headers=admin_headers,
        params={"q": "Docker 容器停止了怎么办", "limit": 3},
    ).json()
    assert results[0]["document_id"] == docs[1]["id"]
    tagged = client.get(
        "/api/v1/knowledge/search", headers=admin_headers, params={"q": "故障", "tag": "disk"}
    ).json()
    assert len(tagged) == 1 and tagged[0]["document_id"] == docs[0]["id"]
    identifier = docs[1]["id"]
    response = client.put(
        f"/api/v1/knowledge/{identifier}",
        headers=admin_headers,
        json={"title": "容器故障", "content": "新的诊断步骤", "enabled": False},
    )
    assert response.json()["version"] == 2
    with SessionLocal() as db:
        assert all(r["document_id"] != identifier for r in search(db, "容器"))
    assert index_once()
    client.delete(f"/api/v1/knowledge/{identifier}", headers=admin_headers)
    assert client.get(f"/api/v1/knowledge/{identifier}", headers=admin_headers).status_code == 404
    assert results[0]["content"]  # The earlier immutable citation snapshot remains usable.


def test_split_token_budget_and_overlap():
    content = "# 内存故障\n\n内存持续增长，检查容器日志和 OOM。\n\n" * 60
    chunks = split_content("内存诊断", content)
    tokenizer = Tokenizer.from_file(str(get_settings().embedding_model_dir / "tokenizer.json"))
    tokenizer.no_padding()
    tokenizer.no_truncation()
    assert len(chunks) > 2
    assert all(len(tokenizer.encode(c, add_special_tokens=False).ids) <= 400 for c in chunks)
    assert chunks[0][-20:] in chunks[1]


def test_index_failure_retry_and_stale_publish(client, admin_headers, monkeypatch):
    body = {"title": "doc", "content": "原始版本"}
    row = client.post("/api/v1/knowledge", headers=admin_headers, json=body).json()
    with monkeypatch.context() as patch:
        patch.setattr("app.workers.ai.encoder", lambda: (_ for _ in ()).throw(ValueError()))
        assert index_once()
    assert (
        client.get(f"/api/v1/knowledge/{row['id']}", headers=admin_headers).json()["index_status"]
        == "failed"
    )
    assert (
        client.post(f"/api/v1/knowledge/{row['id']}/reindex", headers=admin_headers).status_code
        == 202
    )
    assert index_once()
    with SessionLocal() as db:
        assert not build_index(db, UUID(row["id"]), 1, [], [])
        assert (
            db.scalar(
                select(KnowledgeDocument).where(KnowledgeDocument.id == UUID(row["id"]))
            ).version
            == 2
        )


@pytest.fixture
def local_provider(ai_config, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import mock_diagnosis

    mock_diagnosis.scenario = "normal"
    service = ThreadingHTTPServer(("127.0.0.1", 0), mock_diagnosis.Handler)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(ai_config, "ai_base_url", f"http://127.0.0.1:{service.server_port}")
    from app.models import AgentChannel

    with SessionLocal() as db:
        for channel in db.scalars(select(AgentChannel)):
            channel.base_url = ai_config.ai_base_url
        db.commit()
    monkeypatch.setattr(
        "app.services.diagnosis.tools.metrics_result",
        lambda *_: {"result": [{"metric": {}, "values": [[1, "20"], [2, "30"]]}]},
    )
    yield mock_diagnosis
    service.shutdown()
    service.server_close()
    thread.join(2)


def test_real_harness_mcp_and_vectors(client, admin_headers, incident, ai_config, local_provider):
    skill = client.post(
        "/api/v1/skills",
        headers=admin_headers,
        json={
            "code": "container_unavailable",
            "title": "容器不可用诊断",
            "content": "先用 get_container_status 核对状态，再读取日志。",
        },
    ).json()
    client.post(
        "/api/v1/knowledge",
        headers=admin_headers,
        json={
            "title": "Docker 容器停止",
            "content": "容器停止不可用，先看状态与日志。人工决定是否重启。",
        },
    )
    assert index_once()
    identifier = enqueue(client, admin_headers, incident).json()["id"]
    assert analyze_once()
    response = client.get(f"/api/v1/analyses/{identifier}", headers=admin_headers).json()
    assert response["status"] == "completed", response
    assert response["knowledge"] and response["result"]["knowledge_ids"]
    assert response["result"]["proposal"] is None
    assert set(response["tool_calls"][-1]["runtime_tools"]) == PUBLIC_TOOLS
    assert len(response["tool_calls"]) == 10
    assert "mcp__ops__propose_action_skill" not in response["tool_calls"][-1]["runtime_tools"]
    loaded = next(e for e in response["evidence"] if e["source"] == "read_skill")
    assert loaded["data"]["id"] == skill["id"]
    assert loaded["data"]["content"] == skill["content"]


def test_real_harness_loads_default_readonly_skill(
    client, admin_headers, incident, ai_config, local_provider
):
    from scripts.seed_knowledge import main

    main()
    main()
    identifier = enqueue(client, admin_headers, incident).json()["id"]
    assert analyze_once()
    response = client.get(f"/api/v1/analyses/{identifier}", headers=admin_headers).json()
    assert response["status"] == "completed", response
    loaded = next(e for e in response["evidence"] if e["source"] == "read_skill")
    assert loaded["data"]["code"] == "sre_diagnosis" and loaded["data"]["version"] == 1
    bundled = Path(__file__).resolve().parents[1] / "app/skills/sre-diagnosis/SKILL.md"
    assert loaded["data"]["content"] == bundled.read_text(encoding="utf-8")
    assert response["result"]["proposal"] is None
    assert "propose_action_skill" not in {c.get("tool") for c in response["tool_calls"]}
    assert loaded["id"] not in response["result"]["evidence_ids"]


def test_real_harness_retries_only_current_model_turn_after_partial_tool_stream(
    client, admin_headers, incident, ai_config, local_provider,
):
    import mock_diagnosis

    from app.models import AgentChannel, AgentConfig, ModelUsage

    attempts = []

    class InterruptedProvider(mock_diagnosis.Handler):
        def do_POST(self):
            attempts.append(self.path)
            if len(attempts) != 2:
                return super().do_POST()
            self.rfile.read(int(self.headers["Content-Length"]))
            # An unfinished tool call must never reach Harness or execute through MCP.
            raw = b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,'
            raw += b'"function":{"name":"mcp__ops__get_recent_logs","arguments":"{"}}]}}]}\n\n'
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), InterruptedProvider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with SessionLocal() as db:
            primary = db.scalar(select(AgentChannel).where(AgentChannel.module == "diagnosis"))
            db.add(AgentChannel(
                module="diagnosis", name="备用", position=1, enabled=True,
                base_url=primary.base_url, model="compatible-backup",
                encrypted_api_key=primary.encrypted_api_key, timeout_seconds=30,
            ))
            primary.base_url = f"http://127.0.0.1:{server.server_port}"
            db.get(AgentConfig, "diagnosis").version += 1
            db.commit()
        identifier = enqueue(client, admin_headers, incident).json()["id"]
        assert analyze_once()
        row = client.get(f"/api/v1/analyses/{identifier}", headers=admin_headers).json()
        assert row["status"] == "completed", row
        assert len(attempts) == 2
        names = [c["tool"] for c in row["tool_calls"] if c.get("tool")]
        assert len(names) == len(set(names))
        logs = [c for c in row["tool_calls"] if c.get("tool") == "get_recent_logs"]
        assert len(logs) == 1
        with SessionLocal() as db:
            records = db.scalars(select(ModelUsage).where(
                ModelUsage.resource_id == UUID(identifier)
            ).order_by(ModelUsage.occurred_at)).all()
            assert records[1].outcome == "failed"
            assert records[1].detail["error_code"] == "stream_incomplete"
            assert records[2].attempt_index == 2 and records[2].model == "compatible-backup"
            assert records[2].request_index == records[1].request_index
            assert all(r.model == "compatible-backup" for r in records[2:])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("401", "authentication_failed"),
        ("402", "insufficient_balance"),
        ("429", "rate_limited"),
        ("503", "service_error"),
        ("malicious", "tool_not_allowed"),
    ],
)
def test_real_harness_rejects_provider_and_shell(
    client, admin_headers, incident, ai_config, local_provider, scenario, expected
):
    local_provider.scenario = scenario
    identifier = enqueue(client, admin_headers, incident).json()["id"]
    assert analyze_once()
    response = client.get(f"/api/v1/analyses/{identifier}", headers=admin_headers).json()
    assert response["status"] == "failed" and response["error_code"] == expected, response
    assert response["tool_calls"][-1]["provider_requests"] == 1


@pytest.mark.parametrize("logs", [False, True])
@pytest.mark.parametrize("large_output", [False, True])
def test_fixed_ssh_reads_are_pinned_bounded_and_redacted(monkeypatch, logs, large_output):
    from unittest.mock import MagicMock

    from app.integrations.ssh import read_container

    client = MagicMock()
    client.__enter__.return_value = client
    channel = MagicMock()
    payload = bytearray(b"credential-value Authorization: Bearer abcdefghijklmnop\n")
    if large_output:
        payload.extend(b"x" * 20000)

    def receive(size):
        data = bytes(payload[:size])
        del payload[:size]
        return data

    channel.recv_ready.side_effect = lambda: bool(payload)
    channel.recv_stderr_ready.return_value = False
    channel.exit_status_ready.return_value = True
    channel.recv_exit_status.return_value = 0
    channel.recv.side_effect = receive
    client.exec_command.return_value = (None, SimpleNamespace(channel=channel), None)
    key = MagicMock()
    key.get_name.return_value = "ssh-ed25519"
    monkeypatch.setattr("app.integrations.ssh.paramiko.SSHClient", lambda: client)
    monkeypatch.setattr("app.integrations.ssh.paramiko.PKey.from_type_string", lambda *_: key)
    monkeypatch.setattr(
        "app.integrations.ssh.cipher",
        lambda: SimpleNamespace(decrypt=lambda _: b'{"secret":"credential-value"}'),
    )
    server = SimpleNamespace(
        ssh_host_key="ssh-ed25519 YQ==", host="pinned-host", ssh_port=2222, ssh_user="reader"
    )
    credential = SimpleNamespace(encrypted_secret="encrypted", auth_type="password")
    result = read_container(server, credential, "/docker/" + "a" * 64, logs=logs)
    expected = (
        "docker logs --since 15m --tail 200 --timestamps "
        if logs
        else "docker inspect --format '{{json .State}}' "
    ) + "a" * 64
    client.exec_command.assert_called_once_with(expected, timeout=10)
    client.get_host_keys().add.assert_called_once_with("[pinned-host]:2222", "ssh-ed25519", key)
    assert client.connect.call_args.kwargs["allow_agent"] is False
    assert client.connect.call_args.kwargs["look_for_keys"] is False
    text = result["logs" if logs else "state"]
    assert "credential-value" not in text and "abcdefghijklmnop" not in text
    assert result["truncated"] is large_output
    assert len(text.encode()) <= 16384
    channel.close.assert_called_once()


@pytest.mark.parametrize("identifier", ["a; reboot", "a" * 64 + " && reboot", "../../x"])
def test_ssh_rejects_untrusted_container_ids(monkeypatch, identifier):
    from app.integrations.ssh import read_container

    def forbidden():
        raise AssertionError("Rejected IDs must not open SSH")

    monkeypatch.setattr("app.integrations.ssh.paramiko.SSHClient", forbidden)
    assert read_container(SimpleNamespace(ssh_host_key="pinned"), object(), identifier) == {
        "error_code": "ssh_unavailable"
    }
