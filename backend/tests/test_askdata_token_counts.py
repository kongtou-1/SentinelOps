"""Token counters remain queryable without exposing authentication tokens."""

import time
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.integrations.database_readonly import query
from app.schemas.service_databases import DatabaseInput
from app.services import askdata_query
from app.utils.redaction import redact


def test_database_scope_accepts_counters_but_rejects_credentials():
    body = {
        "name": "Usage",
        "engine": "postgresql",
        "host": "localhost",
        "port": 5432,
        "database": "usage_test",
        "username": "readonly",
        "environment": "test",
        "allowed_tables": {"public.model_usage": ["input_tokens", "output_tokens"]},
    }
    assert DatabaseInput(**body).allowed_tables == body["allowed_tables"]
    for column in ("access_token", "refresh_token", "api_key", "input_tokens_secret"):
        with pytest.raises(ValidationError):
            DatabaseInput(**{**body, "allowed_tables": {"public.model_usage": [column]}})
    assert redact({"input_tokens": "credential-in-wrong-field"}) == {"input_tokens": "[REDACTED]"}


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ((Decimal("42"), 5.0, None, 123456), ["42", "5.0", None, "[REDACTED]"]),
        (("private-value", True, "987", "credential"), ["[REDACTED]"] * 4),
    ],
)
def test_query_preserves_only_native_numeric_token_counters(values, expected):
    class Cursor:
        description = [
            (name, None)
            for name in ("input_tokens", "output_tokens", "total_input_tokens", "access_token")
        ]

        def __enter__(self):
            self.rows = iter([values])
            return self

        def __exit__(self, *args):
            pass

        def execute(self, sql):
            pass

        def fetchone(self):
            return next(self.rows, None)

    result = query(SimpleNamespace(cursor=Cursor), "SELECT counters")
    assert result["rows"] == [expected]


def test_today_is_anchored_to_question_time_in_shanghai():
    context = askdata_query._time_context({"requested_at": datetime(2026, 11, 8, 17, tzinfo=UTC)})
    assert "2026-11-09T00:00:00+08:00" in context
    assert "2026-11-10T00:00:00+08:00" in context
    assert "2026-11-08T16:00:00+00:00" in context
    assert "2026-11-09T16:00:00+00:00" in context


def test_reconstruction_stops_with_an_actionable_gap(monkeypatch):
    events = []
    monkeypatch.setattr(askdata_query, "_append", lambda *args: events.append(args))
    monkeypatch.setattr(askdata_query, "_cancelled", lambda identifier: False)
    attempts = []

    def pipeline(payload):
        for index in range(20):
            attempts.append(index)
            yield {"type": "step", "node": "reconstruct_sql", "phase": "start"}
            yield {"type": "step", "node": "reconstruct_sql", "phase": "end"}
        pytest.fail("An unanswerable question must not exhaust the full pipeline")

    answer = askdata_query._events("run", pipeline, {}, time.monotonic() + 10)
    result, status = askdata_query._result("run", {}, answer, None)
    assert len(attempts) == askdata_query.MAX_SQL_RECONSTRUCTIONS + 1
    assert status == "needs_clarification" and not result.sql
    assert "字段授权" in result.clarification and "已停止重试" in result.clarification


def test_scalar_answer_uses_final_values_without_a_mixed_metric_chart(monkeypatch):
    monkeypatch.setattr(askdata_query, "_evidence", lambda *args: ([], "none"))
    raw = {
        "columns": ["total_calls", "total_input_tokens", "total_output_tokens"],
        "column_types": [
            {"name": name, "kind": "integer"}
            for name in ("total_calls", "total_input_tokens", "total_output_tokens")
        ],
        "rows": [["2", "30", "10"]],
        "truncated": False,
    }
    result, status = askdata_query._result(
        uuid4(),
        {"version_id": uuid4()},
        {"response": "此 SQL 汇总今天的调用与 Token。", "sql_code": "SELECT usage"},
        SimpleNamespace(execute_raw=lambda sql: raw),
    )
    assert status == "completed"
    assert "调用次数：2" in result.answer
    assert "输入 Token：30" in result.answer and "输出 Token：10" in result.answer
    assert result.chart.kind == "none"
