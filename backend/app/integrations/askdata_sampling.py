"""Bounded, explicitly-authorized row sampling for semantic builds.

Upstream profiling emits ``SELECT * FROM <qualified> LIMIT 1000``. The platform
never forwards that statement: the platform's SQL gate requires explicit
columns, and a wildcard would pull fields outside the connection's grant into
prompts and vector rows. This module recognizes that single sampling shape and
rewrites it to the granted columns with a smaller row cap. Every other
statement is left untouched and still passes through the platform SQL gate.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

SAMPLE_ROW_LIMIT = 50


def sample_statement(sql, dialect, tables, row_limit=SAMPLE_ROW_LIMIT, excluded=None):
    """Return a rewritten SELECT, ``""`` for a sample that must not run, or None.

    ``None`` means the statement is not an upstream table sample. ``""`` means
    it is a sample but the target is outside the authorized scope.
    """
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError, ValueError):
        return None
    if not isinstance(tree, exp.Select):
        return None
    stars = list(tree.find_all(exp.Star))
    if len(stars) != 1 or not isinstance(stars[0].parent, exp.Select):
        return None
    if len(tree.expressions) != 1:
        return None
    if any(
        tree.args.get(key)
        for key in ("where", "group", "having", "order", "joins", "offset", "with", "qualify")
    ):
        return None
    references = list(tree.find_all(exp.Table))
    if len(references) != 1:
        return None
    reference = references[0]
    if reference.catalog or not reference.db or not isinstance(reference.this, exp.Identifier):
        return None
    definition = (tables or {}).get(f"{reference.db}.{reference.name}")
    if definition is None:
        return ""
    blocked = set((excluded or {}).get(f"{reference.db}.{reference.name}", []))
    columns = [
        column["name"]
        for column in definition.get("columns", [])
        if column.get("name") and column.get("name") not in blocked
    ]
    if not columns:
        return ""
    target = exp.Table(
        this=exp.to_identifier(reference.name, quoted=True),
        db=exp.to_identifier(reference.db, quoted=True),
    )
    projection = [exp.column(name, quoted=True) for name in columns]
    bounded = exp.select(*projection).from_(target).limit(max(1, min(row_limit, 200)))
    return bounded.sql(dialect=dialect, comments=False)
