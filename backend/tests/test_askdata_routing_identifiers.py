"""Logical connection IDs are quoted before validation, without widening access."""

from uuid import UUID, uuid4

import pytest
import sqlglot
from pydantic import BaseModel

from app.integrations.askdata_database import PlatformDatabase
from app.integrations.askdata_sql import AskDataSQLRejected, checked_askdata_query

IDENTIFIER = UUID("d60d396f-8797-4eec-9209-23b711b8cb1f")


class GeneratedSQL(BaseModel):
    sql_code: str


def connector(engine="postgresql"):
    return PlatformDatabase(IDENTIFIER, lambda: {"engine": engine, "tables": {}}, None)


@pytest.mark.parametrize("engine,quote", [("postgresql", '"'), ("mysql", "`")])
def test_bare_routing_id_is_quoted_in_cte_and_join(engine, quote):
    db = connector(engine)
    sql = (
        f"WITH x AS (SELECT id FROM {IDENTIFIER}.public.orders) "
        f"SELECT x.id FROM x JOIN {IDENTIFIER} .public.orders o ON x.id=o.id"
    )
    normalized = db.normalize_sql(sql)
    assert normalized.count(f"{quote}{IDENTIFIER}{quote}") == 2
    assert db.normalize_sql(normalized) == normalized
    tree = sqlglot.parse_one(normalized, read=db.dialect)
    assert sum(t.catalog == str(IDENTIFIER) for t in tree.find_all(sqlglot.exp.Table)) == 2


def test_literals_comments_and_other_connection_ids_are_untouched():
    db = connector()
    other = uuid4()
    sql = (
        f"SELECT '{IDENTIFIER}.public.orders', $q${IDENTIFIER}.public.orders$q$ "
        f'FROM "{IDENTIFIER}".public.orders /* {IDENTIFIER}.public.orders */ '
        f"-- {IDENTIFIER}.public.orders\nWHERE id='{other}'"
    )
    assert db.normalize_sql(sql) == sql
    unknown = f'SELECT id FROM "{other}".public.orders'
    assert db._local_sql(unknown, {"engine": "postgresql"}) == unknown
    with pytest.raises(AskDataSQLRejected):
        checked_askdata_query(
            unknown,
            "postgresql",
            {"public.orders": ["id"]},
            {"public.orders": {"columns": [{"name": "id"}]}},
        )


def test_local_routing_removal_still_enforces_column_grants():
    db = connector()
    sql = db._local_sql(f"SELECT secret FROM {IDENTIFIER}.public.orders", {"engine": "postgresql"})
    assert str(IDENTIFIER) not in sql
    with pytest.raises(AskDataSQLRejected) as error:
        checked_askdata_query(
            sql,
            "postgresql",
            {"public.orders": ["id"]},
            {"public.orders": {"columns": [{"name": "id"}, {"name": "secret"}]}},
        )
    assert error.value.code == "column_not_allowed"


def test_catalog_validation_receives_normalized_sql(monkeypatch):
    from auto_ontology.retrieval.text_to_sql.agents import sql_parse_validation as validation

    monkeypatch.setattr(validation, "fetch_all_schema_ids", lambda: [])
    monkeypatch.setattr(validation, "get_schemas_by_ids", lambda ids: {})
    seen = []

    def parse(schemas, sql, dialects):
        seen.append(sql)
        sqlglot.parse_one(sql, read="postgres")
        return {"success": True}

    monkeypatch.setattr(validation.SQLValidationAgent, "_sql_parse_validation", staticmethod(parse))
    monkeypatch.setattr(
        validation.SQLValidationAgent,
        "_validate_intent",
        lambda self, state, path, *args: {"decision": "valid_sql", "path_state": path},
    )
    response = GeneratedSQL(sql_code=f"SELECT COUNT(*) FROM {IDENTIFIER}.public.orders")
    state = {"connectors": [connector()], "path_state": {"sql_generation_result": response}}
    result = validation.SQLValidationAgent().execute(state)
    assert result["decision"] == "valid_sql"
    assert f'"{IDENTIFIER}"' in seen[0]
    assert result["path_state"]["sql_code"] == seen[0]


def test_value_validation_normalizes_before_extracting_checks(monkeypatch):
    from auto_ontology.retrieval.text_to_sql.agents import sql_value_validation as validation

    seen = []
    monkeypatch.setattr(
        validation, "extract_value_checks", lambda sql, dialect: seen.append(sql) or []
    )
    sql = f"SELECT COUNT(*) FROM {IDENTIFIER}.public.orders"
    state = {
        "validate_sql_values": True,
        "connectors": [connector()],
        "path_state": {"sql_generation_result": GeneratedSQL(sql_code=sql)},
    }
    result = validation.SQLValueValidationAgent().execute(state)
    assert f'"{IDENTIFIER}"' in seen[0]
    assert result["path_state"]["sql_code"] == seen[0]
    assert result["path_state"]["sql_generation_result"].sql_code == seen[0]
