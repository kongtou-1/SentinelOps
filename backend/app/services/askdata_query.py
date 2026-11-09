"""Orchestrate one Chinese ask-data run against the pinned upstream pipeline.

The API only queues a run. This module owns the authorized task bindings, the
durable step events, the budget/cancellation gates and the single executed
result that the answer, table, chart and CSV export all share.
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import UTC, datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import AskDataConversation, AskDataDefinition
from app.repositories import askdata as settings_repo
from app.repositories import askdata_runs as repository
from app.schemas.askdata_runs import (
    AskDataChartSpec,
    AskDataColumnMeta,
    AskDataEvidence,
    AskDataResult,
)
from app.services import askdata as settings
from app.services import askdata_runs as conversations

logger = logging.getLogger(__name__)

DEFAULT_BUDGET_SECONDS = 180
MAX_SQL_RECONSTRUCTIONS = 3
CHINESE_ANSWER_RULE = (
    "请使用简体中文回答，用 2 到 4 句话直接说明结论；"
    "不要复述推理过程，不要输出内部思考，不要编造 SQL 之外的数值。"
)
CLARIFICATION_HINTS = ("?", "？", "请明确", "请说明", "请补充", "无法确定", "无法判断")


class RunFailure(Exception):
    def __init__(self, code: str):
        # Driver and upstream messages are not stable codes and may contain
        # private query details. Only bounded machine-readable codes persist.
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,99}", code):
            code = "query_failed"
        super().__init__(code)
        self.code = code


def budget_seconds(connection_id=None):
    """Administrator budget from ask-data settings, environment as the fallback."""
    if connection_id is not None:
        with SessionLocal() as db:
            row = settings_repo.configuration(db, connection_id)
            if row is not None and row.budget_seconds:
                return max(30, min(int(row.budget_seconds), 900))
    try:
        value = int(os.environ.get("ASKDATA_QUERY_BUDGET_SECONDS", DEFAULT_BUDGET_SECONDS))
    except ValueError:
        value = DEFAULT_BUDGET_SECONDS
    return max(30, min(value, 900))


def _append(identifier, kind, data):
    with SessionLocal() as db:
        repository.append_event(db, identifier, kind, data)
        db.commit()


def _cancelled(identifier):
    with SessionLocal() as db:
        return repository.cancelled(db, identifier)


def _load(identifier):
    with SessionLocal() as db:
        row = repository.worker_run(db, identifier)
        if row is None or row.status != "running":
            return None
        conversation = db.get(AskDataConversation, row.conversation_id)
        user = settings_repo.active_user(db, conversation.owner_id) if conversation else None
        if conversation is None or user is None:
            raise RunFailure("run_context_missing")
        version_id = row.model_version_id
        connection = settings_repo.connection(db, row.connection_id)
        if connection is None:
            raise RunFailure("run_context_missing")
        context = repository.context_event(db, identifier) or {}
        return {
            "question": row.question,
            "conversation_id": row.conversation_id,
            "connection_id": row.connection_id,
            "version_id": version_id,
            "user_id": user.id,
            "context": context,
            "requested_at": row.created_at,
        }


def _history(db, conversation_id, current_run_id, limit=3):
    return repository.recent_terminal_runs(db, conversation_id, current_run_id, limit)


def _payload(state, history, connector, data_retriever, semantic_retriever, code_context=None):
    """Assemble the upstream payload.

    Only certified rules enter ``acronyms`` (upstream domain rules/glossary).
    Draft references stay labelled in evidence. ``sql_examples`` is a list of
    ``{"question", "sql", "evidence"}`` dicts
    (the upstream prompt renders them with ``.get``). Stage 4 contributes code
    rules and statically resolved, grant-checked SQL from the source snapshot.
    """
    code_context = code_context or {}
    evidence_lines = ["先前问答（仅作上下文，不是新的查询条件）："]
    examples = []
    for row in history:
        result = row.result or {}
        evidence_lines.append(f"问：{row.question}")
        if result.get("answer"):
            evidence_lines.append(f"答：{str(result['answer'])[:200]}")
        if result.get("sql") and len(examples) < 2:
            examples.append(
                {
                    "question": str(row.question)[:200],
                    "sql": str(result["sql"]),
                    "evidence": "先前问答",
                }
            )
    evidence = "\n".join(evidence_lines) if len(evidence_lines) > 1 else ""
    code_evidence = str(code_context.get("evidence") or "")
    if code_evidence:
        evidence = "\n".join(part for part in (evidence, code_evidence) if part)
    from app.services import askdata_settings

    with SessionLocal() as db:
        business = askdata_settings.business_context(db, state["connection_id"])
    if business["evidence"]:
        evidence = "\n".join(part for part in (evidence, business["evidence"]) if part)
    examples = [*list(code_context.get("sql_examples") or [])[:2], *examples[:2]][:3]
    return {
        "question": state["question"],
        "processing_question": state["question"],
        "connectors": [connector],
        "data_retriever": data_retriever,
        "semantic_retriever": semantic_retriever,
        "custom_prompts": CHINESE_ANSWER_RULE + "\n" + _time_context(state),
        "acronyms": [
            *list(code_context.get("domain_rules") or []),
            *list(business["acronyms"]),
        ],
        "evidence": evidence,
        "sql_examples": examples,
        "target_db": str(state["connection_id"]),
        "validate_sql_values": True,
        "numeric_type_alignment": False,
        "shorten_answer": True,
        "skip_evidence_refinement": False,
        "prediction": False,
    }


def _time_context(state):
    requested_at = state.get("requested_at") or datetime.now(UTC)
    local = requested_at.astimezone(timezone(timedelta(hours=8)))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return (
        f"本次提问时间：{local.isoformat()}；业务时区 Asia/Shanghai（UTC+08:00）。"
        f"‘今天’为 {start.isoformat()}（含）至 {end.isoformat()}（不含）；"
        f"数据库会话使用 UTC，对应边界为 {start.astimezone(UTC).isoformat()} "
        f"至 {end.astimezone(UTC).isoformat()}。不要用数据库的 UTC CURRENT_DATE 代替本地日期。"
        "只能使用授权目录中的表和字段；缺少字段时明确说明缺口，不编造字段或用常量替代。"
        "Token 统计中的 NULL 表示未知，不能默认当作零。"
    )


def _guard(user_id, connection_id, version_id, writing):
    with SessionLocal() as db:
        actor = settings_repo.active_user(db, user_id)
        if actor is None:
            raise RunFailure("authorization_revoked")
        try:
            settings.validate_version(
                db,
                actor,
                connection_id,
                version_id,
                "askdata:manage" if writing else "askdata:run",
            )
        except HTTPException as exc:
            raise RunFailure("authorization_revoked") from exc


def chart_for(columns, rows) -> AskDataChartSpec:
    return _chart(columns, rows)


def _chart(columns, rows) -> AskDataChartSpec:
    if not columns or not rows:
        return AskDataChartSpec()
    numeric = [column.name for column in columns if column.kind in ("integer", "decimal")]
    if not numeric:
        return AskDataChartSpec()
    categorical = [column for column in columns if column.kind in ("text", "date", "datetime")]
    if not categorical:
        return AskDataChartSpec()
    x = categorical[0].name
    temporal = any(column.name == x and column.kind in ("date", "datetime") for column in columns)
    return AskDataChartSpec(
        kind="line" if temporal else "bar",
        x=x,
        y=numeric[:3],
        title="查询结果",
    )


def _evidence(db, version_id, sql):
    from app.services import askdata_code_rules as code_rules

    definitions = db.scalars(
        select(AskDataDefinition).where(
            AskDataDefinition.model_version_id == version_id,
            AskDataDefinition.deleted_at.is_(None),
        )
    ).all()
    lowered = sql.lower()
    matched = [row for row in definitions if row.object_key and row.object_key.lower() in lowered][
        :10
    ]
    items = [
        AskDataEvidence(
            kind=row.object_kind,
            name=row.object_key,
            description=str((row.payload or {}).get("description", ""))[:200],
            certified=bool(row.certified and not row.review_required),
        )
        for row in matched
    ]
    # Stage 4: include the code rules actually referenced by the final SQL,
    # with snapshot / commit / path / line provenance for the answer view.
    try:
        code_items = [
            AskDataEvidence(**item) for item in code_rules.evidence_rules(db, version_id, sql)
        ]
    except Exception:
        logger.exception("Code-rule evidence lookup failed (version=%s)", version_id)
        code_items = []
    items.extend(code_items)
    if items:
        certification = "certified" if all(item.certified for item in items) else "draft"
    elif definitions:
        certification = "draft"
    else:
        certification = "none"
    return items, certification


def _result(identifier, state, answer, connector):
    text = str(answer.get("response") or answer.get("answer") or "").strip()
    sql = str(answer.get("sql_code") or "").strip()
    needs_clarification = bool(text) and any(hint in text for hint in CLARIFICATION_HINTS)
    if not sql:
        return (
            AskDataResult(
                answer=text,
                sql="",
                certification="none",
                clarification=text if needs_clarification else None,
            ),
            "needs_clarification" if needs_clarification else "completed",
        )

    try:
        raw = connector.execute_raw(sql)
    except ValueError as exc:
        raise RunFailure(str(exc.args[0]) if exc.args else "sql_rejected") from exc
    except Exception as exc:
        code = str(exc.args[0]) if exc.args else "query_execution_failed"
        raise RunFailure(code) from exc

    columns = [AskDataColumnMeta.model_validate(item) for item in (raw.get("column_types") or [])]
    if not columns:
        columns = [AskDataColumnMeta(name=name, kind="text") for name in raw.get("columns", [])]
    rows = raw.get("rows", [])
    with SessionLocal() as db:
        # Masking happens before persistence so answer, table, chart and CSV
        # all expose the same protected values.
        from app.services import askdata_settings

        if state.get("connection_id"):
            rows = askdata_settings.mask_result(db, state["connection_id"], columns, rows)
        evidence, certification = _evidence(db, state["version_id"], sql)
    scalar_summary = (
        len(rows) == 1
        and columns
        and all(column.kind in ("integer", "decimal") for column in columns)
    )
    if scalar_summary or not re.search(r"[\u4e00-\u9fff]", text):
        # Upstream can return an English SQL explanation without result values.
        # Render the final, masked rows directly instead of translating a draft.
        text = _chinese_result_summary(columns, rows, bool(raw.get("truncated")))
    result = AskDataResult(
        answer=text,
        sql=sql,
        executed_sql=raw.get("executed_sql"),
        columns=columns,
        rows=rows,
        chart=_chart(columns, rows),
        evidence=evidence,
        truncated=bool(raw.get("truncated")),
        query_time=raw.get("observed_at"),
        row_count=len(rows),
        certification=certification,
    )
    return result, "completed"


def _chinese_result_summary(columns, rows, truncated=False):
    if not rows:
        return "查询完成，没有符合条件的记录。"
    prefix = f"查询返回{'至少 ' if truncated else ' '}{len(rows)} 行。"
    labels = {
        "calls": "调用次数",
        "total_calls": "调用次数",
        "input_tokens": "输入 Token",
        "total_input_tokens": "输入 Token",
        "output_tokens": "输出 Token",
        "total_output_tokens": "输出 Token",
    }
    samples = []
    for row in rows[:3]:
        samples.append(
            "，".join(
                f"{labels.get(column.name.lower(), column.name)}："
                f"{'未设置' if value is None else str(value)[:100]}"
                for column, value in zip(columns[:6], row[:6], strict=False)
            )
        )
    if len(rows) > 3 or len(columns) > 6:
        prefix += "以下为部分结果，完整结果见表格。"
    return prefix + "；".join(samples) + "。"


def _events(identifier, stream_fn, payload, deadline):
    """Consume upstream events, persisting short labels without internal thoughts."""
    answer = None
    generated_sql = ""
    failure = None
    reconstructions = 0
    for event in stream_fn(payload):
        if time.monotonic() > deadline:
            raise RunFailure("query_budget_exceeded")
        if _cancelled(identifier):
            raise RunFailure("run_cancelled")
        kind = (event or {}).get("type")
        if kind == "step":
            node = str(event.get("node") or "")
            phase = event.get("phase")
            if node == "reconstruct_sql" and phase == "start":
                reconstructions += 1
                if reconstructions > MAX_SQL_RECONSTRUCTIONS:
                    return {
                        "response": (
                            f"在当前授权数据和业务口径下，SQL 已修正 {MAX_SQL_RECONSTRUCTIONS} 次"
                            "仍未通过校验，已停止重试。请补充所需字段授权或明确统计口径后重试。"
                        ),
                        "sql_code": "",
                    }
            status = "running" if phase == "start" else "completed"
            summary = conversations.label(node)
            if status == "running":
                summary = f"{summary}（进行中）"
            _append(
                identifier,
                "step",
                {"step": node, "status": status, "summary": summary},
            )
        elif kind == "sql":
            generated_sql = str(event.get("sql") or "")
        elif kind == "result":
            answer = event.get("answer") or {}
        elif kind == "error":
            failure = "agent_failed"
            break
    if answer is None:
        raise RunFailure(failure or "agent_returned_no_answer")
    if isinstance(answer, dict) and generated_sql and not answer.get("sql_code"):
        answer["sql_code"] = generated_sql
    return answer


def _finish(identifier, status, result=None, error_code=None):
    with SessionLocal() as db:
        repository.finish_run(
            db,
            identifier,
            status,
            result=result.model_dump() if result is not None else None,
            error_code=RunFailure(error_code).code if error_code is not None else None,
        )
        db.commit()


def run(identifier, *, stream_fn=None):
    """Execute one claimed run. Safe to call again: non-running rows are ignored."""
    try:
        state = _load(identifier)
    except RunFailure as exc:
        _finish(identifier, "failed", error_code=exc.code)
        return
    if state is None:
        return

    user_id = state["user_id"]
    connection_id = state["connection_id"]
    version_id = state["version_id"]
    deadline = time.monotonic() + budget_seconds(connection_id)
    try:
        from auto_ontology.host import HostBindings

        from app.integrations.askdata_model import model_factory
        from app.integrations.askdata_semantic import (
            BuildStorage,
            embedding_factory,
            settings_reader,
            vector_factory,
        )
        from app.integrations.ontology import task

        if stream_fn is None:
            from auto_ontology.retrieval.text_to_sql.main import stream_agent_response

            stream_fn = stream_agent_response

        storage = BuildStorage(
            version_id,
            lambda writing: _guard(user_id, connection_id, version_id, writing),
        )
        connector = settings.database_connector(user_id, connection_id, version_id, gate="askdata")
        from app.services import askdata_settings

        with SessionLocal() as db:
            task_model_config = askdata_settings.model_config(db, connection_id)
        bindings = HostBindings(
            connection_id=connection_id,
            model_version_id=version_id,
            user_id=user_id,
            model_factory=model_factory,
            store_factory=storage.store,
            transaction_factory=storage.transaction,
            vector_factory=vector_factory,
            connector_factory=lambda: [connector],
            embedding_factory=embedding_factory,
            settings_reader=settings_reader,
            cancelled=lambda: _cancelled(identifier),
            model_config=task_model_config,
        )
        success = False
        try:
            with task(bindings):
                from auto_ontology.utils.retriever import (
                    get_data_objects_retriever,
                    get_semantic_objects_retriever,
                )

                with SessionLocal() as db:
                    history = _history(db, state["conversation_id"], identifier)
                from app.services import askdata_code_rules as code_rules

                try:
                    code_context = code_rules.context_payload(
                        version_id, connector.authorized_tables(), connector.dialect
                    )
                except Exception:
                    logger.exception("Code-rule context failed (version=%s)", version_id)
                    code_context = {}
                payload = _payload(
                    state,
                    history,
                    connector,
                    get_data_objects_retriever(),
                    get_semantic_objects_retriever(),
                    code_context,
                )
                _append(
                    identifier,
                    "step",
                    {
                        "step": "running",
                        "status": "running",
                        "summary": conversations.label("running"),
                    },
                )
                answer = _events(identifier, stream_fn, payload, deadline)
                result, status = _result(identifier, state, answer, connector)
                _append(
                    identifier,
                    "step",
                    {
                        "step": status,
                        "status": "completed",
                        "summary": conversations.label(status),
                    },
                )
                _finish(identifier, status, result=result)
                success = True
        finally:
            storage.close(success=success)
    except RunFailure as exc:
        _append(
            identifier,
            "step",
            {"step": "failed", "status": "failed", "summary": conversations.label("failed")},
        )
        _finish(identifier, "failed", error_code=exc.code)
    except Exception:
        logger.exception("Ask-data run failed (run=%s)", identifier)
        _append(
            identifier,
            "step",
            {"step": "failed", "status": "failed", "summary": conversations.label("failed")},
        )
        _finish(identifier, "failed", error_code="query_failed")
