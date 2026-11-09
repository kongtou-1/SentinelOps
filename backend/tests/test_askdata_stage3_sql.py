"""Stage 3: the ask-data SQL gate, typed results and the untouched diagnosis gate."""

from uuid import uuid4

import pytest

from app.integrations.askdata_database import PlatformDatabase
from app.integrations.askdata_sql import AskDataSQLRejected, checked_askdata_query
from app.integrations.database_readonly import _column_meta
from app.integrations.database_sql import checked_query

COLUMNS = ["id", "amount", "status", "created_at", "customer_id"]
ALLOWED = {"public.orders": COLUMNS}
SCHEMA = {"public.orders": {"columns": [{"name": name} for name in COLUMNS]}}


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id, amount FROM public.orders WHERE amount > 10",
        "WITH recent AS (SELECT id, amount FROM public.orders) "
        "SELECT id, SUM(amount) AS total FROM recent GROUP BY id",
        "SELECT d.id, d.total FROM "
        "(SELECT id, SUM(amount) AS total FROM public.orders GROUP BY id) d WHERE d.total > 5",
        "SELECT id FROM public.orders UNION ALL SELECT id FROM public.orders",
        "SELECT id FROM public.orders EXCEPT SELECT id FROM public.orders WHERE status='x'",
        "SELECT id FROM public.orders INTERSECT SELECT customer_id FROM public.orders",
        "SELECT o.id, ROW_NUMBER() OVER "
        "(PARTITION BY o.status ORDER BY o.created_at DESC) AS rn FROM public.orders o",
        "SELECT CASE WHEN amount > 10 THEN 'big' ELSE 'small' END AS bucket, "
        "COUNT(*) AS n FROM public.orders GROUP BY 1",
        "SELECT id FROM public.orders WHERE id IN (SELECT customer_id FROM public.orders)",
        "SELECT a.id FROM public.orders a JOIN public.orders b ON a.id=b.customer_id",
        "SELECT id AS x FROM public.orders ORDER BY x DESC LIMIT 10",
        "SELECT status, SUM(amount) FILTER (WHERE amount > 0) AS s "
        "FROM public.orders GROUP BY status",
        "SELECT DATE_TRUNC('day', created_at) AS d, COUNT(*) AS c "
        "FROM public.orders GROUP BY 1",
        "SELECT COALESCE(status,'unknown') AS s, COUNT(DISTINCT id) AS n "
        "FROM public.orders GROUP BY 1",
    ],
)
def test_complex_readonly_sql_is_accepted(sql):
    executed, summary, digest = checked_askdata_query(sql, "postgresql", ALLOWED, SCHEMA)
    assert "LIMIT" in executed.upper()
    assert summary and len(digest) == 64


@pytest.mark.parametrize(
    "sql,code",
    [
        ("SELECT id FROM public.orders; DELETE FROM public.orders", "multi_statement_not_allowed"),
        ("DELETE FROM public.orders RETURNING id", "single_select_required"),
        ("INSERT INTO public.orders (id) VALUES (1)", "single_select_required"),
        ("WITH x AS (DELETE FROM public.orders RETURNING id) SELECT id FROM x", "write_statement_not_allowed"),
        ("WITH RECURSIVE x AS (SELECT id FROM public.orders UNION ALL "
         "SELECT id FROM x) SELECT id FROM x", "recursive_cte_not_allowed"),
        ("SELECT pg_sleep(100) FROM public.orders", "function_not_allowed"),
        ("SELECT id FROM pg_catalog.pg_class", "system_schema_not_allowed"),
        ("SELECT * FROM public.orders", "explicit_columns_required"),
        ("SELECT id FROM public.orders LIMIT 5 OFFSET 10", "offset_not_supported"),
        ("SELECT secret FROM public.orders", "column_not_allowed"),
        ("SELECT id FROM public.orders WHERE secret = 1", "column_not_allowed"),
        ("SELECT id FROM other.jobs", "table_not_allowed"),
        ("SELECT id FROM x.public.orders", "table_not_allowed"),
        ("SELECT id INTO new_orders FROM public.orders", "unsupported_query"),
        ("SELECT id FROM public.orders FOR UPDATE", "unsupported_query"),
        ("CALL test()", "single_select_required"),
    ],
)
def test_unsafe_sql_is_rejected_with_a_stable_code(sql, code):
    with pytest.raises(AskDataSQLRejected) as error:
        checked_askdata_query(sql, "postgresql", ALLOWED, SCHEMA)
    assert error.value.code == code


def test_limit_is_bounded_and_shape_ignores_literals():
    first, shape_a, hash_a = checked_askdata_query(
        "SELECT id FROM public.orders WHERE amount > 10 LIMIT 5000",
        "postgresql",
        ALLOWED,
        SCHEMA,
    )
    _, shape_b, hash_b = checked_askdata_query(
        "SELECT id FROM public.orders WHERE amount > 999", "postgresql", ALLOWED, SCHEMA
    )
    assert "LIMIT 200" in first.upper()
    assert shape_a == shape_b and hash_a == hash_b


def test_mysql_dialect_and_grant_scope():
    executed, _, _ = checked_askdata_query(
        "SELECT id, COUNT(*) AS c FROM public.orders GROUP BY id", "mysql", ALLOWED, SCHEMA
    )
    assert "`public`.`orders`" in executed and "LIMIT 200" in executed.upper()
    restricted = {"public.orders": ["id", "status"]}
    with pytest.raises(AskDataSQLRejected) as error:
        checked_askdata_query(
            "SELECT amount FROM public.orders", "postgresql", restricted, SCHEMA
        )
    assert error.value.code == "column_not_allowed"


def test_diagnosis_gate_is_unchanged():
    """Builds and diagnosis must keep their original small dialect."""
    with pytest.raises(ValueError):
        checked_query(
            "WITH x AS (SELECT id FROM public.orders) SELECT id FROM x",
            "postgresql",
            ALLOWED,
            SCHEMA,
        )
    statement, _, _ = checked_query(
        "SELECT id FROM public.orders", "postgresql", ALLOWED, SCHEMA
    )
    assert "LIMIT 200" in statement.upper()


def test_column_metadata_keeps_numeric_and_temporal_kinds():
    description = [
        ("id", 23, None),
        ("amount", 1700, None),
        ("created_at", 1114, None),
        ("paid", 16, None),
        ("note", 25, None),
    ]
    assert [item["kind"] for item in _column_meta(description)] == [
        "integer",
        "decimal",
        "datetime",
        "boolean",
        "text",
    ]


def test_execute_raw_uses_platform_executor_without_sampling_rewrite():
    seen = []
    connector = PlatformDatabase(
        uuid4(),
        lambda: {"engine": "postgresql", "tables": {}},
        lambda sql: seen.append(sql) or {"columns": ["id"], "rows": [["1"]]},
    )
    sql = "WITH x AS (SELECT id FROM public.orders) SELECT id FROM x"
    result = connector.execute_raw(sql)
    assert seen == [sql]
    assert result["rows"] == [["1"]]
    with pytest.raises(ValueError):
        connector.execute_raw("")
