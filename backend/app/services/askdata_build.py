"""Background semantic build runner.

The API queues work; this module is the only place that ingests a live catalog
and compiles taxonomy. It holds the connection's advisory lock for the whole
build and binds one store session per worker thread.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models import AskDataEmbedding, AskDataModelVersion
from app.repositories import askdata as repository
from app.services import askdata as settings
from app.services import service_databases

logger = logging.getLogger(__name__)


class BuildFailure(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class BuildCancelled(BuildFailure):
    def __init__(self):
        super().__init__("build_cancelled")


def recover():
    with SessionLocal() as db:
        rows = repository.recover_interrupted(db, repository.stale_build_cutoff(0))
        db.commit()
        return len(rows)


def run_once():
    with SessionLocal() as db:
        build = repository.claim_build(db)
        if build is None:
            db.rollback()
            return False
        identifier = build.id
        db.commit()
    run(identifier)
    return True


def run(identifier):
    with SessionLocal() as db:
        build = repository.build(db, identifier)
        if build is None or build.status != "running":
            return
        version = db.get(AskDataModelVersion, build.model_version_id)
        connection = repository.connection(db, build.connection_id)
        if version is None or connection is None:
            _finish(identifier, "failed", "build_context_missing")
            return
        user_id, connection_id, version_id = build.requested_by, build.connection_id, version.id
        database_name = str(connection_id)
        db.commit()
    try:
        with service_databases.connection_lock(connection_id):
            counts = _execute(identifier, user_id, connection_id, version_id, database_name)
        # Reviewed definitions are re-applied only after the build transaction
        # has committed, so the new version's objects are visible.
        counts = {**counts, **_carry_over(connection_id, version_id)}
        _complete(identifier, version_id, counts)
    except BuildCancelled:
        _finish(identifier, "cancelled", "build_cancelled")
    except HTTPException as exc:
        code = {
            401: "authorization_revoked",
            403: "authorization_revoked",
            409: str(exc.detail) if exc.detail == "database_busy" else "configuration_changed",
        }.get(exc.status_code, "build_failed")
        _finish(identifier, "failed", code)
    except BuildFailure as exc:
        _finish(identifier, "failed", exc.code)
    except Exception:
        logger.exception("Ask-data build failed (build=%s)", identifier)
        _finish(identifier, "failed", "build_failed")


def _execute(identifier, user_id, connection_id, version_id, database_name):
    from app.integrations.askdata_model import model_factory
    from app.integrations.askdata_semantic import (
        BuildStorage,
        embedding_factory,
        settings_reader,
        vector_factory,
    )
    from app.integrations.ontology import task

    def guard(writing):
        with SessionLocal() as db:
            actor = repository.active_user(db, user_id)
            if actor is None:
                raise BuildFailure("authorization_revoked")
            try:
                settings.validate_version(
                    db,
                    actor,
                    connection_id,
                    version_id,
                    "askdata:manage" if writing else "askdata:read",
                )
            except HTTPException as exc:
                raise BuildFailure("authorization_revoked") from exc

    storage = BuildStorage(version_id, guard)
    connector = settings.database_connector(
        user_id, connection_id, version_id, hold_lock=True, gate="askdata"
    )

    def cancelled():
        return _cancelled(identifier)

    from auto_ontology.host import HostBindings

    from app.services import askdata_settings

    with SessionLocal() as db:
        task_model_config = askdata_settings.model_config(db, connection_id)
    bindings = HostBindings(
        connection_id=connection_id,
        model_version_id=version_id,
        user_id=user_id,
        model_factory=model_factory,
        store_factory=storage.store,
        transaction_factory=storage.transaction,
        vector_factory=vector_factory,
        connector_factory=lambda: [connector],
        embedding_factory=embedding_factory,
        settings_reader=settings_reader,
        cancelled=cancelled,
        model_config=task_model_config,
    )
    success = False
    try:
        with task(bindings):
            # Source rules are extracted before catalog ingestion so that the
            # upstream query-history writer (connector.get_queries) can consume
            # the statically resolved code SQL in the same build.
            source_counts = _source_rules(identifier, user_id, connection_id, version_id)
            _ingest(identifier, connector, storage)
            _compile(identifier, storage, database_name, version_id)
            _relationships(identifier, database_name)
            _metrics(identifier, database_name)
            storage.verify()
            counts = {**_counts(version_id), **source_counts}
            _require_content(counts, version_id)
            success = True
        return counts
    finally:
        storage.close(success=success)


def _carry_over(connection_id, version_id):
    from app.services import askdata_definitions as definitions

    try:
        result = definitions.carry_over(connection_id, version_id)
        return {
            "carried_definitions": result.get("carried", 0),
            "pending_reviews": result.get("review", 0),
        }
    except Exception:
        logger.exception("Ask-data definition carry-over failed (version=%s)", version_id)
        return {
            "carried_definitions": 0,
            "pending_reviews": 0,
            "definition_warning": "carry_over_failed",
        }


def _source_rules(identifier, user_id, connection_id, version_id):
    """Read only the pinned snapshot, extract rules, persist and index them."""
    from app.services import askdata_code_rules as code_rules

    _step(identifier, "extracting_source_rules")
    try:
        counts = code_rules.extract_for_build(identifier, user_id, connection_id, version_id)
    except HTTPException as exc:
        if exc.status_code in (401, 403):
            raise BuildFailure("authorization_revoked") from exc
        if exc.status_code == 404:
            raise BuildFailure("source_snapshot_unavailable") from exc
        if exc.status_code == 409 and exc.detail in (
            "source_snapshot_integrity_error",
            "source_snapshot_unavailable",
        ):
            raise BuildFailure(exc.detail) from exc
        raise
    _detail(
        identifier,
        {
            "source_rules": counts["source_rules"],
            "source_files": counts["source_files"],
            "code_embeddings": counts["code_embeddings"],
            "source_static_sql": counts["source_static_sql"],
            "source_dynamic": counts["source_dynamic"],
            "source_snapshot_origin": counts["source_snapshot_origin"],
            "source_gaps": counts["source_gaps"],
        },
    )
    return {
        key: counts[key]
        for key in (
            "source_rules",
            "source_files",
            "code_embeddings",
            "source_static_sql",
            "source_dynamic",
        )
    }


def _ingest(identifier, connector, storage):
    from auto_ontology.catalog import ingest_catalog

    _step(identifier, "ingesting_catalog")
    tables, columns = ingest_catalog(connector)
    _detail(
        identifier,
        {
            "tables": len(tables) if tables is not None else 0,
            "columns": len(columns) if columns is not None else 0,
        },
    )
    _index_catalog(identifier, connector.database_name, tables, columns)
    storage.verify()


def _index_catalog(identifier, database_name, tables, columns):
    """Embed the table and column docs the retriever searches.

    Upstream splits this across two layers: the catalog write stores rows, and
    the ingestion *service* then embeds them into the data-objects collection.
    The platform calls the catalog layer directly, so without this step that
    collection stays empty, ``get_relevant_tables`` finds nothing, and every
    question ends as "SQL can't be constructed from the data." The semantic
    collection is written separately during compilation.
    """
    from auto_ontology.utils import get_embed_params
    from auto_ontology.utils.embedding import batch_embed_chunks
    from auto_ontology.utils.embedding_rows import CatalogEmbeddingRowsOp
    from auto_ontology.vdb import get_data_vdb
    from auto_ontology.vdb.records import to_vdb_records

    written = 0
    if tables is not None and not tables.empty:
        rows = CatalogEmbeddingRowsOp(database_name=database_name)((tables, columns))
        vdb = get_data_vdb(database_name=database_name, reset=True)
        for chunk in batch_embed_chunks(rows, get_embed_params(), label=database_name):
            records = to_vdb_records(chunk)
            if records:
                written += vdb.run(records)
    _detail(identifier, {"data_embeddings": written})
    return written


def _compile(identifier, storage, database_name, version_id):
    from auto_ontology.semantic.pipeline import compile_semantic_layer

    from app.integrations.askdata_semantic import PlatformSemanticEmbedder

    _step(identifier, "compiling_semantics")
    embedder = PlatformSemanticEmbedder(version_id, database_name)
    compile_semantic_layer(database_name, embedder=embedder)
    storage.verify()


def _relationships(identifier, database_name):
    from auto_ontology.semantic.semantic_fk import resolve_semantic_fks

    _step(identifier, "resolving_relationships")
    resolve_semantic_fks(database_name)


def _metrics(identifier, database_name):
    from auto_ontology.semantic.sql_attribute_suggester import suggest_sql_attributes

    _step(identifier, "building_metrics")
    count = suggest_sql_attributes(database_name)
    _detail(identifier, {"metrics": int(count or 0)})


def _counts(version_id):
    from app.repositories import askdata_catalog

    with SessionLocal() as db:
        values = askdata_catalog.counts(db, version_id)
        for collection in ("data", "semantic"):
            values[f"{collection}_embeddings"] = int(
                db.scalar(
                    select(func.count())
                    .select_from(AskDataEmbedding)
                    .where(
                        AskDataEmbedding.model_version_id == version_id,
                        AskDataEmbedding.collection == collection,
                        AskDataEmbedding.deleted_at.is_(None),
                        AskDataEmbedding.meta["source"].astext.is_distinct_from("code_rule"),
                    )
                    .execution_options(access_internal=True)
                )
                or 0
            )
        values["embeddings"] = values["data_embeddings"] + values["semantic_embeddings"]
        return values


def _require_content(counts, version_id):
    if not counts["tables"]:
        raise BuildFailure("catalog_empty")
    if not counts["terms"]:
        raise BuildFailure("semantic_empty")
    if not counts["data_embeddings"]:
        raise BuildFailure("data_embedding_empty")
    if not counts["semantic_embeddings"]:
        raise BuildFailure("embedding_empty")


def _step(identifier, name):
    if _cancelled(identifier):
        raise BuildCancelled()
    with SessionLocal() as db:
        build = repository.build(db, identifier)
        if build is None or build.status != "running":
            raise BuildCancelled()
        build.step = name
        db.commit()


def _detail(identifier, values):
    with SessionLocal() as db:
        build = repository.build(db, identifier)
        if build is None:
            return
        build.detail = {**(build.detail or {}), **values}
        db.commit()


def _cancelled(identifier):
    with SessionLocal() as db:
        build = repository.build(db, identifier)
        return bool(build and (build.cancel_requested or build.status != "running"))


def _complete(identifier, version_id, counts):
    now = datetime.now(UTC)
    with SessionLocal() as db:
        build = repository.build(db, identifier)
        version = db.get(AskDataModelVersion, version_id)
        if build is None or version is None:
            return
        build.status, build.step, build.finished_at = "completed", "completed", now
        build.detail = {**(build.detail or {}), **counts}
        version.status = "ready"
        version.context = {**(version.context or {}), "counts": counts}
        db.commit()


def _finish(identifier, status, code):
    now = datetime.now(UTC)
    with SessionLocal() as db:
        build = repository.build(db, identifier)
        if build is None:
            return
        build.status, build.error_code, build.finished_at = status, code, now
        version = db.get(AskDataModelVersion, build.model_version_id)
        if version is not None and version.status not in ("ready", "retired"):
            version.status = "failed"
            version.context = {**(version.context or {}), "error_code": code}
        db.commit()
