"""A deliberately small SELECT dialect. Every referenced column is authorized."""

import hashlib

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from sqlglot.optimizer.qualify import qualify

SAFE_FUNCTIONS = {
    "COUNT",
    "SUM",
    "AVG",
    "MIN",
    "MAX",
    "COALESCE",
    "NULLIF",
    "ABS",
    "ROUND",
    "LOWER",
    "UPPER",
    "LENGTH",
    "CHAR_LENGTH",
    "DATE_TRUNC",
    "EXTRACT",
    "CAST",
    "CURRENT_TIMESTAMP",
    "CURRENT_DATE",
    "NOW",
    "TIMESTAMPDIFF",
    "DATEDIFF",
}


def checked_query(sql, dialect, allowed, schema):
    dialect = "postgres" if dialect == "postgresql" else dialect
    try:
        statements = sqlglot.parse(sql, read=dialect)
        if len(statements) != 1 or not isinstance(statements[0], exp.Select):
            raise ValueError("single_select_required")
        tree = statements[0]
        # CTEs/subqueries and unions are excluded in v1, rather than guessing column lineage.
        forbidden = (
            exp.Subquery,
            exp.CTE,
            exp.With,
            exp.Into,
            exp.Lock,
            exp.Command,
            exp.Parameter,
            exp.Placeholder,
            exp.Hint,
        )
        if any(isinstance(node, forbidden) for node in tree.walk()):
            raise ValueError("unsupported_query")
        if any(not isinstance(star.parent, exp.Count) for star in tree.find_all(exp.Star)):
            raise ValueError("explicit_columns_required")
        if not list(tree.find_all(exp.Table)):
            raise ValueError("table_required")
        for function in tree.find_all(exp.Func):
            # SQLGlot represents boolean conjunctions as Func nodes as well.
            if isinstance(function, (exp.And, exp.Or)):
                continue
            if isinstance(function, exp.Anonymous) or function.sql_name() not in SAFE_FUNCTIONS:
                raise ValueError("function_not_allowed")
        if any(
            dtype.this == exp.DataType.Type.USERDEFINED for dtype in tree.find_all(exp.DataType)
        ):
            raise ValueError("custom_type_not_allowed")
        if any(isinstance(n, exp.Dot) and not isinstance(n, exp.Column) for n in tree.walk()):
            raise ValueError("qualified_function_not_allowed")
        metadata = {}
        for table in tree.find_all(exp.Table):
            if table.catalog or not isinstance(table.this, exp.Identifier):
                raise ValueError("cross_database_not_allowed")
            key = f"{table.db}.{table.name}"
            if key not in allowed or key not in schema:
                raise ValueError("table_not_allowed")
            actual = {c["name"]: "UNKNOWN" for c in schema[key]["columns"]}
            if not set(allowed[key]) <= actual.keys():
                raise ValueError("schema_changed")
            metadata.setdefault(table.db, {})[table.name] = {
                col: typ for col, typ in actual.items() if col in allowed[key]
            }
        tree = qualify(tree, dialect=dialect, schema=metadata, validate_qualify_columns=True)
        output_names = [select.output_name for select in tree.selects]
        if len(output_names) != len(set(output_names)):
            raise ValueError("duplicate_output_names")
        aliases = {
            table.alias_or_name: f"{table.db}.{table.name}" for table in tree.find_all(exp.Table)
        }
        output_aliases = {s.alias for s in tree.selects if s.alias}
        for column in tree.find_all(exp.Column):
            if not column.table and column.name in output_aliases:
                continue
            key = aliases.get(column.table)
            if key is None or column.name not in allowed[key]:
                raise ValueError("column_not_allowed")
        if tree.args.get("offset"):
            raise ValueError("offset_not_supported")
        limit = tree.args.get("limit")
        count = (
            int(limit.expression.this)
            if limit and isinstance(limit.expression, exp.Literal)
            else 200
        )
        tree = tree.limit(min(max(count, 0), 200))
        normalized = tree.copy()
        for literal in list(normalized.find_all(exp.Literal)):
            literal.replace(exp.Placeholder())
        summary = normalized.sql(dialect=dialect, comments=False)
        return (
            tree.sql(dialect=dialect, comments=False),
            summary,
            hashlib.sha256(summary.encode()).hexdigest(),
        )
    except (ParseError, sqlglot.errors.OptimizeError, TypeError, KeyError, AttributeError):
        raise ValueError("unsupported_query") from None
