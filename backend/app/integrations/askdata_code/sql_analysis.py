"""SQL fragment classification via the pinned upstream sqlglot extractor.

Only a statically resolvable, read-only SELECT is eligible for the upstream
query-history / SQL-attribute pipeline. Templates, JPQL and anything the
parser cannot resolve stay as *text evidence* — never as ``sql_text`` and
never as a statement the platform executes.
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from app.integrations.askdata_sql import WRITE_NODES, AskDataSQLRejected, checked_askdata_query

_TEMPLATE = re.compile(
    r"\$\{|#\{|<\s*(?:if|where|choose|when|otherwise|foreach|set|trim)\b|"
    r"%\s*\(|%s|\{\s*\w+\s*\}|\bJPQL\b",
    re.IGNORECASE,
)
_SELECT_RE = re.compile(r"^\s*(?:with\b|select\b|\(\s*select\b)", re.IGNORECASE)


def _tables(result) -> dict[str, set[str]]:
    return {
        str(name): {str(column) for column in (match.columns or ())}
        for name, match in (result.tables or {}).items()
    }


def _canonical(name: str) -> str:
    return str(name).rsplit(".", 1)[-1].strip('"`[]').casefold()


def _ast_tables(parsed) -> dict[str, set[str]]:
    """Fallback when the upstream extractor has no catalog schema to qualify.

    Code SQL rarely carries schema qualifiers, so ``all_schemas={}`` can leave
    the upstream result empty. sqlglot's own AST still resolves table and
    column identifiers, which is enough for the authorization check.
    """
    tables: dict[str, set[str]] = {}
    for node in parsed.find_all(exp.Table):
        name = f"{node.db}.{node.name}" if node.db else node.name
        tables.setdefault(name, set())
    names = list(tables)
    for node in parsed.find_all(exp.Column):
        owner = node.table or (names[0] if len(names) == 1 else "")
        if not owner:
            continue
        matched = next((n for n in names if _canonical(n) == _canonical(owner)), None)
        target = matched or (names[0] if len(names) == 1 else None)
        if target:
            tables[target].add(node.name)
    return tables


def analyze_sql(sql: str, dialect: str = "postgres") -> dict:
    """Return parse state, normalized SQL, and the referenced tables/columns."""
    text = (sql or "").strip()
    empty = {
        "parse_state": "dynamic",
        "sql_text": "",
        "tables": {},
        "normalized": "",
        "reason": "empty_sql",
    }
    if not text:
        return empty
    if _TEMPLATE.search(text) or not _SELECT_RE.match(text):
        return {**empty, "reason": "template_or_non_select"}
    try:
        statements = [node for node in sqlglot.parse(text, read=dialect) if node is not None]
        if len(statements) != 1:
            return {**empty, "reason": "multiple_statements"}
        parsed = statements[0]
    except Exception:
        return {**empty, "reason": "sql_parse_failed"}
    if parsed is None or not isinstance(
        parsed, (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)
    ):
        return {**empty, "reason": "not_read_only_select"}
    if _TEMPLATE.search(text):
        return {**empty, "reason": "template_marker"}
    if any(isinstance(node, WRITE_NODES) for node in parsed.walk()):
        return {**empty, "reason": "not_read_only_select"}

    # Upstream extractor is pure sqlglot: no host binding, no catalog, no DB.
    try:
        from auto_ontology.catalog.parsers.sqlglot_extractor import (
            extract_tables_and_columns,
        )

        result = extract_tables_and_columns(text, dialects=["", dialect], all_schemas={})
        tables = _tables(result)
    except Exception:
        tables = {}
    if not tables:
        tables = _ast_tables(parsed)
    try:
        from auto_ontology.catalog.parsers.query_comparator import normalize_sql

        normalized = normalize_sql(text, dialect=dialect, ignore_literals=True) or ""
    except Exception:
        normalized = ""
    return {
        "parse_state": "static",
        "sql_text": text,
        "tables": tables,
        "normalized": normalized,
        "reason": "",
    }


def authorized_fragment(sql: str, allowed: dict, dialect: str = "postgres") -> bool:
    """A code SQL fragment may only mention granted tables and columns.

    ``allowed`` is the connector's authorization-filtered schema cache, keyed
    by ``schema.table`` with a ``columns`` list of ``{"name": ...}`` entries.
    """
    analysis = analyze_sql(sql, dialect)
    if analysis["parse_state"] != "static" or not analysis["tables"]:
        return False
    try:
        statements = [node for node in sqlglot.parse(sql, read=dialect) if node is not None]
        if len(statements) != 1:
            return False
        tree = statements[0]
        # Named bind parameters are valid static examples, never executable
        # here. Substitute only in this authorization copy of the AST.
        for parameter in list(tree.find_all(exp.Placeholder)):
            parameter.replace(exp.Null())
        ctes = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
        for table in tree.find_all(exp.Table):
            if table.catalog:
                return False
            if not table.db and table.name in ctes:
                continue
            name = f"{table.db}.{table.name}" if table.db else table.name
            key = resolve_table(name, allowed)
            if key is None:
                return False
            schema, name = key.split(".", 1)
            table.set("db", exp.to_identifier(schema, quoted=True))
            table.set("this", exp.to_identifier(name, quoted=True))
        grants = {
            key: [column["name"] for column in table.get("columns", [])]
            for key, table in allowed.items()
        }
        checked_askdata_query(tree.sql(dialect=dialect), dialect, grants, allowed)
    except (AskDataSQLRejected, ParseError):
        return False
    return True


def resolve_table(name: str, allowed: dict) -> str | None:
    """Keep schema identity; only resolve an unqualified name if unambiguous."""
    if "." in name:
        return name if name in allowed else None
    matches = [key for key in allowed if key.rsplit(".", 1)[-1] == name]
    return matches[0] if len(matches) == 1 else None
