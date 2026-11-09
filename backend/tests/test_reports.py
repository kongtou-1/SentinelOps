import hashlib
import json
import smtplib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from docx import Document
from pypdf import PdfReader
from sqlalchemy import func, select

import app.workers.reports as report_worker
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import (
    AgentAnalysis,
    AlertEvent,
    Application,
    AuditLog,
    Container,
    Incident,
    IncidentAlert,
    IncidentTimeline,
    PostmortemDelivery,
    PostmortemReport,
    PostmortemRevision,
    Server,
    User,
)
from app.services.reports.context import compact_payload, metric_samples, model_context
from app.services.reports.logic import content_valid, snapshot


def content(evidence):
    refs = [r["id"] for r in evidence["records"] if r["kind"] == "incident"]
    return {
        "summary": "容器故障已恢复，完成复盘",
        "impact": "实际业务影响范围待人工核实",
        "causes": [
            {"description": "进程停止，根因待核实", "confidence": "suspected", "evidence_ids": refs}
        ],
        "response_and_recovery": "依据告警和恢复记录核对处置过程",
        "lessons": ["补充应用健康检查"],
        "improvements": [{"action": "核查应用日志", "owner": "管理员", "deadline": "待确定"}],
        "data_gaps": [],
        "evidence_ids": refs,
    }


@pytest.fixture
def configured(monkeypatch, tmp_path):
    settings = get_settings()
    key = tmp_path / "ai.key"
    key.write_text("dummy-report-key-" + "x" * 32)
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "ai_key_file", key)
    from app.services.agent_config import import_environment

    with SessionLocal() as db:
        import_environment(db)
    return settings


@pytest.fixture
def closed_incident(client, admin_headers):
    with SessionLocal() as db:
        now = datetime.now(UTC)
        row = Incident(
            title="中文事故",
            severity="critical",
            status="closed",
            started_at=now - timedelta(minutes=3),
            last_alert_started_at=now - timedelta(minutes=2),
            recovered_at=now - timedelta(minutes=1),
            closed_at=now,
        )
        db.add(row)
        db.flush()
        event = AlertEvent(
            fingerprint=uuid4().hex,
            severity="critical",
            status="resolved",
            starts_at=row.started_at,
            ends_at=row.recovered_at,
            labels={"alertname": "ContainerDown"},
            annotations={"summary": row.title},
        )
        db.add(event)
        db.flush()
        db.add(IncidentAlert(incident_id=row.id, alert_event_id=event.id))
        db.add(
            IncidentTimeline(
                incident_id=row.id,
                event_type="comment_added",
                occurred_at=now,
                actor_type="system",
                content={"comment": "password=hidden-value sk-secretvalue123"},
            )
        )
        db.commit()
        return str(row.id)


@pytest.fixture
def fake_harness(monkeypatch):
    boxes = []
    original = report_worker.ReportTools.__init__

    def initialize(self, evidence, identifier=None):
        original(self, evidence, identifier)
        boxes.append(self)

    def run(self, *args, **kwargs):
        assert kwargs["max_tokens"] == 16384
        box = boxes[-1]
        box.call("get_postmortem_context", {})
        return content(box.snapshot), None

    monkeypatch.setattr(report_worker.ReportTools, "__init__", initialize)
    monkeypatch.setattr(report_worker.HarnessAdapter, "run", run)
    return boxes


def create(client, headers, incident_id, force=False):
    response = client.post(
        f"/api/v1/incidents/{incident_id}/reports", headers=headers, json={"force": force}
    )
    assert response.status_code in (200, 202), response.text
    return response.json()


@pytest.fixture
def draft(client, admin_headers, closed_incident, configured, fake_harness):
    row = create(client, admin_headers, closed_incident)
    assert report_worker.generate_once()
    return client.get(f"/api/v1/reports/{row['id']}", headers=admin_headers).json()


def test_legacy_approved_snapshot_keeps_time_section_and_new_snapshot_marks_unverified(draft):
    from copy import deepcopy

    from app.services.reports.exports import sections

    current = SimpleNamespace(**draft)
    assert "未取得恢复核验" in dict(sections(current, draft["content"]))["事故时间"][0]
    legacy = deepcopy(draft)
    for record in legacy["snapshot"]["records"]:
        if record["kind"] == "alert":
            for field in (
                "rule_snapshot",
                "recovery_status",
                "recovery_reason",
                "recovery_checked_at",
                "recovery_evidence_at",
            ):
                record["data"].pop(field)
    original = dict(sections(SimpleNamespace(**legacy), legacy["content"]))["事故时间"][0]
    assert "\n恢复时间：" in original and "核验恢复时间" not in original
    assert original.endswith("开始至恢复表示告警持续区间，不等同于业务停机时间。")


def test_report_outline_has_fixed_sections(draft):
    from app.services.reports.exports import sections

    names = [name for name, _ in sections(SimpleNamespace(**draft), draft["content"])]
    assert names[:8] == [
        "事故摘要",
        "影响范围",
        "关键时间线",
        "原因与证据",
        "处置与恢复核验",
        "经验教训",
        "改进措施",
        "证据缺口",
    ]


def test_nested_frozen_diagnostic_references_are_valid_but_invented_ids_are_rejected():
    frozen = {
        "records": [
            {
                "id": "analysis:known",
                "kind": "analysis",
                "data": {
                    "evidence": [{"id": "e_known", "data": {"value": 98}}],
                },
            }
        ],
        "data_gaps": [],
    }
    value = content(frozen)
    value["evidence_ids"] = ["e_known"]
    value["causes"][0]["evidence_ids"] = ["e_known"]
    assert content_valid(SimpleNamespace(snapshot=frozen), value)["evidence_ids"] == ["e_known"]
    value["evidence_ids"] = ["e_invented"]
    with pytest.raises(ValueError, match="outside the frozen snapshot"):
        content_valid(SimpleNamespace(snapshot=frozen), value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("ai_base_url", "http://changed-provider"),
        ("ai_model", "changed-model"),
        ("ai_timeout_seconds", 17),
    ],
)
def test_queued_report_configuration_change_stops_before_provider(
    client, admin_headers, closed_incident, configured, monkeypatch, field, value
):
    row = create(client, admin_headers, closed_incident)
    if field == "ai_timeout_seconds":
        monkeypatch.setattr(configured, field, value)
    else:
        from app.models import AgentChannel, AgentConfig

        with SessionLocal() as db:
            channel = db.scalar(select(AgentChannel).where(AgentChannel.module == "report"))
            setattr(channel, "base_url" if field == "ai_base_url" else "model", value)
            db.get(AgentConfig, "report").version += 1
            db.commit()
    monkeypatch.setattr(
        report_worker.HarnessAdapter,
        "run",
        lambda *_a, **_k: pytest.fail("Provider must not be called"),
    )
    assert report_worker.generate_once()
    result = client.get("/api/v1/reports/" + row["id"], headers=admin_headers).json()
    assert result["status"] == "failed" and result["error_code"] == "configuration_changed"
    assert result["tool_calls"] == []


@pytest.fixture
def approved(client, admin_headers, draft):
    response = client.post(
        f"/api/v1/reports/{draft['id']}/review",
        headers=admin_headers,
        json={"expected_revision": 1, "decision": "approve", "comment": "已核对来源"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def send_body(revision=1):
    return {
        "expected_revision": revision,
        "recipients": ["reviewer@example.test"],
        "comment": "明确发送本地测试报告",
        "idempotency_key": str(uuid4()),
    }


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
def test_roles(client, make_user, login, configured, role):
    make_user(role, role)
    headers = {"Authorization": "Bearer " + login(role)["access_token"]}
    missing = str(uuid4())
    assert client.get(f"/api/v1/reports/{missing}", headers=headers).status_code == 404
    assert client.get(f"/api/v1/incidents/{missing}/reports", headers=headers).status_code == 404
    assert client.get(f"/api/v1/reports/{missing}/revisions", headers=headers).status_code == 404
    assert (
        client.get(f"/api/v1/reports/{missing}/export?format=pdf", headers=headers).status_code
        == 404
    )
    assert client.post(
        f"/api/v1/incidents/{missing}/reports", headers=headers, json={}
    ).status_code == (403 if role == "viewer" else 404)
    for suffix, payload in (
        ("review", {"expected_revision": 1, "decision": "approve", "comment": "ok"}),
        ("send", send_body()),
    ):
        assert client.post(
            f"/api/v1/reports/{missing}/{suffix}", headers=headers, json=payload
        ).status_code == (404 if role == "admin" else 403)
    assert client.get(f"/api/v1/reports/{missing}/deliveries", headers=headers).status_code == (
        404 if role == "admin" else 403
    )
    assert client.put(
        f"/api/v1/reports/{missing}",
        headers=headers,
        json={"expected_revision": 1, "content": content({"records": []})},
    ).status_code == (404 if role == "admin" else 403)


@pytest.mark.parametrize(
    "suffix", ["", "/revisions", "/deliveries", "/export?format=pdf", "/export?format=json"]
)
def test_anonymous(client, suffix):
    assert client.get(f"/api/v1/reports/{uuid4()}{suffix}").status_code == 401


@pytest.mark.parametrize("status", ["open", "in_progress", "recovered", "merged"])
def test_not_closed(client, admin_headers, closed_incident, configured, status):
    with SessionLocal() as db:
        db.get(Incident, UUID(closed_incident)).status = status
        db.commit()
    assert (
        client.post(
            f"/api/v1/incidents/{closed_incident}/reports", headers=admin_headers, json={}
        ).status_code
        == 409
    )


def test_active_analysis(client, admin_headers, closed_incident, configured):
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.username == "admin"))
        db.add(
            AgentAnalysis(
                incident_id=UUID(closed_incident),
                requested_by=admin.id,
                input_hash="x" * 64,
                config={},
                status="queued",
            )
        )
        db.commit()
    assert (
        client.post(
            f"/api/v1/incidents/{closed_incident}/reports", headers=admin_headers, json={}
        ).status_code
        == 409
    )


def test_disabled_and_missing_key(client, admin_headers, closed_incident, configured, monkeypatch):
    monkeypatch.setattr(configured, "ai_enabled", False)
    path = f"/api/v1/incidents/{closed_incident}/reports"
    assert client.post(path, headers=admin_headers, json={}).status_code == 503
    monkeypatch.setattr(configured, "ai_enabled", True)
    configured.ai_key_file.unlink()
    from app.models import AgentChannel, AgentConfig

    with SessionLocal() as db:
        for channel in db.scalars(select(AgentChannel).where(AgentChannel.module == "report")):
            channel.enabled = False
        db.get(AgentConfig, "report").version += 1
        db.commit()
    assert client.post(path, headers=admin_headers, json={}).status_code == 503


def test_generate_duplicate_snapshot_immutable_and_force(
    client, admin_headers, draft, closed_incident
):
    assert draft["status"] == "draft" and draft["revision"] == 1
    assert "hidden-value" not in json.dumps(draft) and "sk-secretvalue123" not in json.dumps(draft)
    assert draft["content"]["data_gaps"] == draft["snapshot"]["data_gaps"]
    assert create(client, admin_headers, closed_incident)["id"] == draft["id"]
    new = create(client, admin_headers, closed_incident, force=True)
    assert new["id"] != draft["id"]
    assert create(client, admin_headers, closed_incident, force=True)["id"] == new["id"]
    assert (
        client.get(f"/api/v1/reports/{draft['id']}", headers=admin_headers).json()["snapshot"]
        == draft["snapshot"]
    )
    with SessionLocal() as db:
        assert db.get(Incident, UUID(closed_incident)).status == "closed"


def test_concurrent_generation(client, admin_headers, closed_incident, configured):
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda _: create(client, admin_headers, closed_incident), range(2)))
    assert rows[0]["id"] == rows[1]["id"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(PostmortemReport)) == 1


def test_recursive_merge_snapshot(client, admin_headers, closed_incident):
    with SessionLocal() as db:
        root = db.get(Incident, UUID(closed_incident))
        child = Incident(
            title="合并来源",
            severity="warning",
            status="merged",
            started_at=root.started_at,
            last_alert_started_at=root.started_at,
            merged_into_id=root.id,
        )
        db.add(child)
        db.flush()
        grandchild = Incident(
            title="二级来源",
            severity="warning",
            status="merged",
            started_at=root.started_at,
            last_alert_started_at=root.started_at,
            merged_into_id=child.id,
        )
        db.add(grandchild)
        db.flush()
        db.add(
            IncidentTimeline(
                incident_id=grandchild.id,
                event_type="comment_added",
                occurred_at=datetime.now(UTC),
                actor_type="system",
                content={"comment": "源处理过程"},
            )
        )
        db.flush()
        evidence = snapshot(db, root)
        assert {r["data"]["title"] for r in evidence["records"] if r["kind"] == "incident"} == {
            "中文事故",
            "合并来源",
            "二级来源",
        }
        assert any(
            r["data"].get("content", {}).get("comment") == "源处理过程" for r in evidence["records"]
        )


def test_oversized_context_fails_without_truncation(
    client, admin_headers, closed_incident, configured, monkeypatch
):
    monkeypatch.setattr("app.services.reports.logic.CONTEXT_LIMIT", 1)
    row = create(client, admin_headers, closed_incident)
    assert row["status"] == "failed" and row["error_code"] == "report_context_too_large"
    assert len(row["snapshot"]["records"]) == 3


def test_large_report_summarizes_input_without_changing_frozen_evidence(
    client, admin_headers, closed_incident, configured, fake_harness
):
    with SessionLocal() as db:
        incident = db.get(Incident, UUID(closed_incident))
        incident.closure = {"forced": True, "recovery_verified": False, "reason": "人工终止"}
        admin = db.scalar(select(User).where(User.username == "admin"))
        samples = [[i, str(0.123456789012345 + i)] for i in range(4000)]
        db.add(
            AgentAnalysis(
                incident_id=incident.id,
                requested_by=admin.id,
                input_hash="x" * 64,
                config={},
                status="completed",
                result={"summary": "磁盘占用异常"},
                evidence=[
                    {
                        "source": "get_server_metrics",
                        "data": {
                            "metric": "host_disk_percent",
                            "series": {"values": samples},
                        },
                    }
                ]
                * 4,
            )
        )
        for i in range(540):
            db.add(
                IncidentTimeline(
                    incident_id=incident.id,
                    event_type="alert_created" if i % 2 else "alert_linked",
                    occurred_at=incident.closed_at,
                    actor_type="system",
                    content={"alert_event_id": str(uuid4()), "alertname": "HistoricalWorkerDown"},
                )
            )
        db.commit()
    row = create(client, admin_headers, closed_incident)
    frozen = row["snapshot"]
    assert row["status"] == "queued"
    stats = frozen["context_summary"]
    assert stats["snapshot_bytes"] > 262144 and stats["input_bytes"] < 262144
    assert len(frozen["records"]) == 544
    assert (
        len(
            next(r for r in frozen["records"] if r["kind"] == "analysis")["data"]["evidence"][0][
                "data"
            ]["series"]["values"]
        )
        == 4000
    )
    assert report_worker.generate_once()
    supplied = fake_harness[-1].snapshot
    groups = [r for r in supplied["records"] if "aggregation" in r]
    assert sum(r["aggregation"]["event_count"] for r in groups) == 540
    assert all(r["aggregation"]["first_source_id"] == r["id"] for r in groups)
    assert next(r for r in supplied["records"] if r["kind"] == "incident")["data"]["closure"] == {
        "forced": True,
        "recovery_verified": False,
        "reason": "人工终止",
    }
    result = client.get("/api/v1/reports/" + row["id"], headers=admin_headers).json()
    assert result["status"] == "draft" and result["revision"] == 1
    assert result["snapshot"] == frozen
    assert any("强制关闭" in gap for gap in result["content"]["data_gaps"])
    assert any("没有平台执行记录" in gap for gap in result["content"]["data_gaps"])


def test_context_preserves_human_events_and_aggregates_only_matching_machine_facts():
    from copy import deepcopy

    records = []
    for index, (event_type, content) in enumerate(
        [
            ("alert_created", {"alert_event_id": "a", "severity": "critical"}),
            ("alert_created", {"alert_event_id": "b", "severity": "critical"}),
            ("alert_created", {"alert_event_id": "c", "severity": "warning"}),
            ("comment_added", {"comment": "人工决定" * 2000}),
            ("incident_force_closed", {"forced": True, "recovery_verified": False}),
        ]
    ):
        records.append(
            {
                "id": f"timeline:{index}",
                "kind": "timeline",
                "occurred_at": f"2026-10-14T00:00:0{index}Z",
                "content_hash": "f" * 64,
                "data": {"event_type": event_type, "content": content},
            }
        )
    frozen = {"records": records, "data_gaps": ["未核验恢复"]}
    before = deepcopy(frozen)
    context = model_context(frozen)
    assert frozen == before and context == model_context(frozen)
    assert len(context["records"]) == 4
    assert context["data_gaps"] == frozen["data_gaps"]
    assert context["records"][-2:] == records[-2:]
    assert context["records"][0]["aggregation"]["event_count"] == 2


def test_metric_summary_keeps_extrema_invalid_samples_and_time_windows():
    values = [[i, str(i)] for i in range(100)] + [[100, "NaN"], [101, "+Inf"]]
    values[51][1] = "-9000"
    summary = metric_samples(values)
    assert summary["min"] == [51, "-9000"]
    assert summary["max"] == [99, "99"]
    assert summary["first"] == [0, "0"] and summary["last"] == [99, "99"]
    assert summary["invalid_sample_count"] == 2 and summary["sample_count"] == 102
    assert sum(w["count"] for w in summary["windows"]) == 100
    empty = metric_samples([[0, "NaN"]])
    assert empty["invalid_sample_count"] == 1 and "min" not in empty


def test_identical_metric_series_share_samples_but_keep_every_label():
    samples = [[i, str(i)] for i in range(100)]
    original = {
        "result": [
            {"metric": {"mountpoint": "/a"}, "values": samples},
            {"metric": {"mountpoint": "/b"}, "values": samples},
            {"metric": {"mountpoint": "/c"}, "values": [[0, "NaN"]]},
        ]
    }
    compact = compact_payload(original)
    assert len(compact["result"]) == 2
    assert compact["result"][0]["metric_labels"] == [{"mountpoint": "/a"}, {"mountpoint": "/b"}]
    assert len(original["result"]) == 3 and original["result"][0]["values"] == samples
    assert compact["result"][1]["values"] == [[0, "NaN"]]


def test_edit_review_and_immutable_approval(client, admin_headers, draft):
    path = f"/api/v1/reports/{draft['id']}"
    changed = {**draft["content"], "summary": "人工核查补充总结"}
    payload = {"expected_revision": 1, "content": changed}
    assert client.put(path, headers=admin_headers, json=payload).json()["revision"] == 2
    assert client.put(path, headers=admin_headers, json=payload).status_code == 409
    bad = {**changed, "evidence_ids": ["invented"]}
    assert (
        client.put(
            path, headers=admin_headers, json={"expected_revision": 2, "content": bad}
        ).status_code
        == 422
    )
    assert client.get(path + "/export?format=pdf", headers=admin_headers).status_code == 409
    assert client.get(path + "/export?format=json", headers=admin_headers).status_code == 409
    assert client.post(path + "/send", headers=admin_headers, json=send_body(2)).status_code == 409
    assert (
        client.post(
            path + "/review",
            headers=admin_headers,
            json={"expected_revision": 1, "decision": "approve", "comment": "stale"},
        ).status_code
        == 409
    )
    returned = client.post(
        path + "/review",
        headers=admin_headers,
        json={"expected_revision": 2, "decision": "request_changes", "comment": "核对影响范围"},
    )
    assert returned.json()["status"] == "draft"
    assert (
        client.post(
            path + "/review",
            headers=admin_headers,
            json={"expected_revision": 2, "decision": "approve", "comment": "通过"},
        ).json()["status"]
        == "approved"
    )
    assert (
        client.put(
            path, headers=admin_headers, json={"expected_revision": 2, "content": changed}
        ).status_code
        == 409
    )
    revisions = client.get(path + "/revisions", headers=admin_headers).json()
    assert len(revisions) == 2 and revisions[0]["content"] == draft["content"]
    logs = client.get(f"/api/v1/audit-logs?report_id={draft['id']}", headers=admin_headers).json()
    assert {r["action"] for r in logs} >= {
        "report_queued",
        "report_generated",
        "report_edited",
        "report_changes_requested",
        "report_approved",
    }


@pytest.mark.parametrize("mode", ["provider", "references", "no_tool", "requester"])
def test_generation_failures(
    client, admin_headers, closed_incident, configured, fake_harness, monkeypatch, mode
):
    row = create(client, admin_headers, closed_incident)
    if mode == "provider":
        monkeypatch.setattr(
            report_worker.HarnessAdapter, "run", lambda *a, **k: (None, "authentication_failed")
        )
    elif mode == "references":

        def bad(*a, **k):
            box = fake_harness[-1]
            box.call("get_postmortem_context", {})
            return {**content(box.snapshot), "evidence_ids": ["invented"]}, None

        monkeypatch.setattr(report_worker.HarnessAdapter, "run", bad)
    elif mode == "no_tool":
        monkeypatch.setattr(report_worker.HarnessAdapter, "run", lambda *a, **k: ({}, None))
    else:
        with SessionLocal() as db:
            db.scalar(select(User).where(User.username == "admin")).is_active = False
            db.commit()
    assert report_worker.generate_once()
    with SessionLocal() as db:
        result = db.get(PostmortemReport, UUID(row["id"]))
        assert result.status == "failed"
        assert (
            result.error_code
            == {
                "provider": "authentication_failed",
                "references": "invalid_response",
                "no_tool": "context_not_read",
                "requester": "requester_unavailable",
            }[mode]
        )
        assert db.scalar(select(func.count()).select_from(PostmortemRevision)) == 0


def test_report_tool_boundary():
    tool = report_worker.ReportTools({"records": []})
    assert tool.call("get_postmortem_context", {})["data"]["records"] == []
    repeated = tool.call("get_postmortem_context", {})["data"]
    assert repeated["already_read"] is True and "records" not in repeated
    with pytest.raises(ValueError):
        tool.call("bash", {})
    with pytest.raises(ValueError):
        tool.call("get_postmortem_context", {"incident_id": str(uuid4())})


def test_exports(client, admin_headers, approved):
    path = f"/api/v1/reports/{approved['id']}/export"
    pdf = client.get(path + "?format=pdf", headers=admin_headers)
    assert pdf.status_code == 200, pdf.text if pdf.status_code != 200 else ""
    text = "".join(page.extract_text() for page in PdfReader(BytesIO(pdf.content)).pages)
    assert "事故摘要" in text and "人工" in text and approved["snapshot_hash"] in text
    assert b"/FontFile2" in pdf.content
    word = client.get(path + "?format=docx", headers=admin_headers)
    doc = Document(BytesIO(word.content))
    assert any(p.text == "事故摘要" for p in doc.paragraphs)
    assert len(doc.tables) == 1
    assert not doc.styles["Title"].element.xpath("./w:pPr/w:pBdr")
    evidence = client.get(path + "?format=json", headers=admin_headers)
    assert evidence.status_code == 200
    assert evidence.headers["content-type"] == "application/json"
    assert evidence.headers["content-disposition"].endswith('-evidence.json"')
    data = evidence.json()
    assert data["snapshot"] == approved["snapshot"]
    assert data["snapshot_hash"] == approved["snapshot_hash"]
    assert data["content"] == approved["content"]
    from app.utils.canonical_json import digest

    assert digest(data["snapshot"]) == data["snapshot_hash"]
    assert digest(data["content"]) == data["content_hash"]
    assert client.get(path + "?format=exe", headers=admin_headers).status_code == 422


def test_large_export_keeps_raw_evidence_in_json_only(approved):
    from copy import deepcopy

    from app.services.reports.exports import export_report
    from app.utils.canonical_json import digest

    value = deepcopy(approved)
    evidence = {
        "id": "e_large_metric",
        "source": "get_server_metrics",
        "observed_at": value["snapshot"]["captured_at"],
        "data": {"metric": "disk_usage", "series": [{"values": [[i, 98] for i in range(10000)]}]},
    }
    records = value["snapshot"]["records"]
    records.append(
        {
            "id": "analysis:large",
            "kind": "analysis",
            "occurred_at": evidence["observed_at"],
            "content_hash": digest(evidence),
            "data": {"evidence": [evidence], "raw_logs": "RAW_LOG_SENTINEL\n" * 20000},
        }
    )
    for i in range(546):
        records.append(
            {
                "id": f"timeline:automatic-{i}",
                "kind": "timeline",
                "occurred_at": evidence["observed_at"],
                "content_hash": digest(i),
                "data": {"event_type": "alert_linked", "actor_type": "system", "content": {}},
            }
        )
    for i in range(20):
        identifier = f"alert:cited-{i}"
        data = {"status": "firing", "severity": "warning", "recovery_status": "pending"}
        records.append(
            {
                "id": identifier,
                "kind": "alert",
                "occurred_at": evidence["observed_at"],
                "content_hash": digest(data),
                "data": data,
            }
        )
        value["content"]["evidence_ids"].append(identifier)
    value["content"]["causes"][0]["evidence_ids"] = [evidence["id"]]
    value["snapshot_hash"] = digest(value["snapshot"])
    row = SimpleNamespace(**value)
    row.reviewed_at = datetime.fromisoformat(value["reviewed_at"])
    original = deepcopy(row.snapshot)
    pdf = PdfReader(BytesIO(export_report(row, value["content"], "pdf")))
    assert all(page.extract_text().count("证据编号") <= 1 for page in pdf.pages)
    text = "".join(page.extract_text() for page in pdf.pages)
    word = Document(BytesIO(export_report(row, value["content"], "docx")))
    word_text = "\n".join(p.text for p in word.paragraphs) + "\n".join(
        c.text for t in word.tables for r in t.rows for c in r.cells
    )
    for result in (text, word_text):
        assert "证据摘要与来源" in result and "disk_usage" in result
        assert "e_large_metric" in result and "analysis:large" in result
        assert "RAW_LOG_SENTINEL" not in result and "timeline:automatic-" not in result
    assert len(pdf.pages) <= 5
    raw = json.loads(export_report(row, value["content"], "json"))
    assert raw["snapshot"] == original == row.snapshot
    assert raw["snapshot_hash"] == digest(original)
    assert len(evidence["data"]["series"][0]["values"]) == 10000


def test_approved_pdf_reuses_frozen_attachment_across_renderer_changes(
    client, admin_headers, approved, monkeypatch
):
    path = "/api/v1/reports/" + approved["id"]
    response = client.post(path + "/send", headers=admin_headers, json=send_body())
    assert response.status_code == 202, response.text
    with SessionLocal() as db:
        original = db.get(PostmortemDelivery, UUID(response.json()["id"])).attachment
    monkeypatch.setattr(
        "app.services.reports.exports.export_report",
        lambda *_: pytest.fail("Approved PDF already frozen"),
    )
    download = client.get(path + "/export?format=pdf", headers=admin_headers)
    assert download.status_code == 200 and download.content == original
    other = client.post(path + "/send", headers=admin_headers, json=send_body())
    assert other.status_code == 202
    with SessionLocal() as db:
        assert db.get(PostmortemDelivery, UUID(other.json()["id"])).attachment == original


@pytest.fixture
def fake_smtp(monkeypatch):
    result = SimpleNamespace(data=None, reject=False, disconnect=False, messages=0)

    class SMTP:
        def __init__(self, *a, **k):
            pass

        def ehlo(self):
            pass

        def mail(self, sender):
            return 250, b"ok"

        def rcpt(self, recipient):
            return (550 if result.reject else 250), b"ok"

        def data(self, message):
            result.messages += 1
            result.data = message
            if result.disconnect:
                raise smtplib.SMTPServerDisconnected()
            return 250, b"ok"

        def close(self):
            pass

    monkeypatch.setattr(report_worker.smtplib, "SMTP", SMTP)
    return result


def test_mail_idempotence_and_frozen_attachment(
    client, admin_headers, approved, fake_smtp, monkeypatch, configured
):
    path = f"/api/v1/reports/{approved['id']}"
    body = send_body()
    first = client.post(path + "/send", headers=admin_headers, json=body)
    assert first.status_code == 202, first.text
    identifier = first.json()["id"]
    assert client.post(path + "/send", headers=admin_headers, json=body).json()["id"] == identifier
    assert (
        client.post(
            path + "/send", headers=admin_headers, json={**body, "comment": "other"}
        ).status_code
        == 409
    )
    with SessionLocal() as db:
        row = db.get(PostmortemDelivery, UUID(identifier))
        assert hashlib.sha256(row.attachment).hexdigest() == row.attachment_hash
        assert b"PDF" in row.attachment[:10]
        exported = client.get(path + "/export?format=pdf", headers=admin_headers)
        assert exported.content == row.attachment
    monkeypatch.setattr(configured, "ai_enabled", False)
    assert report_worker.deliver_once()
    assert not report_worker.deliver_once()
    assert fake_smtp.messages == 1
    assert client.get(path + "/deliveries", headers=admin_headers).json()[0]["status"] == "sent"
    assert client.post(path + "/send", headers=admin_headers, json=body).json()["status"] == "sent"


@pytest.mark.parametrize("mode", ["reject", "disconnect", "permission"])
def test_delivery_failures(client, admin_headers, approved, fake_smtp, mode):
    path = f"/api/v1/reports/{approved['id']}"
    response = client.post(path + "/send", headers=admin_headers, json=send_body())
    assert response.status_code == 202, response.text
    if mode == "permission":
        with SessionLocal() as db:
            db.scalar(select(User).where(User.username == "admin")).is_active = False
            db.commit()
    else:
        setattr(fake_smtp, mode, True)
    assert report_worker.deliver_once()
    with SessionLocal() as db:
        row = db.get(PostmortemDelivery, UUID(response.json()["id"]))
        assert row.status == ("unknown" if mode == "disconnect" else "failed")
        assert (row.dispatched_at is not None) == (mode == "disconnect")
    assert not report_worker.deliver_once()


def test_invalid_mail_inputs(client, admin_headers, approved):
    path = f"/api/v1/reports/{approved['id']}/send"
    for recipients in ([], ["not-email"], ["x@example.test\r\nBcc: other@example.test"]):
        assert (
            client.post(
                path, headers=admin_headers, json={**send_body(), "recipients": recipients}
            ).status_code
            == 422
        )


def test_interrupt_and_queue_retention(client, admin_headers, approved):
    path = f"/api/v1/reports/{approved['id']}/send"
    ids = [
        UUID(client.post(path, headers=admin_headers, json=send_body()).json()["id"])
        for _ in range(3)
    ]
    with SessionLocal() as db:
        db.get(PostmortemDelivery, ids[0]).status = "sending"
        sent = db.get(PostmortemDelivery, ids[1])
        sent.status, sent.dispatched_at = "sending", datetime.now(UTC)
        row = db.get(PostmortemReport, UUID(approved["id"]))
        row.status = "running"
        db.commit()
        report_worker.interrupt_tasks(db)
        assert db.get(PostmortemReport, row.id).status == "interrupted"
        assert [db.get(PostmortemDelivery, i).status for i in ids] == [
            "interrupted",
            "unknown",
            "queued",
        ]


def test_generation_permission_revoked_before_publish(
    client, admin_headers, closed_incident, configured, fake_harness, monkeypatch
):
    row = create(client, admin_headers, closed_incident)

    def revoked(*args, **kwargs):
        box = fake_harness[-1]
        box.call("get_postmortem_context", {})
        with SessionLocal() as db:
            db.scalar(select(User).where(User.username == "admin")).is_active = False
            db.commit()
        return content(box.snapshot), None

    monkeypatch.setattr(report_worker.HarnessAdapter, "run", revoked)
    assert report_worker.generate_once()
    with SessionLocal() as db:
        result = db.get(PostmortemReport, UUID(row["id"]))
        assert result.status == "failed" and result.error_code == "requester_unavailable"
        assert db.scalar(select(func.count()).select_from(PostmortemRevision)) == 0


def test_concurrent_draft_edits(client, admin_headers, draft):
    payload = {"expected_revision": 1, "content": draft["content"]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: (
                    client.put(
                        f"/api/v1/reports/{draft['id']}", headers=admin_headers, json=payload
                    ).status_code
                ),
                range(2),
            )
        )
    assert sorted(results) == [200, 409]


def test_audit_snapshot_projects_only_public_metadata(client, admin_headers, closed_incident):
    with SessionLocal() as db:
        db.add(
            AuditLog(
                incident_id=UUID(closed_incident),
                action="historical_action",
                request_id=str(uuid4()),
                ip="restricted-source-ip",
                detail={"private": "restricted-detail"},
            )
        )
        db.flush()
        frozen = snapshot(db, db.get(Incident, UUID(closed_incident)))
        assert "restricted-detail" not in json.dumps(frozen)
        assert "restricted-source-ip" not in json.dumps(frozen)
        assert any(r["kind"] == "audit" for r in frozen["records"])


def test_snapshot_and_revision_survive_source_change(client, admin_headers, draft):
    with SessionLocal() as db:
        db.get(Incident, UUID(draft["incident_id"])).title = "changed directly for test"
        db.commit()
    current = client.get(f"/api/v1/reports/{draft['id']}", headers=admin_headers).json()
    assert current["snapshot"] == draft["snapshot"]
    assert current["content"] == draft["content"]


def test_concurrent_mail_enqueue(client, admin_headers, approved):
    path = f"/api/v1/reports/{approved['id']}/send"
    body = send_body()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: client.post(path, headers=admin_headers, json=body), range(2))
        )
    assert all(r.status_code == 202 for r in results)
    assert results[0].json()["id"] == results[1].json()["id"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(PostmortemDelivery)) == 1


@pytest.mark.parametrize("role", ["viewer", "operator"])
def test_only_admin_can_delete_reports(client, admin_headers, draft, make_user, login, role):
    make_user(role, role)
    headers = {"Authorization": "Bearer " + login(role)["access_token"]}
    assert client.delete("/api/v1/reports/" + draft["id"], headers=headers).status_code == 403
    assert client.get("/api/v1/reports/" + draft["id"], headers=admin_headers).status_code == 200


def test_delete_report_preserves_evidence_and_hides_all_read_routes(
    client, admin_headers, approved
):
    path = "/api/v1/reports/" + approved["id"]
    assert client.delete(path, headers=admin_headers).status_code == 204
    for suffix in ("", "/revisions", "/deliveries", "/export?format=pdf"):
        assert client.get(path + suffix, headers=admin_headers).status_code == 404
    assert client.delete(path, headers=admin_headers).status_code == 404
    for route in ("/api/v1/reports", f"/api/v1/incidents/{approved['incident_id']}/reports"):
        response = client.get(route, headers=admin_headers)
        assert response.json() == [] and response.headers["x-total-count"] == "0"
    with SessionLocal() as db:
        row = db.get(PostmortemReport, UUID(approved["id"]))
        assert row.deleted_at and row.updated_at >= row.deleted_at
        assert row.snapshot == approved["snapshot"]
        assert db.scalar(select(func.count()).select_from(PostmortemRevision)) == 1
        assert db.scalar(select(AuditLog.id).where(AuditLog.action == "report_deleted"))
        assert db.scalar(
            select(IncidentTimeline.id).where(IncidentTimeline.event_type == "report_deleted")
        )


@pytest.mark.parametrize("status", ["queued", "running"])
def test_cannot_delete_generating_report(client, admin_headers, draft, status):
    with SessionLocal() as db:
        db.get(PostmortemReport, UUID(draft["id"])).status = status
        db.commit()
    assert client.delete("/api/v1/reports/" + draft["id"], headers=admin_headers).status_code == 409


@pytest.mark.parametrize("status", ["queued", "sending"])
def test_cannot_delete_report_during_delivery(client, admin_headers, approved, status):
    path = "/api/v1/reports/" + approved["id"]
    delivery = client.post(path + "/send", headers=admin_headers, json=send_body()).json()
    with SessionLocal() as db:
        db.get(PostmortemDelivery, UUID(delivery["id"])).status = status
        db.commit()
    assert client.delete(path, headers=admin_headers).status_code == 409


def test_report_service_context_is_frozen_and_legacy_context_does_not_rewrite_snapshot(
    client,
    admin_headers,
    closed_incident,
    configured,
    fake_harness,
):
    with SessionLocal() as db:
        service = Application(code="report-service", name="问答服务")
        server = Server(
            name="report-host",
            host="localhost",
            node_exporter_target="host:9100",
            cadvisor_target="host:8080",
        )
        db.add_all([service, server])
        db.flush()
        container = Container(
            server_id=server.id,
            application_id=service.id,
            docker_id="a" * 64,
            name="qa-api",
            image="qa:1",
            status="running",
            labels={},
            last_seen_at=datetime.now(UTC),
        )
        db.add(container)
        db.flush()
        event = db.scalar(select(AlertEvent))
        event.server_id = server.id
        event.labels = {"id": "/docker/" + container.docker_id}
        db.commit()
        service_id = service.id
    report = create(client, admin_headers, closed_incident)
    context = report["resource_context"]
    assert context["services"] == [{"id": str(service_id), "name": "问答服务"}]
    assert {r["name"] for r in context["resources"]} == {"report-host", "qa-api"}
    assert context["source"] == "frozen"
    with SessionLocal() as db:
        db.get(Application, service_id).name = "重命名服务"
        db.commit()
    path = "/api/v1/reports/" + report["id"]
    assert client.get(path, headers=admin_headers).json()["resource_context"] == context
    assert (
        client.get("/api/v1/reports", headers=admin_headers).json()[0]["resource_context"]
        == context
    )
    with SessionLocal() as db:
        row = db.get(PostmortemReport, UUID(report["id"]))
        row.snapshot = {k: v for k, v in row.snapshot.items() if k != "resource_context"}
        from app.utils.canonical_json import digest

        row.snapshot_hash = digest(row.snapshot)
        original = row.snapshot
        db.commit()
    legacy = client.get(path, headers=admin_headers).json()
    assert legacy["resource_context"]["services"][0]["name"] == "重命名服务"
    assert legacy["resource_context"]["source"] == "current_assets"
    assert legacy["snapshot"] == original


def test_host_only_report_does_not_attribute_all_host_services(
    client,
    admin_headers,
    closed_incident,
    configured,
):
    with SessionLocal() as db:
        service = Application(code="host-service", name="同机服务")
        server = Server(
            name="host-only",
            host="localhost",
            node_exporter_target="host:9100",
            cadvisor_target="host:8080",
        )
        db.add_all([service, server])
        db.flush()
        db.add(
            Container(
                server_id=server.id,
                application_id=service.id,
                docker_id="b" * 64,
                name="unrelated",
                image="qa:1",
                status="running",
                labels={},
                last_seen_at=datetime.now(UTC),
            )
        )
        db.scalar(select(AlertEvent)).server_id = server.id
        db.commit()
    report = create(client, admin_headers, closed_incident)
    assert report["resource_context"]["services"] == []
    assert [r["name"] for r in report["resource_context"]["resources"]] == ["host-only"]


def test_report_context_includes_multiple_services_from_actions_and_source_bindings(
    client,
    admin_headers,
):
    from app.services.reports.resources import resource_context

    with SessionLocal() as db:
        first = Application(code="first", name="问答服务")
        second = Application(code="second", name="推理服务")
        server = Server(
            name="actions-host",
            host="localhost",
            node_exporter_target="host:9100",
            cadvisor_target="host:8080",
        )
        db.add_all([first, second, server])
        db.flush()
        container = Container(
            server_id=server.id,
            application_id=first.id,
            docker_id="c" * 64,
            name="actions-api",
            image="qa:1",
            status="running",
            labels={},
            last_seen_at=datetime.now(UTC),
        )
        db.add(container)
        db.flush()
        evidence = {
            "records": [
                {"kind": "approval", "data": {"container_id": str(container.id)}},
                {"kind": "execution", "data": {"container_id": str(container.id)}},
                {
                    "kind": "analysis",
                    "data": {"source_context": {"bindings": [{"application_id": str(second.id)}]}},
                },
                {"kind": "repair_handoff", "data": {"application_id": str(first.id)}},
            ]
        }
        context = resource_context(db, evidence)
        assert {s["name"] for s in context["services"]} == {"问答服务", "推理服务"}
        assert len(context["resources"]) == 2


def test_report_delivery_uses_platform_sender_configuration(
    client, admin_headers, approved, fake_smtp, tmp_path, monkeypatch
):
    from cryptography.fernet import Fernet

    key = tmp_path / "mail.key"
    key.write_bytes(Fernet.generate_key())
    monkeypatch.setattr(get_settings(), "credential_key_file", key)
    response = client.put(
        "/api/v1/system/mail-settings",
        headers=admin_headers,
        json={
            "expected_version": 0,
            "host": "local-mail-test",
            "port": 1025,
            "security": "none",
            "sender": "platform@example.test",
            "username": "",
        },
    )
    assert response.status_code == 200, response.text
    result = client.post(
        f"/api/v1/reports/{approved['id']}/send", headers=admin_headers, json=send_body()
    )
    assert result.status_code == 202, result.text
    with SessionLocal() as db:
        assert (
            db.get(PostmortemDelivery, UUID(result.json()["id"])).sender == "platform@example.test"
        )
    assert report_worker.deliver_once()
    assert fake_smtp.messages == 1 and b"From: platform@example.test" in fake_smtp.data
