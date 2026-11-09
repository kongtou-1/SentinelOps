"""Registered parameterized SELECTs use the existing SQL authorization and read-only adapter."""

import sqlglot
from sqlglot import exp

from app.integrations.database_sql import checked_query


def sql_tree(source, row, parameters=None):
    values = {**source.query_parameters, **(parameters or {})}
    if parameters and not set(parameters) <= set(source.allowed_query_params):
        raise ValueError("inspection_query_parameters_not_authorized")
    dialect = "postgres" if row.engine == "postgresql" else "mysql"
    try:
        trees = sqlglot.parse(source.query_sql, read=dialect)
        if len(trees) != 1 or not isinstance(trees[0], exp.Select):
            raise ValueError("single_select_required")
        tree = trees[0]
        if tree.args.get("limit") or tree.args.get("offset"):
            raise ValueError("inspection_query_pagination_managed")
        names = {node.name for node in tree.find_all(exp.Placeholder)}
        if not names <= values.keys() or not values.keys() <= names:
            raise ValueError("inspection_query_parameters_invalid")
        for node in list(tree.find_all(exp.Placeholder)):
            node.replace(exp.convert(values[node.name]))
        # Reuse all table, column, function and statement restrictions after safe AST binding.
        sql, _, _ = checked_query(
            tree.sql(dialect=dialect),
            row.engine,
            row.allowed_tables,
            row.schema_cache.get("tables", {}),
        )
        return sqlglot.parse_one(sql, read=dialect)
    except sqlglot.errors.ParseError:
        raise ValueError("unsupported_query") from None


def validate(source, row, parameters=None):
    tree = sql_tree(source, row, parameters)
    outputs = {s.output_name for s in tree.selects}
    if not {source.id_column, *source.fact_columns} <= outputs:
        raise ValueError("inspection_query_columns_missing")
    if tree.args.get("group") or tree.args.get("distinct"):
        raise ValueError("inspection_discovery_requires_object_rows")
    selected = next(s for s in tree.selects if s.output_name == source.id_column)
    expression = selected.this if isinstance(selected, exp.Alias) else selected
    if not isinstance(expression, exp.Column):
        raise ValueError("inspection_query_id_column_required")
    return tree, expression
