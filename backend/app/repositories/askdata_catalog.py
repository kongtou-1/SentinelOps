"""Read-only catalog and semantic projections for one model version."""

from sqlalchemy import func, or_, select

from app.models.askdata import ONTOLOGY_TABLES


def _table(name):
    return ONTOLOGY_TABLES[name]


def _active(table):
    return table.c.deleted_at.is_(None)


def summary(db, version_id):
    table = _table("catalog_table")
    schema = _table("catalog_schema")
    column = _table("catalog_column")
    term, table_term = _table("term"), _table("table__term")
    attribute = _table("column_attribute")
    fk, semantic = _table("column__foreign_key"), _table("column__semantic_fk")

    def count(statement):
        return int(db.scalar(statement) or 0)

    counts = {
        "tables": count(
            select(func.count()).select_from(table).where(
                table.c.model_version_id == version_id, _active(table)
            )
        ),
        "columns": count(
            select(func.count()).select_from(column).where(
                column.c.model_version_id == version_id, _active(column)
            )
        ),
        "terms": count(
            select(func.count()).select_from(term).where(
                term.c.model_version_id == version_id, _active(term)
            )
        ),
        "attributes": count(
            select(func.count()).select_from(attribute).where(
                attribute.c.model_version_id == version_id, _active(attribute)
            )
        ),
        "relationships": count(
            select(func.count()).select_from(fk).where(
                fk.c.model_version_id == version_id, _active(fk)
            )
        )
        + count(
            select(func.count()).select_from(semantic).where(
                semantic.c.model_version_id == version_id, _active(semantic)
            )
        ),
    }
    schema_rows = db.execute(
        select(
            schema.c.name,
            func.coalesce(schema.c.description, ""),
            func.count(func.distinct(table.c.id)),
            func.count(func.distinct(table_term.c.term_id)),
        )
        .select_from(
            schema.outerjoin(
                table,
                (table.c.schema_id == schema.c.id)
                & (table.c.model_version_id == version_id)
                & _active(table),
            )
            .outerjoin(
                table_term,
                (table_term.c.table_id == table.c.id)
                & (table_term.c.model_version_id == version_id)
                & _active(table_term),
            )
        )
        .where(schema.c.model_version_id == version_id, _active(schema))
        .group_by(schema.c.id, schema.c.name, schema.c.description)
        .order_by(schema.c.name)
        .execution_options(access_internal=True)
    ).all()
    return {
        **counts,
        "schemas": [
            {"name": name, "description": description, "table_count": tables, "term_count": terms}
            for name, description, tables, terms in schema_rows
        ],
    }


def tables(db, version_id, search="", schema_name=None, offset=0, limit=50):
    table, schema = _table("catalog_table"), _table("catalog_schema")
    column, table_term = _table("catalog_column"), _table("table__term")
    fk, semantic, attribute = (
        _table("column__foreign_key"),
        _table("column__semantic_fk"),
        _table("column_attribute"),
    )
    conditions = [table.c.model_version_id == version_id, _active(table), _active(schema)]
    if schema_name:
        conditions.append(schema.c.name == schema_name)
    if search:
        pattern = f"%{search.strip()}%"
        conditions.append(
            or_(
                table.c.name.ilike(pattern),
                func.coalesce(table.c.description, "").ilike(pattern),
            )
        )
    base = (
        select(
            table.c.id,
            table.c.name,
            table.c.description,
            table.c.table_type,
            table.c.is_junction_table,
            schema.c.name,
        )
        .join(
            schema,
            (schema.c.id == table.c.schema_id)
            & (schema.c.model_version_id == version_id),
        )
        .where(*conditions)
    )
    total = int(
        db.scalar(
            select(func.count())
            .select_from(base.subquery())
            .execution_options(access_internal=True)
        )
        or 0
    )
    rows = db.execute(
        base.order_by(schema.c.name, table.c.name, table.c.id)
        .offset(offset)
        .limit(limit)
        .execution_options(access_internal=True)
    ).all()
    identifiers = [row[0] for row in rows]
    columns = _counts_by(db, column, column.c.table_id, identifiers, version_id)
    terms = _counts_by(db, table_term, table_term.c.table_id, identifiers, version_id)
    declared = _relationship_counts(db, fk, identifiers, version_id)
    inferred = _semantic_relationship_counts(db, semantic, attribute, identifiers, version_id)
    return {
        "items": [
            {
                "id": row[0],
                "schema_name": row[5],
                "name": row[1],
                "description": row[2] or "",
                "table_type": row[3] or "",
                "is_junction_table": bool(row[4]),
                "column_count": columns.get(row[0], 0),
                "term_count": terms.get(row[0], 0),
                "relationship_count": declared.get(row[0], 0) + inferred.get(row[0], 0),
            }
            for row in rows
        ],
        "total": total,
    }


def _counts_by(db, table, column, identifiers, version_id):
    if not identifiers:
        return {}
    rows = db.execute(
        select(column, func.count())
        .where(
            table.c.model_version_id == version_id,
            _active(table),
            column.in_(identifiers),
        )
        .group_by(column)
        .execution_options(access_internal=True)
    ).all()
    return {key: int(value) for key, value in rows}


def _relationship_counts(db, fk, identifiers, version_id):
    column = _table("catalog_column")
    if not identifiers:
        return {}
    rows = db.execute(
        select(column.c.table_id, func.count())
        .select_from(
            fk.join(
                column,
                (column.c.id == fk.c.source_column_id)
                & (column.c.model_version_id == version_id),
            )
        )
        .where(
            fk.c.model_version_id == version_id,
            _active(fk),
            column.c.table_id.in_(identifiers),
        )
        .group_by(column.c.table_id)
        .execution_options(access_internal=True)
    ).all()
    return {key: int(value) for key, value in rows}


def _semantic_relationship_counts(db, semantic, attribute, identifiers, version_id):
    column = _table("catalog_column")
    if not identifiers:
        return {}
    rows = db.execute(
        select(column.c.table_id, func.count())
        .select_from(
            semantic.join(
                column,
                (column.c.id == semantic.c.column_id)
                & (column.c.model_version_id == version_id),
            )
        )
        .where(
            semantic.c.model_version_id == version_id,
            _active(semantic),
            column.c.table_id.in_(identifiers),
        )
        .group_by(column.c.table_id)
        .execution_options(access_internal=True)
    ).all()
    return {key: int(value) for key, value in rows}


def table(db, version_id, table_id):
    catalog_table, schema = _table("catalog_table"), _table("catalog_schema")
    column, attribute = _table("catalog_column"), _table("column_attribute")
    table_term, term = _table("table__term"), _table("term")
    row = db.execute(
        select(
            catalog_table.c.id,
            catalog_table.c.name,
            catalog_table.c.description,
            catalog_table.c.table_type,
            catalog_table.c.is_junction_table,
            catalog_table.c.pk,
            schema.c.name,
        )
        .join(
            schema,
            (schema.c.id == catalog_table.c.schema_id)
            & (schema.c.model_version_id == version_id),
        )
        .where(
            catalog_table.c.model_version_id == version_id,
            catalog_table.c.id == table_id,
            _active(catalog_table),
            _active(schema),
        )
        .execution_options(access_internal=True)
    ).first()
    if row is None:
        return None
    columns = db.execute(
        select(
            column.c.id,
            column.c.name,
            column.c.data_type,
            column.c.is_nullable,
            column.c.description,
            column.c.ordinal_position,
            column.c.is_unique,
            column.c.sample_values,
        )
        .where(
            column.c.model_version_id == version_id,
            column.c.table_id == table_id,
            _active(column),
        )
        .order_by(column.c.ordinal_position, column.c.name)
        .execution_options(access_internal=True)
    ).all()
    attributes = db.execute(
        select(attribute.c.source_column, attribute.c.name, attribute.c.term_name)
        .where(
            attribute.c.model_version_id == version_id,
            attribute.c.table_id == table_id,
            _active(attribute),
        )
        .order_by(attribute.c.name)
        .execution_options(access_internal=True)
    ).all()
    by_column = {}
    for source_column, name, term_name in attributes:
        by_column.setdefault(source_column, []).append((name, term_name))
    terms = db.execute(
        select(term.c.id, term.c.name, term.c.description)
        .select_from(
            table_term.join(
                term,
                (term.c.id == table_term.c.term_id) & (term.c.model_version_id == version_id),
            )
        )
        .where(
            table_term.c.model_version_id == version_id,
            table_term.c.table_id == table_id,
            _active(table_term),
            _active(term),
        )
        .order_by(term.c.name)
        .execution_options(access_internal=True)
    ).all()
    primary_key = set(row[5] or [])
    return {
        "id": row[0],
        "schema_name": row[6],
        "name": row[1],
        "description": row[2] or "",
        "table_type": row[3] or "",
        "is_junction_table": bool(row[4]),
        "columns": [
            {
                "id": item[0],
                "name": item[1],
                "data_type": item[2] or "",
                "nullable": item[3] is not False,
                "description": item[4] or "",
                "ordinal_position": item[5],
                "is_unique": item[6],
                "sample_values": item[7],
                "primary_key": item[1] in primary_key,
                "term_name": by_column[item[1]][0][1] if item[1] in by_column else None,
                "attribute_name": by_column[item[1]][0][0] if item[1] in by_column else None,
            }
            for item in columns
        ],
        "terms": [
            {"id": item[0], "name": item[1], "description": item[2] or ""} for item in terms
        ],
        "foreign_keys": foreign_keys(db, version_id, table_id),
    }


def foreign_keys(db, version_id, table_id):
    column, table, schema = (
        _table("catalog_column"),
        _table("catalog_table"),
        _table("catalog_schema"),
    )
    fk, semantic, attribute = (
        _table("column__foreign_key"),
        _table("column__semantic_fk"),
        _table("column_attribute"),
    )
    source_column = column.alias("source_column")
    target_column = column.alias("target_column")
    target_table = table.alias("target_table")
    target_schema = schema.alias("target_schema")
    declared = db.execute(
        select(
            source_column.c.name,
            target_schema.c.name,
            target_table.c.name,
            target_column.c.name,
        )
        .select_from(
            fk.join(
                source_column,
                (source_column.c.id == fk.c.source_column_id)
                & (source_column.c.model_version_id == version_id),
            )
            .join(
                target_column,
                (target_column.c.id == fk.c.target_column_id)
                & (target_column.c.model_version_id == version_id),
            )
            .join(
                target_table,
                (target_table.c.id == target_column.c.table_id)
                & (target_table.c.model_version_id == version_id),
            )
            .join(
                target_schema,
                (target_schema.c.id == target_table.c.schema_id)
                & (target_schema.c.model_version_id == version_id),
            )
        )
        .where(
            fk.c.model_version_id == version_id,
            _active(fk),
            source_column.c.table_id == table_id,
        )
        .order_by(source_column.c.name, target_schema.c.name, target_table.c.name)
        .execution_options(access_internal=True)
    ).all()
    inferred = db.execute(
        select(
            source_column.c.name,
            target_schema.c.name,
            target_table.c.name,
            attribute.c.source_column,
        )
        .select_from(
            semantic.join(
                source_column,
                (source_column.c.id == semantic.c.column_id)
                & (source_column.c.model_version_id == version_id),
            )
            .join(
                attribute,
                (attribute.c.id == semantic.c.attribute_id)
                & (attribute.c.model_version_id == version_id),
            )
            .join(
                target_table,
                (target_table.c.id == attribute.c.table_id)
                & (target_table.c.model_version_id == version_id),
            )
            .join(
                target_schema,
                (target_schema.c.id == target_table.c.schema_id)
                & (target_schema.c.model_version_id == version_id),
            )
        )
        .where(
            semantic.c.model_version_id == version_id,
            _active(semantic),
            source_column.c.table_id == table_id,
        )
        .order_by(source_column.c.name, target_schema.c.name, target_table.c.name)
        .execution_options(access_internal=True)
    ).all()
    return [
        {
            "columns": [source_name],
            "referenced_schema": target_schema_name,
            "referenced_table": target_table_name,
            "referenced_columns": [target_name],
            "kind": kind,
        }
        for kind, rows in (("declared", declared), ("semantic", inferred))
        for source_name, target_schema_name, target_table_name, target_name in rows
    ]


def terms(db, version_id, search="", offset=0, limit=50):
    term, table_term, table, schema = (
        _table("term"),
        _table("table__term"),
        _table("catalog_table"),
        _table("catalog_schema"),
    )
    attribute_term = _table("column_attribute__term")
    conditions = [term.c.model_version_id == version_id, _active(term)]
    if search:
        pattern = f"%{search.strip()}%"
        conditions.append(
            or_(
                term.c.name.ilike(pattern),
                func.coalesce(term.c.description, "").ilike(pattern),
            )
        )
    total = int(
        db.scalar(
            select(func.count())
            .select_from(term)
            .where(*conditions)
            .execution_options(access_internal=True)
        )
        or 0
    )
    rows = db.execute(
        select(term.c.id, term.c.name, term.c.description, term.c.synonyms)
        .where(*conditions)
        .order_by(term.c.name, term.c.id)
        .offset(offset)
        .limit(limit)
        .execution_options(access_internal=True)
    ).all()
    identifiers = [row[0] for row in rows]
    attributes = _counts_by(db, attribute_term, attribute_term.c.term_id, identifiers, version_id)
    names = {}
    if identifiers:
        linked = db.execute(
            select(table_term.c.term_id, schema.c.name, table.c.name)
            .select_from(
                table_term.join(
                    table,
                    (table.c.id == table_term.c.table_id)
                    & (table.c.model_version_id == version_id),
                ).join(
                    schema,
                    (schema.c.id == table.c.schema_id) & (schema.c.model_version_id == version_id),
                )
            )
            .where(
                table_term.c.model_version_id == version_id,
                table_term.c.term_id.in_(identifiers),
                _active(table_term),
                _active(table),
                _active(schema),
            )
            .order_by(schema.c.name, table.c.name)
            .execution_options(access_internal=True)
        ).all()
        for term_id, schema_name, table_name in linked:
            names.setdefault(term_id, []).append(f"{schema_name}.{table_name}")
    return {
        "items": [
            {
                "id": row[0],
                "name": row[1],
                "description": row[2] or "",
                "synonyms": list(row[3] or []),
                "attribute_count": attributes.get(row[0], 0),
                "table_names": names.get(row[0], []),
            }
            for row in rows
        ],
        "total": total,
    }


def term(db, version_id, term_id):
    term, table_term, table = (
        _table("term"),
        _table("table__term"),
        _table("catalog_table"),
    )
    attribute, attribute_term = _table("column_attribute"), _table("column_attribute__term")
    row = db.execute(
        select(term.c.id, term.c.name, term.c.description, term.c.synonyms)
        .where(
            term.c.model_version_id == version_id,
            term.c.id == term_id,
            _active(term),
        )
        .execution_options(access_internal=True)
    ).first()
    if row is None:
        return None
    attributes = db.execute(
        select(
            attribute.c.id,
            attribute.c.name,
            attribute.c.description,
            attribute.c.datatype,
            attribute.c.source_column,
            attribute.c.table_id,
            attribute.c.certified,
            table.c.name,
        )
        .select_from(
            attribute_term.join(
                attribute,
                (attribute.c.id == attribute_term.c.attribute_id)
                & (attribute.c.model_version_id == version_id),
            ).join(
                table,
                (table.c.id == attribute.c.table_id)
                & (table.c.model_version_id == version_id),
            )
        )
        .where(
            attribute_term.c.model_version_id == version_id,
            attribute_term.c.term_id == term_id,
            _active(attribute_term),
            _active(attribute),
            _active(table),
        )
        .order_by(table.c.name, attribute.c.source_column)
        .execution_options(access_internal=True)
    ).all()
    tables = db.execute(
        select(table.c.id, table.c.name, table.c.description)
        .select_from(
            table_term.join(
                table,
                (table.c.id == table_term.c.table_id)
                & (table.c.model_version_id == version_id),
            )
        )
        .where(
            table_term.c.model_version_id == version_id,
            table_term.c.term_id == term_id,
            _active(table_term),
            _active(table),
        )
        .order_by(table.c.name)
        .execution_options(access_internal=True)
    ).all()
    return {
        "id": row[0],
        "name": row[1],
        "description": row[2] or "",
        "synonyms": list(row[3] or []),
        "attributes": [
            {
                "id": item[0],
                "name": item[1],
                "description": item[2] or "",
                "datatype": item[3] or "",
                "source_column": item[4],
                "table_id": item[5],
                "table_name": item[7],
                "certified": bool(item[6]),
            }
            for item in attributes
        ],
        "tables": [
            {"id": item[0], "name": item[1], "description": item[2] or ""} for item in tables
        ],
    }


def relationships(db, version_id, limit=500):
    column, table, schema = (
        _table("catalog_column"),
        _table("catalog_table"),
        _table("catalog_schema"),
    )
    fk, semantic, attribute = (
        _table("column__foreign_key"),
        _table("column__semantic_fk"),
        _table("column_attribute"),
    )
    source_table = table.alias("source_table")
    source_schema = schema.alias("source_schema")
    target_table = table.alias("target_table")
    target_schema = schema.alias("target_schema")
    source_column = column.alias("source_column")
    target_column = column.alias("target_column")

    def joins(statement, source_id, target_id):
        return (
            statement.join(
                source_column,
                (source_column.c.id == source_id)
                & (source_column.c.model_version_id == version_id),
            )
            .join(
                source_table,
                (source_table.c.id == source_column.c.table_id)
                & (source_table.c.model_version_id == version_id),
            )
            .join(
                source_schema,
                (source_schema.c.id == source_table.c.schema_id)
                & (source_schema.c.model_version_id == version_id),
            )
            .join(
                target_column,
                (target_column.c.id == target_id)
                & (target_column.c.model_version_id == version_id),
            )
            .join(
                target_table,
                (target_table.c.id == target_column.c.table_id)
                & (target_table.c.model_version_id == version_id),
            )
            .join(
                target_schema,
                (target_schema.c.id == target_table.c.schema_id)
                & (target_schema.c.model_version_id == version_id),
            )
        )

    declared_count = int(
        db.scalar(
            select(func.count())
            .select_from(fk)
            .where(fk.c.model_version_id == version_id, _active(fk))
            .execution_options(access_internal=True)
        )
        or 0
    )
    semantic_count = int(
        db.scalar(
            select(func.count())
            .select_from(semantic)
            .where(semantic.c.model_version_id == version_id, _active(semantic))
            .execution_options(access_internal=True)
        )
        or 0
    )
    declared = db.execute(
        joins(
            select(
                source_schema.c.name,
                source_table.c.name,
                source_column.c.name,
                target_schema.c.name,
                target_table.c.name,
                target_column.c.name,
            ).select_from(fk),
            fk.c.source_column_id,
            fk.c.target_column_id,
        )
        .where(fk.c.model_version_id == version_id, _active(fk))
        .order_by(source_schema.c.name, source_table.c.name, source_column.c.name)
        .limit(limit)
        .execution_options(access_internal=True)
    ).all()
    inferred = db.execute(
        select(
            source_schema.c.name,
            source_table.c.name,
            source_column.c.name,
            target_schema.c.name,
            target_table.c.name,
            attribute.c.source_column,
        )
        .select_from(
            semantic.join(
                source_column,
                (source_column.c.id == semantic.c.column_id)
                & (source_column.c.model_version_id == version_id),
            )
            .join(
                source_table,
                (source_table.c.id == source_column.c.table_id)
                & (source_table.c.model_version_id == version_id),
            )
            .join(
                source_schema,
                (source_schema.c.id == source_table.c.schema_id)
                & (source_schema.c.model_version_id == version_id),
            )
            .join(
                attribute,
                (attribute.c.id == semantic.c.attribute_id)
                & (attribute.c.model_version_id == version_id),
            )
            .join(
                target_table,
                (target_table.c.id == attribute.c.table_id)
                & (target_table.c.model_version_id == version_id),
            )
            .join(
                target_schema,
                (target_schema.c.id == target_table.c.schema_id)
                & (target_schema.c.model_version_id == version_id),
            )
        )
        .where(semantic.c.model_version_id == version_id, _active(semantic))
        .order_by(source_schema.c.name, source_table.c.name, source_column.c.name)
        .limit(limit)
        .execution_options(access_internal=True)
    ).all()
    items = [
        {
            "source_schema": row[0],
            "source_table": row[1],
            "source_column": row[2],
            "target_schema": row[3],
            "target_table": row[4],
            "target_column": row[5],
            "kind": "declared",
        }
        for row in declared
    ] + [
        {
            "source_schema": row[0],
            "source_table": row[1],
            "source_column": row[2],
            "target_schema": row[3],
            "target_table": row[4],
            "target_column": row[5],
            "kind": "semantic",
        }
        for row in inferred
    ]
    return {
        "items": items,
        "total": declared_count + semantic_count,
        "declared": declared_count,
        "semantic": semantic_count,
    }


def counts(db, version_id):
    table, column = _table("catalog_table"), _table("catalog_column")
    term, attribute = _table("term"), _table("column_attribute")
    fk, semantic = _table("column__foreign_key"), _table("column__semantic_fk")

    def count(table_):
        return int(
            db.scalar(
                select(func.count())
                .select_from(table_)
                .where(table_.c.model_version_id == version_id, _active(table_))
                .execution_options(access_internal=True)
            )
            or 0
        )

    return {
        "tables": count(table),
        "columns": count(column),
        "terms": count(term),
        "attributes": count(attribute),
        "relationships": count(fk) + count(semantic),
    }
