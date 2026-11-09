import json
import time
from datetime import UTC, datetime

import app.repositories.worker_conversations as _repository
from app.db.session import SessionLocal
from app.integrations.diagnostic_gateway import DiagnosticGateway
from app.integrations.harness import SYSTEM, HarnessAdapter
from app.models import AnalysisMessage, AnalysisRun
from app.schemas.diagnosis import Diagnosis
from app.services.diagnosis.analyses import configuration, requester_valid, validate_result
from app.services.diagnosis.conversations import (
    event,
    leave_pending,
    valid_evidence,
    valid_knowledge,
)
from app.services.diagnosis.tools import READ_ONLY_TOOLS, DiagnosticTools
from app.services.knowledge.agent_results import HISTORY_GUIDE
from app.services.knowledge.context import bounded, carry_records, evidence_catalog, recent_messages
from app.utils.redaction import redact

CHAT_SYSTEM = (
    (
        SYSTEM[: SYSTEM.index("输出单个 JSON")]
        + """\n与用户持续对话，可以解释正在检查的内容、回答问题并接收补充。
回复使用简短段落并换行。用户陈述和历史聊天不是工具核验证据。
历史观测保留时间，不能冒充当前状态。需要新鲜状态时重新调用工具。
形成或修订诊断时调用 submit_diagnosis，提交符合 schema 且引用有效证据的候选结论。
工具读取失败只作为数据缺口，不得把带 error_code 的记录放进 evidence_ids。
提交被拒绝时按返回的无效引用或字段错误修正后重交；仅成功提交才算形成候选结论。
候选结论由用户手动采用，不会自动申请审批或执行动作。普通解释无需提交诊断。
"""
    )
    + HISTORY_GUIDE
    + " 历史证据目录仅含定位信息，使用前通过历史读取工具查阅原文并保留观测时间。"
)
CHAT_TOOLS = {**READ_ONLY_TOOLS, "submit_diagnosis": Diagnosis}


class ConversationTools(DiagnosticTools):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.submission_error = None

    def call(self, name, arguments, internal=False):
        try:
            result = super().call(name, arguments, internal)
        except (ValueError, TypeError):
            if name == "submit_diagnosis":
                self.submission_error = "invalid_response"
            raise
        if name == "submit_diagnosis":
            self.submission_error = None
        return result

    def dispatch(self, db, row, incident, name, body):
        if name == "submit_diagnosis":
            return validate_result(
                db,
                row,
                body.model_dump(mode="json"),
                valid_evidence(db, row, self.evidence),
                valid_knowledge(db, self.knowledge),
            )
        return super().dispatch(db, row, incident, name, body)


def record(run_id, kind, data):
    data = redact(data)
    with SessionLocal() as db:
        conversation, run = _repository.record_query(db, run_id)
        if run.status not in ("running", "cancelling"):
            return
        if kind == "model_started":
            run.model_requests = data["request_index"]
        elif kind == "tool_started" and not data.get("internal"):
            run.tool_count += 1
        elif kind == "tool_finished" and data.get("result"):
            result = data["result"]
            run.evidence = [*run.evidence, result]
            run.knowledge = data.get("knowledge", run.knowledge)
            run.tool_calls = [
                *run.tool_calls,
                {
                    "call_id": data["call_id"],
                    "tool": data["tool"],
                    "evidence_id": result["id"],
                    "duration_ms": data["duration_ms"],
                    "arguments": data.get("arguments", {}),
                    "internal": bool(data.get("internal")),
                },
            ]
            if data["tool"] == "submit_diagnosis":
                run.candidate = result["data"]
                event(db, conversation, run, "diagnosis_candidate", {})
        elif kind in ("messages_dispatched", "messages_accepted"):
            for identifier in data["message_ids"]:
                message = _repository.record_query_2(db, identifier)
                if message and message.run_id == run.id:
                    message.sdk_message_id = data["sdk_message_id"]
                    if message.status != "accepted":
                        message.status = "accepted" if kind == "messages_accepted" else "waiting"
        elif kind == "assistant_delta":
            key = f"{run.id}:{data['request_index']}:{data.get('attempt_index', 1)}"
            message = _repository.record_query_3(db, key, run)
            if message is None:
                message = AnalysisMessage(
                    conversation_id=conversation.id,
                    run_id=run.id,
                    role="assistant",
                    content="",
                    status="streaming",
                    stream_key=key,
                )
                _repository.record_query_4(db, message)
            message.content += data["text"]
        elif kind == "assistant_interrupted":
            key = f"{run.id}:{data['request_index']}:{data.get('attempt_index', 1)}"
            message = _repository.record_query_3(db, key, run)
            if message:
                message.status = "interrupted"
        elif kind == "assistant_committed":
            # SDK settlement is authoritative; merge the matching visible stream projection.
            candidates = _repository.record_query_5(db, run)
            message = next((m for m in candidates if m.content == data["text"]), None)
            if message is None:
                message = AnalysisMessage(
                    conversation_id=conversation.id,
                    run_id=run.id,
                    role="assistant",
                    content=data["text"],
                    status="committed",
                )
                _repository.record_query_6(db, message)
            message.status, message.sdk_message_id = "committed", data.get("sdk_message_id")
        event(db, conversation, run, kind, data)
        db.commit()


def stop_reason(run_id):
    with SessionLocal() as db:
        incident, run = _repository.stop_reason_query(db, run_id)
        if run.status in ("cancelling", "cancelled"):
            return "cancelled"
        if (
            run.status != "running"
            or run.deleted_at
            or incident.deleted_at
            or incident.status in ("closed", "merged")
        ):
            return "incident_changed"
        if not requester_valid(db, run):
            return "requester_unavailable"
        if run.config != configuration():
            return "configuration_changed"
    return None


def pending(run_id):
    with SessionLocal() as db:
        conversation, run = _repository.pending_query(db, run_id)
        if run.status != "running":
            return []
        rows = _repository.pending_query_2(db, run_id)
        result = []
        for row in rows:
            row.status = "dispatched"
            result.append({"message_id": str(row.id), "content": row.content})
            event(db, conversation, run, "message_dispatching", {"message_id": str(row.id)})
        db.commit()
        return result


def finish(run_id, error):
    with SessionLocal() as db:
        conversation, run = _repository.finish_query(db, run_id)
        error = "cancelled" if run.status == "cancelling" else error
        if not error and run.candidate:
            try:
                run.candidate = validate_result(
                    db,
                    run,
                    run.candidate,
                    valid_evidence(db, run, run.evidence),
                    valid_knowledge(db, run.knowledge),
                )
            except (ValueError, TypeError):
                error = "invalid_response"
        run.status = "cancelled" if error == "cancelled" else "failed" if error else "completed"
        run.error_code, run.finished_at = error, datetime.now(UTC)
        leave_pending(db, run)
        event(db, conversation, run, "run_" + run.status, {"error_code": error})
        db.commit()


def guard_conversation(run_id):
    reason = stop_reason(run_id)
    if reason:
        raise ValueError(reason)


def conversation_once():
    with SessionLocal() as db:
        run = _repository.conversation_once_query(db)
        if run is None:
            return False
        conversation = _repository.conversation_once_query_2(db, run)
        if run.status != "queued":
            return False
        run_id, config = run.id, run.config
        run.status, run.started_at, run.session_id = (
            "running",
            datetime.now(UTC),
            "conversation-run-" + str(run.id),
        )
        event(db, conversation, run, "run_started", {"model": config["model"]})
        history, previous = _repository.conversation_once_query_3(conversation, db, run)
        last_candidate = next(
            (p.candidate for p in reversed(previous) if p.status == "completed" and p.candidate), {}
        )
        references = set(last_candidate.get("evidence_ids", []))
        for cause in last_candidate.get("possible_causes", []):
            references.update(cause.get("evidence_ids", []))
        evidence = list(
            {
                e["id"]: {**e, "history_ref": {"kind": "conversation", "result_id": str(p.id)}}
                for p in previous
                for e in p.evidence
                if e.get("source") not in ("submit_diagnosis", "propose_action_skill")
            }.values()
        )
        evidence = carry_records(valid_evidence(db, run, evidence), references)
        knowledge = valid_knowledge(
            db, list({k["id"]: k for p in previous for k in p.knowledge}.values())
        )
        knowledge = carry_records(
            knowledge, last_candidate.get("knowledge_ids", []), max_items=10, max_chars=16000
        )
        run.evidence, run.knowledge = evidence, knowledge
        if last_candidate:
            try:
                last_candidate = validate_result(db, run, last_candidate, evidence, knowledge)
            except (ValueError, TypeError):
                last_candidate = {}
        history_input = recent_messages(history)
        sdk_id = run.session_id
        db.commit()

    def callback(kind, data):
        record(run_id, kind, data)

    tools = ConversationTools(
        run_id, row_model=AnalysisRun, definitions=CHAT_TOOLS, on_event=callback
    )
    tools.evidence, tools.knowledge = evidence, knowledge
    gateway = DiagnosticGateway(
        tools,
        definitions=CHAT_TOOLS,
        expected_config=config,
        on_event=callback,
        request_guard=lambda: guard_conversation(run_id),
    )
    error = stop_reason(run_id)
    try:
        if not error:
            context = tools.call("get_alert_context", {}, internal=True)
            tools.call(
                "search_knowledge", {"query": context["data"]["title"][:1000]}, internal=True
            )
            inputs = pending(run_id)
            history_index = tools.call("search_agent_history", {}, internal=True)["data"]
            prompt = "请基于当前事故和用户对话进行只读诊断。\n" + json.dumps(
                {
                    "context": context,
                    "knowledge": carry_records(
                        tools.knowledge,
                        last_candidate.get("knowledge_ids", []),
                        max_items=10,
                        max_chars=16000,
                    ),
                    "history": history_input,
                    "previous_diagnosis": bounded(last_candidate, 6000),
                    "historical_evidence": evidence_catalog(evidence),
                    "history_index": history_index,
                    "history_policy": "最近12条消息、8轮证据目录；更早结果按需检索读取。",
                    "user_statements": inputs,
                },
                ensure_ascii=False,
            )
            with gateway.running() as url:
                _, error = HarnessAdapter().run(
                    url,
                    gateway.token,
                    config["model"],
                    prompt,
                    sdk_id,
                    max(1, gateway.expires - time.monotonic()),
                    system_prompt=CHAT_SYSTEM,
                    on_event=callback,
                    pending=lambda: pending(run_id),
                    should_stop=lambda: stop_reason(run_id),
                    initial_ids=[m["message_id"] for m in inputs],
                )
            error = (
                stop_reason(run_id)
                or gateway.error
                or tools.blocked
                or error
                or tools.submission_error
            )
    except Exception:
        error = tools.blocked or gateway.error or "analysis_failed"
    finish(run_id, error)
    return True


def interrupt_conversations(db):
    for run in _repository.interrupt_conversations_query(db):
        conversation = _repository.interrupt_conversations_query_2(db, run)
        run.status, run.error_code, run.finished_at = (
            "interrupted",
            "worker_interrupted",
            datetime.now(UTC),
        )
        leave_pending(db, run)
        event(db, conversation, run, "run_interrupted", {"error_code": run.error_code})
    db.commit()
