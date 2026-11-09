"""阶段 7：版本作用域下的聚合读取必须仍能按主键分组。

版本隔离把每张基表包成子查询，PostgreSQL 的「按主键分组、选取同表其它列」
规则只对基表成立。上游 `dal/terms.fetch_terms_and_attributes_for_table` 正是
这种写法，缺了分组扩展，读取 term 会直接 GroupingError，语义编译拿不到已落库
的 term，向量一条也写不进去。
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.models.askdata import ONTOLOGY_TABLES
from app.repositories.ontology import _expand_group_by

term = ONTOLOGY_TABLES["term"]
table_term = ONTOLOGY_TABLES["table__term"]
schema = ONTOLOGY_TABLES["catalog_schema"]


def _statement():
    return (
        select(
            term.c.name,
            term.c.description,
            term.c.id,
            func.array_agg(func.distinct(schema.c.name)).label("schema_names"),
        )
        .select_from(
            table_term.join(term, term.c.id == table_term.c.term_id).join(
                schema, schema.c.id == term.c.id
            )
        )
        .group_by(term.c.id)
    )


def test_group_by_gains_the_columns_of_the_grouped_table():
    expanded = _expand_group_by(_statement())
    grouped = {str(clause) for clause in expanded._group_by_clauses}
    assert "term.name" in grouped
    assert "term.description" in grouped
    assert "term.id" in grouped


def test_aggregate_and_foreign_table_columns_are_not_grouped():
    expanded = _expand_group_by(_statement())
    grouped = {str(clause) for clause in expanded._group_by_clauses}
    assert not any("catalog_schema" in clause for clause in grouped)


def test_statement_without_group_by_is_returned_untouched():
    statement = select(term.c.id).where(term.c.name == "Customer")
    assert _expand_group_by(statement) is statement


def test_grouping_is_idempotent():
    once = _expand_group_by(_statement())
    twice = _expand_group_by(once)
    assert {str(c) for c in once._group_by_clauses} == {str(c) for c in twice._group_by_clauses}
