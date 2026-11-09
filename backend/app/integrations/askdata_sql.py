"""Read-only SQL gate for natural-language ask-data runs.

The diagnosis and inspection gate (``database_sql.checked_query``) deliberately
stays a small SELECT dialect. Ask-data needs CTEs, derived tables, set
operations, conditional expressions and window functions, so it owns a separate
entry point; no existing caller changes behaviour.

The optimizer only ever sees granted columns. After qualification every column
is checked against the allowed sources of its own scope, so a CTE or derived
table cannot smuggle in a field outside the connection's grant.
"""

from __future__ import annotations

import hashlib

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.qualify import qualify

SYSTEM_SCHEMAS = frozenset(
    {
        "information_schema",
        "performance_schema",
        "pg_catalog",
        "pg_temp",
        "pg_toast",
        "pg_toast_temp",
        "mysql",
        "sys",
    }
)

# Uppercased ``Expression.sql_name()`` values, not raw SQL text. Unknown
# functions are parsed as Anonymous and rejected before this lookup.
SAFE_FUNCTIONS = frozenset(
    {
        "ABS",
        "AND",
        "ARRAY_AGG",
        "AVG",
        "CASE",
        "CAST",
        "CEIL",
        "CEILING",
        "CHAR_LENGTH",
        "CHARACTER_LENGTH",
        "COALESCE",
        "CONCAT",
        "CONCAT_WS",
        "COUNT",
        "CUME_DIST",
        "CURRENT_DATE",
        "CURRENT_TIME",
        "CURRENT_TIMESTAMP",
        "DATE",
        "DATE_ADD",
        "DATE_FORMAT",
        "DATE_SUB",
        "DATE_TRUNC",
        "DATEDIFF",
        "DAY",
        "DENSE_RANK",
        "EXTRACT",
        "FIRST_VALUE",
        "FLOOR",
        "GREATEST",
        "GROUP_CONCAT",
        "HOUR",
        "IF",
        "IFNULL",
        "ISNULL",
        "LAG",
        "LAST_VALUE",
        "LEAD",
        "LEAST",
        "LENGTH",
        "LOWER",
        "LTRIM",
        "MAX",
        "MIN",
        "MINUTE",
        "MOD",
        "MONTH",
        "NOT",
        "NOW",
        "NTH_VALUE",
        "NTILE",
        "NULLIF",
        "OR",
        "PERCENT_RANK",
        "POWER",
        "RANK",
        "REPLACE",
        "ROUND",
        "ROW_NUMBER",
        "RTRIM",
        "SECOND",
        "SIGN",
        "SQRT",
        "STRING_AGG",
        "SUBSTR",
        "SUBSTRING",
        "SUM",
        "TIMESTAMPDIFF",
        "TIMESTAMP_TRUNC",
        "TO_CHAR",
        "TRIM",
        "TRY_CAST",
        "UNIX_TIMESTAMP",
        "UPPER",
        "YEAR",
    }
)

_FORBIDDEN_NODE_NAMES = (
    "Insert",
    "Update",
    "Delete",
    "Merge",
    "Create",
    "Drop",
    "Alter",
    "TruncateTable",
    "Command",
    "Into",
    "Lock",
    "Parameter",
    "Placeholder",
    "Hint",
    "Pragma",
    "Transaction",
    "Commit",
    "Rollback",
    "Use",
    "Set",
    "Copy",
    "LoadData",
    "Grant",
    "Revoke",
    "Attach",
    "Detach",
    "Kill",
)
FORBIDDEN_NODES = tuple(
    getattr(exp, name) for name in _FORBIDDEN_NODE_NAMES if hasattr(exp, name)
)
WRITE_NODES = tuple(
    getattr(exp, name)
    for name in ("Insert", "Update", "Delete", "Merge")
    if hasattr(exp, name)
)
SELECT_ROOTS = (exp.Select, exp.Union, exp.Except, exp.Intersect)


class AskDataSQLRejected(ValueError):
    """A stable failure code suitable for the run's error_code column."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _reject(code: str):
    raise AskDataSQLRejected(code)


def _unwrapped(node):
    if isinstance(node, exp.Subquery):
        return _unwrapped(node.this)
    return node


def _projection_names(node) -> set[str]:
    """Output column names of a SELECT or a set operation, including aliases."""
    body = _unwrapped(node)
    if isinstance(body, exp.Select):
        return {
            projection.alias_or_name
            for projection in body.expressions
            if projection.alias_or_name
        }
    if isinstance(body, (exp.Union, exp.Except, exp.Intersect)):
        return _projection_names(body.this) | _projection_names(body.expression)
    return set()


def _selects(node):
    return [select for select in node.find_all(exp.Select)]


def _reject_forbidden(tree):
    for node in tree.walk():
        if isinstance(node, WRITE_NODES):
            _reject("write_statement_not_allowed")
        if isinstance(node, FORBIDDEN_NODES):
            _reject("unsupported_query")
        if isinstance(node, exp.DataType) and node.this == exp.DataType.Type.USERDEFINED:
            _reject("custom_type_not_allowed")
        if isinstance(node, exp.Dot) and not isinstance(node, exp.Column):
            _reject("qualified_function_not_allowed")
    for clause in tree.find_all(exp.With):
        if clause.args.get("recursive"):
            _reject("recursive_cte_not_allowed")
    for cte in tree.find_all(exp.CTE):
        if not isinstance(_unwrapped(cte.this), SELECT_ROOTS):
            _reject("write_cte_not_allowed")
    for star in tree.find_all(exp.Star):
        if not isinstance(star.parent, exp.Count):
            _reject("explicit_columns_required")
    for function in tree.find_all(exp.Func):
        if isinstance(function, exp.Anonymous) or function.sql_name().upper() not in SAFE_FUNCTIONS:
            _reject("function_not_allowed")


def _cte_names(tree) -> set[str]:
    return {cte.alias_or_name for cte in tree.find_all(exp.CTE) if cte.alias_or_name}


def _granted_columns(key, allowed, schema) -> set[str]:
    actual = {column["name"] for column in schema[key]["columns"]}
    granted = set(allowed[key])
    if not granted <= actual:
        raise AskDataSQLRejected("schema_changed")
    return granted


def _physical_tables(tree, ctes, allowed, schema):
    """Validate every real table and build the optimizer's restricted schema."""
    metadata: dict[str, dict[str, dict[str, str]]] = {}
    physical: dict[str, set[str]] = {}
    for table in tree.find_all(exp.Table):
        if table.name in ctes:
            if table.db or table.catalog:
                raise AskDataSQLRejected("table_not_allowed")
            continue
        if table.catalog or not table.db or not isinstance(table.this, exp.Identifier):
            raise AskDataSQLRejected("table_not_allowed")
        if table.db.lower() in SYSTEM_SCHEMAS:
            raise AskDataSQLRejected("system_schema_not_allowed")
        key = f"{table.db}.{table.name}"
        if key not in allowed or key not in schema:
            raise AskDataSQLRejected("table_not_allowed")
        granted = _granted_columns(key, allowed, schema)
        metadata.setdefault(table.db, {})[table.name] = {
            column: "UNKNOWN" for column in granted
        }
        physical.setdefault(table.alias_or_name, set()).update(granted)
    if not physical:
        raise AskDataSQLRejected("table_required")
    return metadata, physical


def _scope_columns(tree, ctes, allowed, schema):
    """Map every visible alias to the columns it may legitimately expose.

    Physical aliases resolve to the granted columns. CTE and derived-table
    aliases resolve to their own projection names plus the granted columns of
    the real tables they read, so aliases such as ``total`` or ``rn`` remain
    usable without widening the grant.
    """
    scopes: dict[str, set[str]] = {}
    for table in tree.find_all(exp.Table):
        if table.name in ctes or table.catalog or not table.db:
            continue
        key = f"{table.db}.{table.name}"
        if key in allowed and key in schema:
            scopes.setdefault(table.alias_or_name, set()).update(
                _granted_columns(key, allowed, schema)
            )

    def add_scope(alias, body):
        if not alias:
            return
        names = _projection_names(body)
        for table in body.find_all(exp.Table):
            if table.catalog or not table.db or table.name in ctes:
                continue
            key = f"{table.db}.{table.name}"
            if key in allowed and key in schema:
                names |= _granted_columns(key, allowed, schema)
        scopes.setdefault(alias, set()).update(names)

    for cte in tree.find_all(exp.CTE):
        add_scope(cte.alias_or_name, cte.this)
    for subquery in tree.find_all(exp.Subquery):
        add_scope(subquery.alias, subquery.this)
    return scopes


def _check_columns(tree, scopes, physical):
    projection_aliases: set[str] = set()
    for select in _selects(tree):
        projection_aliases |= _projection_names(select)
    for column in tree.find_all(exp.Column):
        if not column.table:
            if column.name in projection_aliases:
                continue
            raise AskDataSQLRejected("column_not_allowed")
        allowed = scopes.get(column.table, physical.get(column.table))
        if allowed is None or column.name not in allowed:
            raise AskDataSQLRejected("column_not_allowed")


def checked_askdata_query(sql, dialect, allowed, schema, row_limit=200):
    """Return ``(executable_sql, shape_summary, sha256)`` or raise with a code.

    ``allowed`` maps ``schema.table`` to its granted columns; ``schema`` is the
    connection's structure cache. Both are owned by the platform, never by the
    model. The returned SQL always carries a bounded LIMIT.
    """
    dialect = "postgres" if dialect == "postgresql" else dialect
    try:
        statements = [node for node in sqlglot.parse(sql, read=dialect) if node is not None]
        if len(statements) != 1:
            raise AskDataSQLRejected("multi_statement_not_allowed")
        tree = statements[0]
        if not isinstance(tree, SELECT_ROOTS) or isinstance(tree, exp.Subquery):
            raise AskDataSQLRejected("single_select_required")
        _reject_forbidden(tree)
        ctes = _cte_names(tree)
        metadata, physical = _physical_tables(tree, ctes, allowed, schema)
        try:
            tree = qualify(tree, dialect=dialect, schema=metadata, validate_qualify_columns=True)
        except OptimizeError as exc:
            # The optimizer only knows granted columns; an unresolved column is
            # a field outside the connection's grant, not a syntax accident.
            raise AskDataSQLRejected("column_not_allowed") from exc
        _check_columns(tree, _scope_columns(tree, ctes, allowed, schema), physical)
        if tree.args.get("offset"):
            raise AskDataSQLRejected("offset_not_supported")
        limit = tree.args.get("limit")
        count = row_limit
        if limit and isinstance(limit.expression, exp.Literal):
            try:
                count = int(limit.expression.this)
            except (TypeError, ValueError):
                count = row_limit
        tree = tree.limit(min(max(count, 0), row_limit))
        shape = tree.copy()
        for literal in list(shape.find_all(exp.Literal)):
            literal.replace(exp.Placeholder())
        summary = shape.sql(dialect=dialect, comments=False)
        return (
            tree.sql(dialect=dialect, comments=False),
            summary,
            hashlib.sha256(summary.encode()).hexdigest(),
        )
    except (ParseError, OptimizeError, TypeError, KeyError, AttributeError, IndexError) as exc:
        raise AskDataSQLRejected("unsupported_query") from exc
