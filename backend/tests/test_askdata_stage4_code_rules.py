"""Stage-4 source-rule extractor and build-row tests.

These tests exercise the pure logic that does not require a live database:
the Python/Java static extractors, SQL authorization filtering, and the
build-row carry-over / review / conflict logic. DB-backed build and API tests
live in ``test_askdata_stage4_service.py``.
"""

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.integrations.askdata_code import RuleDraft, extract_rules, language_for
from app.integrations.askdata_code.sql_analysis import analyze_sql, authorized_fragment
from app.models.askdata_code_rules import AskDataCodeRule
from app.services.askdata_code_rules import (
    _authorized_rules,
    _build_rows,
    _natural_key,
    _removed_fingerprint,
)

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "askdata_stage4"


def _load_sample(app):
    files = {}
    for p in sorted((FIXTURE_ROOT / app).rglob("*")):
        if p.suffix in (".py", ".java", ".xml"):
            files[p.relative_to(FIXTURE_ROOT / app).as_posix()] = p.read_text(encoding="utf-8")
    return files


def _rules_by_kind(files):
    rules, _ = extract_rules(files)
    by = {}
    for rule in rules:
        by.setdefault(rule.rule_kind, set()).add(rule.expression)
    return by


def test_samples_share_schema_but_differ_on_business_rules():
    a = _rules_by_kind(_load_sample("app_a"))
    b = _rules_by_kind(_load_sample("app_b"))

    # Same catalog structure is understood by both.
    assert "table_mapping" in a and "table_mapping" in b
    assert "column_mapping" in a and "column_mapping" in b

    # Amount unit: app_a stores cents (divide by 100); app_b stores yuan.
    assert any("CENT" in v for v in a["amount_unit"])
    assert any("100" in v for v in a["amount_unit"])
    assert any("YUAN" in v for v in b["amount_unit"])
    assert not any("CENT" in v for v in b["amount_unit"])

    # Status business rule differs.
    assert any("PAID" in v for v in a["status_filter"])
    assert any("REFUNDED" in v for v in a["status_filter"])
    assert any("SETTLED" in v for v in b["status_filter"])
    assert any("REFUND" in v for v in b["status_filter"])

    # Soft-delete strategy differs: flag column vs timestamp column.
    assert any("is_deleted" in v for v in a["soft_delete"])
    assert any("deleted_at" in v for v in b["soft_delete"])

    # Time field used for time statistics differs.
    assert any("created_at" in v for v in a["time_field"])
    assert any("paid_at" in v for v in b["time_field"])


def test_static_and_dynamic_sql_fragments():
    rules, gaps = extract_rules(_load_sample("app_a"))
    sqls = [r for r in rules if r.rule_kind == "sql_fragment"]

    static = [r for r in sqls if r.parse_state == "static"]
    dynamic = [r for r in sqls if r.parse_state == "dynamic"]

    assert static, "expected authorized static SQL fragments"
    assert dynamic, "expected dynamic / unresolved fragments to be flagged"

    # Dynamic fragments must never carry executable SQL text.
    for rule in dynamic:
        assert rule.sql_text == ""

    authorized_schema = {
        "public.orders": {
            "columns": [
                {"name": c}
                for c in (
                    "id",
                    "status",
                    "amount",
                    "is_deleted",
                    "deleted_at",
                    "created_at",
                    "paid_at",
                    "user_id",
                )
            ]
        }
    }
    for rule in static:
        assert authorized_fragment(rule.sql_text, authorized_schema)


def test_dynamic_sql_is_not_treated_as_executable():
    rules, gaps = extract_rules(_load_sample("app_a"))
    dynamic = [r for r in rules if r.rule_kind == "sql_fragment" and r.parse_state == "dynamic"]
    parse_scopes = {r.detail.get("parse_scope") for r in dynamic}
    # JPQL, MyBatis dynamic XML and runtime-concatenated SQL must be marked dynamic.
    assert "jpql" in parse_scopes
    assert any(
        scope and scope.startswith("mybatis") and "dynamic" in scope for scope in parse_scopes
    )
    assert any(scope == "native_sql" for scope in parse_scopes)  # runtime-concatenated SQL
    # Gaps record at least the scanned/parsed file counts.
    assert gaps.get("scanned_files")


def test_unauthorized_tables_are_filtered():
    schema_allowed = {"public.orders": {"columns": [{"name": "status"}, {"name": "amount"}]}}
    allowed = authorized_fragment(
        "SELECT status, SUM(amount) FROM orders GROUP BY status", schema_allowed
    )
    assert allowed is True

    blocked = authorized_fragment("SELECT secret_token FROM credentials", schema_allowed)
    assert blocked is False

    # Unauthorized column on an allowed table is also blocked.
    partial = authorized_fragment("SELECT status, internal_note FROM orders", schema_allowed)
    assert partial is False


def test_analyze_sql_returns_tables_and_columns():
    result = analyze_sql(
        "SELECT o.status, SUM(o.amount) FROM orders o WHERE o.is_deleted = 0 GROUP BY o.status"
    )
    tables = result["tables"]
    assert "orders" in tables
    # Columns are returned as a set rendered into the value string.
    assert "status" in tables["orders"]
    assert "amount" in tables["orders"]


def test_build_rows_carries_certified_and_flags_conflict():
    connection_id = uuid4()
    version_id = uuid4()
    identity = {
        "source_snapshot_id": uuid4(),
        "git_commit": "abc123",
        "svn_revision": None,
        "content_hash": "hash",
    }

    # A previously certified manual rule that now disagrees with the code.
    prior = AskDataCodeRule(
        id=uuid4(),
        connection_id=connection_id,
        model_version_id=version_id,
        language="python",
        path="app/repositories/orders.py",
        start_line=10,
        end_line=12,
        rule_kind="amount_unit",
        object_key="amount_unit",
        expression="amount / 100",  # human-certified stance
        source="manual",
        certified=True,
        review_required=False,
        fingerprint="prior-fp",
        detail={"description": "金额按分存储"},
    )

    draft = RuleDraft(
        language="python",
        path="app/repositories/orders.py",
        start_line=10,
        end_line=12,
        rule_kind="amount_unit",
        object_key="amount_unit",
        expression="amount",  # code now says raw amount, conflicts with certified
        parse_state="static",
        snippet="AMOUNT_UNIT = YUAN",
        detail={"parse_scope": "unit"},
    )

    rows = _build_rows(
        [draft],
        connection_id=connection_id,
        version_id=version_id,
        identity=identity,
        previous_rules=[prior],
    )

    key = (draft.language, draft.path, draft.rule_kind, draft.object_key)
    carried = [r for r in rows if _natural_key(r) == key]
    assert carried, "certified/manual rule must be carried forward"
    row = carried[0]
    # Human expression is preserved, code expression kept for evidence.
    assert row.expression == "amount / 100"
    assert row.detail.get("code_expression") == "amount"
    assert row.certified is True
    assert row.review_required is True
    assert row.conflict_state == "pending"
    assert row.conflict_with == prior.id


def test_build_rows_flags_removed_business_rule():
    connection_id = uuid4()
    version_id = uuid4()
    identity = {
        "source_snapshot_id": uuid4(),
        "git_commit": "abc123",
        "svn_revision": None,
        "content_hash": "hash",
    }

    prior = AskDataCodeRule(
        id=uuid4(),
        connection_id=connection_id,
        model_version_id=version_id,
        language="python",
        path="app/repositories/orders.py",
        start_line=20,
        end_line=22,
        rule_kind="soft_delete",
        object_key="is_deleted",
        expression="is_deleted = 0",
        source="auto",
        certified=False,
        review_required=False,
        fingerprint="removed-fp",
    )

    # Current extraction no longer contains the prior business rule.
    rows = _build_rows(
        [],
        connection_id=connection_id,
        version_id=version_id,
        identity=identity,
        previous_rules=[prior],
    )

    removed = [r for r in rows if r.superseded]
    assert removed, "removed business rule must be flagged for review"
    assert removed[0].review_note == "源码中已移除该规则，待复核"
    assert removed[0].conflict_with == prior.id


def test_language_for_detects_supported_extensions():
    assert language_for("a.py") == "python"
    assert language_for("b.java") == "java"
    assert language_for("c.xml") == "java"  # MyBatis mapping is Java-related
    assert language_for("d.txt") is None
    assert language_for("e.sql") is None


def test_removed_fingerprint_is_stable():
    class _Stub:
        fingerprint = "abc"

    fp = _removed_fingerprint(_Stub())
    assert fp and isinstance(fp, str) and len(fp) == 64
    assert _removed_fingerprint(_Stub()) == fp


@pytest.mark.parametrize(
    "argument, expected", [("False", "FALSE"), ("True", "TRUE"), ("None", "NULL")]
)
def test_python_is_preserves_operand(argument, expected):
    rules, _ = extract_rules({"orders.py": f"q.filter(Order.is_deleted.is_({argument}))"})
    assert [r.expression for r in rules] == [f"Order.is_deleted IS {expected}"]


def test_exclude_and_java_inequality_preserve_negation():
    rules, _ = extract_rules({"orders.py": 'q.exclude(status="CANCELLED")'})
    assert rules[0].expression == "status != CANCELLED"
    rules, _ = extract_rules({"orders.py": 'q.exclude(status="CANCELLED", is_deleted=True)'})
    assert all(r.parse_state == "dynamic" for r in rules)
    rules, _ = extract_rules(
        {
            "Orders.java": (
                "@Select(\"SELECT status FROM orders WHERE status <> 'REFUND'\") List list();"
            )
        }
    )
    assert any(r.expression == "status <> REFUND" for r in rules)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM private.orders",
        "SELECT * FROM orders",
        "SELECT o.secret FROM orders o JOIN orders p ON o.id=p.id",
        "SELECT secret FROM orders o JOIN users u ON o.id=u.id",
        "SELECT id FROM orders; DELETE FROM orders",
        "WITH d AS (DELETE FROM orders RETURNING id) SELECT id FROM d",
    ],
)
def test_code_sql_uses_full_scope_column_and_read_only_gate(sql):
    allowed = {f"public.{table}": {"columns": [{"name": "id"}]} for table in ("orders", "users")}
    assert not authorized_fragment(sql, allowed)


def test_code_sql_resolves_ctes_but_rejects_ambiguous_tables():
    allowed = {"public.orders": {"columns": [{"name": "id"}]}}
    assert authorized_fragment("WITH o AS (SELECT id FROM orders) SELECT id FROM o", allowed)
    assert authorized_fragment("SELECT count(*) FROM orders", allowed)
    assert authorized_fragment("SELECT id FROM orders WHERE id = :identifier", allowed)
    allowed["private.orders"] = allowed["public.orders"]
    assert not authorized_fragment("SELECT id FROM orders", allowed)
    assert authorized_fragment("SELECT id FROM public.orders", allowed)


def _row(kind="sql_fragment", **values):
    defaults = dict(
        language="python",
        path="orders.py",
        start_line=1,
        object_key="orders",
        expression="SELECT secret FROM private.orders",
        sql_text="SELECT secret FROM private.orders",
        detail={},
        rule_kind=kind,
        parse_state="static",
        certified=False,
        review_required=False,
        conflict_state="none",
        superseded=False,
    )
    return SimpleNamespace(**(defaults | values))


def test_unauthorized_and_pending_rules_never_enter_prompt(monkeypatch):
    from app.services import askdata_code_rules as service

    rows = [_row(), _row(sql_text="SELECT id FROM orders", review_required=True, certified=True)]
    monkeypatch.setattr(service, "SessionLocal", lambda: nullcontext(None))
    monkeypatch.setattr(service.repository, "context_rules", lambda *a, **kw: rows)
    payload = service.context_payload(uuid4(), {"public.orders": {"columns": [{"name": "id"}]}})
    assert payload == {"domain_rules": [], "glossary": [], "sql_examples": [], "evidence": ""}


def test_partial_column_grant_hides_global_and_ungranted_rules():
    rows = [
        _row("table_mapping", object_key="orders", sql_text=""),
        _row("column_mapping", object_key="orders.id", sql_text=""),
        _row("column_mapping", object_key="orders.secret", sql_text=""),
        _row("amount_unit", object_key="AMOUNT_UNIT", sql_text=""),
    ]
    result = _authorized_rules(rows, {"public.orders": {"columns": [{"name": "id"}]}}, "postgres")
    assert [r.object_key for r in result] == ["orders", "orders.id"]


def test_only_certified_rules_enter_upstream_domain_rules():
    from app.services.askdata_query import _payload

    certified = {"name": "paid", "description": "paid = 1"}
    draft = {"name": "draft", "description": "unreviewed reference"}
    payload = _payload(
        {"question": "收入", "connection_id": uuid4()},
        [],
        None,
        None,
        None,
        {"domain_rules": [certified], "glossary": [certified, draft], "evidence": "未认证参考"},
    )
    assert payload["acronyms"] == [certified]
    assert payload["evidence"] == "未认证参考"


def test_removed_rule_survives_repeated_rebuild_until_resolved():
    identity = dict(
        source_snapshot_id=uuid4(), git_commit="next", svn_revision=None, content_hash="new"
    )
    prior = AskDataCodeRule(
        id=uuid4(),
        language="python",
        path="orders.py",
        start_line=1,
        end_line=1,
        rule_kind="soft_delete",
        object_key="orders.deleted_at",
        expression="deleted_at IS NULL",
        source="auto",
        fingerprint="original",
        certified=False,
        review_required=False,
        superseded=False,
        git_commit="original-commit",
        content_hash="original-hash",
    )
    for _ in range(3):
        rows = _build_rows(
            [], connection_id=uuid4(), version_id=uuid4(), identity=identity, previous_rules=[prior]
        )
        assert len(rows) == 1
        prior = rows[0]
        assert prior.superseded and prior.review_required
        assert prior.git_commit == "original-commit"


def test_pending_review_and_resolved_manual_choice_survive_rebuilds():
    draft = RuleDraft(
        "python", "orders.py", 1, 1, "status_filter", "orders.status", "status = PAID"
    )
    identity = dict(
        source_snapshot_id=uuid4(), git_commit="next", svn_revision=None, content_hash="new"
    )
    prior = AskDataCodeRule(
        id=uuid4(),
        language=draft.language,
        path=draft.path,
        start_line=1,
        end_line=1,
        rule_kind=draft.rule_kind,
        object_key=draft.object_key,
        expression=draft.expression,
        source="manual",
        fingerprint=draft.fingerprint(),
        certified=True,
        review_required=True,
        review_note="待复核",
        conflict_state="none",
        superseded=False,
    )

    def rebuild(row):
        return _build_rows(
            [draft],
            connection_id=uuid4(),
            version_id=uuid4(),
            identity=identity,
            previous_rules=[row],
        )[0]

    current = rebuild(prior)
    assert current.review_required and current.certified
    current.review_required = False
    current.expression = "status = SETTLED"
    current.conflict_state = "resolved"  # Admin explicitly kept the manual choice.
    current = rebuild(current)
    assert not current.review_required
    assert current.conflict_state == "resolved" and current.expression == "status = SETTLED"


def test_manual_rules_with_same_column_keep_their_own_revision():
    drafts = [
        RuleDraft("python", "orders.py", line, line, "status_filter", "orders.status", expression)
        for line, expression in ((10, "status = PAID"), (20, "status != REFUND"))
    ]
    previous = [
        AskDataCodeRule(
            id=uuid4(),
            language=draft.language,
            path=draft.path,
            start_line=draft.start_line,
            end_line=draft.end_line,
            rule_kind=draft.rule_kind,
            object_key=draft.object_key,
            expression=f"manual {draft.expression}",
            source="manual",
            certified=True,
            review_required=False,
            conflict_state="resolved",
            superseded=False,
            fingerprint=draft.fingerprint(),
            detail={"code_expression": draft.expression},
        )
        for draft in reversed(drafts)
    ]
    rows = _build_rows(
        drafts,
        connection_id=uuid4(),
        version_id=uuid4(),
        identity=dict(source_snapshot_id=None, git_commit=None, svn_revision=None, content_hash=""),
        previous_rules=previous,
    )
    assert [row.expression for row in rows] == [f"manual {draft.expression}" for draft in drafts]
    assert not any(row.review_required for row in rows)


def test_structure_only_connections_do_not_publish_code_queries():
    from app.integrations.askdata_database import PlatformDatabase

    connector = PlatformDatabase(
        uuid4(),
        lambda: {"query_enabled": False, "tables": {}},
        lambda sql: None,
        query_provider=lambda snapshot: ["SELECT id FROM orders"],
    )
    assert connector.get_queries().empty


def test_runtime_attributes_are_not_invented_as_static_values():
    rules, _ = extract_rules(
        {
            "orders.py": (
                "q.filter(Order.status == runtime.status)"
                ".filter(Order.is_deleted.is_(runtime.flag))"
            )
        }
    )
    assert len(rules) == 2
    assert all(rule.parse_state == "dynamic" for rule in rules)
