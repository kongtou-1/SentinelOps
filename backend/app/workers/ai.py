import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import app.repositories.worker_ai as _repository
from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.integrations.diagnostic_gateway import DiagnosticGateway
from app.integrations.harness import HarnessAdapter
from app.services.diagnosis.analyses import complete, configuration, interrupt_runs, requester_valid
from app.services.diagnosis.tools import DiagnosticTools
from app.services.knowledge.index import build_index, encoder, split_content
from app.services.operations.evaluation import interrupt as interrupt_evaluations
from app.services.operations.evaluation import replay_once
from app.services.operations.platform import pulse
from app.workers.conversations import conversation_once, interrupt_conversations

logger = logging.getLogger(__name__)
HEARTBEAT = Path("/tmp/ai-worker.heartbeat")


def index_once():
    with SessionLocal() as db:
        document = _repository.index_once_query(db)
        if document is None:
            return False
        identifier, version, title, content = (
            document.id,
            document.version,
            document.title,
            document.content,
        )
        document.index_status = "running"
        db.commit()
    try:
        chunks = split_content(title, content)
        vectors = list(encoder().embed(chunks))
        with SessionLocal() as db:
            build_index(db, identifier, version, chunks, vectors)
    except Exception as exc:
        logger.error("Knowledge indexing failed (%s)", type(exc).__name__)
        with SessionLocal() as db:
            document = _repository.index_once_query_2(db, identifier)
            if not document.deleted_at and document.version == version:
                document.index_status, document.error_code = "failed", "indexing_failed"
                db.commit()
    return True


def analyze_once():
    with SessionLocal() as db:
        row = _repository.analyze_once_query(db)
        if row is None:
            return False
        run_id, config = row.id, row.config
        row.status, row.started_at, row.session_id = (
            "running",
            datetime.now(UTC),
            "analysis-" + str(row.id),
        )
        session_id = row.session_id
        db.commit()
        if config != configuration():
            complete(db, run_id, None, [], [], [], error="configuration_changed")
            return True
    tools = DiagnosticTools(run_id)
    answer, error = None, None

    def guard():
        from app.models import AgentAnalysis, Incident

        with SessionLocal() as current:
            analysis = current.get(AgentAnalysis, run_id)
            incident = current.get(Incident, analysis.incident_id)
            if (
                analysis.deleted_at
                or analysis.status != "running"
                or incident.deleted_at
                or incident.status in ("closed", "merged")
                or not requester_valid(current, analysis)
            ):
                raise ValueError("task_authorization_changed")

    gateway = DiagnosticGateway(tools, expected_config=config, request_guard=guard)
    try:
        context = tools.call("get_alert_context", {}, internal=True)
        # Initial retrieval is deterministic; the model can refine it using search_knowledge.
        tools.call("search_knowledge", {"query": context["data"]["title"][:1000]}, internal=True)
        history_index = tools.call("search_agent_history", {}, internal=True)["data"]
        prompt = (
            "请分析这次事故。先查看 Skill 目录并加载匹配的指引，读取相关指标和知识，"
            "再输出诊断 JSON。处置仅为人工提案。\n"
            + json.dumps(
                {"context": context, "knowledge": tools.knowledge, "history_index": history_index},
                ensure_ascii=False,
            )
        )
        with gateway.running() as url:
            answer, error = HarnessAdapter().run(
                url,
                gateway.token,
                config["model"],
                prompt,
                session_id,
                max(1, gateway.expires - time.monotonic()),
            )
        error = gateway.error or tools.blocked or error
        tools.calls.append(
            {
                "runtime_tools": gateway.runtime_tools,
                "provider_requests": gateway.requests,
                "provider_attempts": gateway.attempts,
            }
        )
    except Exception as exc:
        logger.error("AI analysis failed (%s)", type(exc).__name__)
        error = tools.blocked or "analysis_failed"
    with SessionLocal() as db:
        complete(db, run_id, answer, tools.evidence, tools.knowledge, tools.calls, error)
    return True


def main():
    logging.basicConfig(level=logging.INFO)
    with engine.connect() as owner:
        if not _repository.main_query(owner):
            raise SystemExit("Another AI worker is active")
        owner.commit()
        with SessionLocal() as db:
            interrupt_runs(db)
            interrupt_conversations(db)
            interrupt_evaluations(db)
        logger.info(
            "AI worker ready; single concurrency; analysis enabled=%s", get_settings().ai_enabled
        )
        while True:
            pulse("ai")
            HEARTBEAT.write_text(str(time.time()), encoding="utf-8")
            _repository.main_query_2(owner)
            owner.commit()
            worked = index_once()
            if get_settings().ai_enabled:
                worked = conversation_once() or analyze_once() or worked
            if not worked:
                worked = replay_once()
            if not worked:
                time.sleep(5)


if __name__ == "__main__":
    main()
