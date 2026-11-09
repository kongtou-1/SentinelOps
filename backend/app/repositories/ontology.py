"""Version-scoped execution of upstream SQLAlchemy Core statements.

No text SQL, DDL, arbitrary table access, engine creation or transaction ownership.
"""

from datetime import UTC, datetime
from threading import get_ident

from auto_ontology.dal.schema import METADATA as UPSTREAM_METADATA
from sqlalchemy import Column, Delete, Insert, Select, Table, Update, and_, select, update
from sqlalchemy.sql import visitors
from sqlalchemy.sql.elements import ColumnClause, TextClause
from sqlalchemy.sql.selectable import CompoundSelect
from sqlalchemy.util import immutabledict

from app.models.askdata import ONTOLOGY_TABLES


def _insert_rows(statement):
    """Return the rows of a multi-row INSERT, or None for the single-row form.

    ``insert(t).values([{...}, {...}])`` keeps the rows in ``_multi_values``
    instead of ``_values``; the version column has to be injected into every row,
    so the two shapes are distinguished here rather than inferred at each use.
    """
    if statement.select is not None or not statement._multi_values:
        return None
    rows = []
    for block in statement._multi_values:
        if not isinstance(block, (list, tuple)) or not block:
            raise ValueError("ontology_batch_insert_requires_named_rows")
        if not all(isinstance(row, dict) for row in block):
            raise ValueError("ontology_batch_insert_requires_named_rows")
        rows.extend(block)
    return rows


def _expand_group_by(statement):
    """Keep Postgres' functional-dependency rule for aggregated reads.

    Version scoping replaces every base table with a derived table, and Postgres
    grants "GROUP BY the primary key, select the rest of that table" only to base
    tables — not to subqueries, where it asks for every selected column. Upstream
    writes exactly that shape (``dal/terms.fetch_terms_and_attributes_for_table``
    groups by ``term.id`` and selects its name, description and synonyms), so the
    columns of any table already named in GROUP BY are added to it. The primary
    key still determines them, so the grouping is unchanged.
    """
    grouped = [clause for clause in statement._group_by_clauses if isinstance(clause, Column)]
    tables = {clause.table for clause in grouped}
    if not tables:
        return statement
    named = {str(clause) for clause in grouped}
    extra = [
        column
        for column in statement.selected_columns
        if isinstance(column, Column) and column.table in tables and str(column) not in named
    ]
    return statement.group_by(*extra) if extra else statement


class OntologyStore:
    def __init__(self, db, version_id, guard):
        self.db, self.version_id, self.guard = db, version_id, guard
        self.thread_id = get_ident()

    def query(self, statement, parameters=None, **kwargs):
        if get_ident() != self.thread_id:
            raise ValueError("ontology_session_thread_mismatch")
        if not isinstance(statement, (Select, CompoundSelect, Insert, Update, Delete)):
            raise ValueError("ontology_structured_statement_required")
        nodes = list(visitors.iterate(statement))
        if any(
            isinstance(n, TextClause)
            or (
                isinstance(n, ColumnClause)
                and n.is_literal
                and n.name not in {"*", "NULL", "TRUE", "FALSE"}
            )
            for n in nodes
        ):
            raise ValueError("ontology_text_sql_not_allowed")
        for node in nodes:
            if isinstance(node, Table) and not (
                node.name in ONTOLOGY_TABLES
                and (
                    node is ONTOLOGY_TABLES[node.name]
                    or node is UPSTREAM_METADATA.tables.get(node.name)
                )
            ):
                raise ValueError("ontology_table_not_allowed")
        writing = not isinstance(statement, (Select, CompoundSelect))
        self.guard(writing)
        replacements = {}

        def replace(node, *, reading=False, cache=None):
            if writing and not reading and isinstance(node, Select):
                read_cache = {}
                return visitors.replacement_traverse(
                    node, {}, lambda part: replace(part, reading=True, cache=read_cache)
                )
            cache = replacements if cache is None else cache
            table = node.table if isinstance(node, Column) else node
            if isinstance(table, Table) and table.name in ONTOLOGY_TABLES:
                if table not in cache:
                    target = ONTOLOGY_TABLES[table.name]
                    if not writing or reading or table is not statement.table:
                        target = (
                            select(target)
                            .where(
                                target.c.model_version_id == self.version_id,
                                target.c.deleted_at.is_(None),
                            )
                            .subquery(table.name)
                        )
                    cache[table] = target
                target = cache[table]
                return target.c[node.name] if isinstance(node, Column) else target
            return None

        scoped = visitors.replacement_traverse(statement, {}, replace)
        # Core traversal clones value mappings as dicts; values() expects its
        # immutable mapping before adding the platform-owned version column.
        if isinstance(scoped, (Insert, Update)) and scoped._values is not None:
            scoped._values = immutabledict(scoped._values)
        if isinstance(scoped, Select) and scoped._group_by_clauses:
            scoped = _expand_group_by(scoped)
        batch = _insert_rows(scoped) if isinstance(scoped, Insert) else None
        values = parameters or {}
        if any(
            key.startswith(
                ("model_version_id", "created_at", "updated_at", "deleted_at", "_askdata_")
            )
            for key in values
        ):
            raise ValueError("ontology_lifecycle_fields_are_managed")
        if writing:
            target = ONTOLOGY_TABLES[statement.table.name]
            protected = {"model_version_id", "created_at", "updated_at", "deleted_at"}
            keys = {getattr(k, "name", k) for k in (getattr(statement, "_values", None) or {})}
            if batch:
                for row in batch:
                    keys |= {getattr(k, "name", k) for k in row}
            if protected.intersection(keys | set(values)) or any(
                k.startswith(
                    ("model_version_id", "created_at", "updated_at", "deleted_at", "_askdata_")
                )
                for k in values
            ):
                raise ValueError("ontology_lifecycle_fields_are_managed")
            now = datetime.now(UTC)
            if isinstance(statement, Insert):
                if statement.select is not None:
                    raise ValueError("ontology_single_insert_required")
                if batch:
                    # Upstream writes foreign keys as one INSERT for the whole
                    # set (catalog/store/schemas.add_fks). Each row carries the
                    # version, so the composite key stays intact.
                    scoped._multi_values = ()
                    scoped = scoped.values(
                        [{**row, "model_version_id": self.version_id} for row in batch]
                    )
                else:
                    scoped = scoped.values(model_version_id=self.version_id)
                conflict = getattr(scoped, "_post_values_clause", None)
                if conflict is not None:
                    conflict = conflict._clone()
                    if conflict.inferred_target_elements:
                        conflict.inferred_target_elements = [
                            target.c.model_version_id,
                            *conflict.inferred_target_elements,
                        ]
                    if hasattr(conflict, "update_whereclause"):
                        if protected.intersection(
                            getattr(k, "name", k) for k, _ in conflict.update_values_to_set
                        ):
                            raise ValueError("ontology_lifecycle_fields_are_managed")
                        conflict.update_whereclause = and_(
                            conflict.update_whereclause
                            if conflict.update_whereclause is not None
                            else True,
                            target.c.model_version_id == self.version_id,
                            target.c.deleted_at.is_(None),
                        )
                        conflict.update_values_to_set = [
                            *conflict.update_values_to_set,
                            ("updated_at", now),
                        ]
                    scoped._post_values_clause = conflict
            else:
                if isinstance(statement, Delete):
                    scoped = update(target).where(*scoped._where_criteria).values(deleted_at=now)
                    if statement._returning:
                        scoped = scoped.returning(
                            *(
                                replace(c) if isinstance(c, Column) else c
                                for c in statement._returning
                            )
                        )
                scoped = scoped.where(
                    target.c.model_version_id == self.version_id, target.c.deleted_at.is_(None)
                ).values(updated_at=now)
        result = self.db.execute(scoped.execution_options(access_internal=True), values)
        rows = [dict(row) for row in result.mappings()] if result.returns_rows else []
        self.guard(writing)
        return rows

    def query_read(self, statement, parameters=None):
        if not isinstance(statement, (Select, CompoundSelect)):
            raise ValueError("ontology_select_required")
        return self.query(statement, parameters)

    def query_write(self, statement, parameters=None):
        if not isinstance(statement, (Insert, Update, Delete)):
            raise ValueError("ontology_write_required")
        return self.query(statement, parameters)
