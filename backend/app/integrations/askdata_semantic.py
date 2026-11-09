"""Platform bindings for upstream semantic compilation.

The upstream library owns taxonomy and relationship prompts; this module supplies
the platform's embedding model, version-scoped pgvector rows, and thread-local
database sessions. It never creates schema or changes a version's scope.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Lock, get_ident
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.db.session import SessionLocal
from app.models import AskDataEmbedding
from app.repositories.ontology import OntologyStore

SEMANTIC_COLLECTION = "semantic_layer"
DATA_COLLECTION = "data_objects_layer"
_EMBEDDINGS = {"semantic_layer": "semantic", "data_objects_layer": "data"}
_VECTOR_SIZE = 512
_encoder_lock = Lock()


class _BuildOntologyStore(OntologyStore):
    """Build-only autocommit adapter; repositories never own transactions."""

    def __init__(self, db, version_id, guard, commit):
        super().__init__(db, version_id, guard)
        self.commit = commit

    def query(self, statement, parameters=None, **kwargs):
        try:
            rows = super().query(statement, parameters, **kwargs)
        except Exception:
            self.commit(failed=True)
            raise
        # The repository has materialized rows and rechecked authorization.
        # End reads too: a Session returns its connection to the shared pool.
        self.commit()
        return rows


class BuildStorage:
    """One database session per upstream worker thread.

    ``OntologyStore`` rejects a session shared across threads. Compilation fans
    out to a small pool, so each thread gets its own connection and transaction;
    the build is isolated by version, and a failed build is never published.
    """

    def __init__(self, version_id, guard):
        self.version_id, self.guard = version_id, guard
        self._sessions: dict[int, Any] = {}
        self._stores: dict[int, OntologyStore] = {}
        self._depths: dict[int, int] = {}
        self._lock = Lock()

    def _connection(self):
        identifier = get_ident()
        with self._lock:
            connection = self._sessions.get(identifier)
            if connection is None:
                connection = self._sessions[identifier] = SessionLocal()
            return connection

    def store(self):
        identifier = get_ident()
        connection = self._connection()
        with self._lock:
            store = self._stores.get(identifier)
            if store is None:
                store = self._stores[identifier] = _BuildOntologyStore(
                    connection, self.version_id, self.guard, self._committer(connection, identifier)
                )
            return store

    def _committer(self, connection, identifier):
        """Commit each write unless the caller opened an explicit transaction.

        Catalog ingest walks one schema on the calling thread and its columns on
        a worker pool; without a commit the worker cannot see the table rows and
        the column insert dies on the composite foreign key. Compilation, which
        does want all-or-nothing, wraps itself in :meth:`transaction`, and there
        the depth is above zero and this stays silent.
        """

        def commit(*, failed=False):
            if self._depths.get(identifier, 0) == 0 and connection.in_transaction():
                if failed:
                    connection.rollback()
                else:
                    connection.commit()

        return commit

    @contextmanager
    def transaction(self):
        connection = self._connection()
        identifier = get_ident()
        self.guard(True)
        depth = self._depths.get(identifier, 0)
        self._depths[identifier] = depth + 1
        try:
            if depth == 0:
                if not connection.in_transaction():
                    connection.begin()
                yield
                connection.commit()
            else:
                with connection.begin_nested():
                    yield
        except Exception:
            if depth == 0:
                connection.rollback()
            raise
        finally:
            self._depths[identifier] = depth

    def verify(self):
        self.guard(False)

    def close(self, *, success):
        with self._lock:
            connections = list(self._sessions.values())
            self._sessions.clear()
            self._stores.clear()
        for connection in connections:
            try:
                if success:
                    if connection.in_transaction():
                        connection.commit()
                elif connection.in_transaction():
                    connection.rollback()
            finally:
                connection.close()


class PlatformVectorStore:
    """Version-scoped pgvector collection over the platform's own table."""

    metadata_filter_format = "dict"

    def __init__(
        self,
        version_id,
        collection_name=SEMANTIC_COLLECTION,
        database_name=None,
        reset=False,
    ):
        self.version_id = version_id
        self.collection_name = collection_name
        self.collection = _EMBEDDINGS.get(collection_name, "semantic")
        self.database_name = database_name
        if reset:
            self.delete_all()

    def create_index(self):
        # The table and its HNSW index are platform migrations, never request DDL.
        return self.collection_name

    def run(self, records):
        rows = [row for group in records for row in group]
        if not rows:
            return 0
        written = 0
        with SessionLocal() as db:
            for record in rows:
                metadata = dict(record.get("metadata") or {})
                embedding = metadata.pop("embedding", None)
                if not embedding:
                    continue
                path = str(metadata.get("source_path") or record.get("path") or "")
                content = str(record.get("text") or metadata.get("content") or "")
                if not path or not content:
                    continue
                statement = insert(AskDataEmbedding).values(
                    model_version_id=self.version_id,
                    collection=self.collection,
                    path=path,
                    content=content,
                    meta=_jsonable(metadata),
                    embedding=embedding,
                )
                statement = statement.on_conflict_do_update(
                    index_elements=[
                        AskDataEmbedding.model_version_id,
                        AskDataEmbedding.collection,
                        AskDataEmbedding.path,
                    ],
                    set_={
                        "content": content,
                        "meta": _jsonable(metadata),
                        "embedding": embedding,
                        "updated_at": func.now(),
                        "deleted_at": None,
                    },
                )
                db.execute(statement.execution_options(access_internal=True))
                written += 1
            db.commit()
        return written

    write_to_index = run

    def retrieval(self, vectors, top_k=10, where=None):
        if not vectors:
            return []
        conditions = self._conditions(where or {})
        results = []
        with SessionLocal() as db:
            for vector in vectors:
                distance = AskDataEmbedding.embedding.cosine_distance(vector)
                rows = db.execute(
                    select(
                        AskDataEmbedding.content,
                        AskDataEmbedding.meta,
                        distance.label("distance"),
                    )
                    .where(
                        AskDataEmbedding.model_version_id == self.version_id,
                        AskDataEmbedding.collection == self.collection,
                        AskDataEmbedding.deleted_at.is_(None),
                        *conditions,
                    )
                    .order_by(distance, AskDataEmbedding.id)
                    .limit(top_k)
                    .execution_options(access_internal=True)
                ).all()
                results.append(
                    [
                        {
                            "text": content,
                            "metadata": dict(metadata or {}),
                            "_distance": float(score),
                        }
                        for content, metadata, score in rows
                    ]
                )
        return results

    def _conditions(self, where):
        conditions = []
        for key, value in where.items():
            if value is None:
                continue
            column = AskDataEmbedding.meta[key].astext
            if isinstance(value, bool):
                conditions.append(column == ("true" if value else "false"))
            elif isinstance(value, (list, tuple, set)):
                conditions.append(column.in_([str(item) for item in value]))
            else:
                conditions.append(column == str(value))
        return conditions

    def delete_by_database(self, database_name):
        if not database_name:
            return []
        with SessionLocal() as db:
            rows = db.scalars(
                select(AskDataEmbedding).where(
                    AskDataEmbedding.model_version_id == self.version_id,
                    AskDataEmbedding.collection == self.collection,
                    AskDataEmbedding.meta["database_name"].astext == str(database_name),
                    AskDataEmbedding.deleted_at.is_(None),
                )
            ).all()
            identifiers = [str(row.id) for row in rows]
            if rows:
                db.execute(
                    update(AskDataEmbedding)
                    .where(AskDataEmbedding.id.in_([row.id for row in rows]))
                    .values(deleted_at=datetime.now(UTC), updated_at=datetime.now(UTC))
                    .execution_options(access_internal=True)
                )
                db.commit()
            return identifiers

    def delete_by_id(self, node_id):
        with SessionLocal() as db:
            row = db.scalar(
                select(AskDataEmbedding).where(
                    AskDataEmbedding.model_version_id == self.version_id,
                    AskDataEmbedding.collection == self.collection,
                    AskDataEmbedding.meta["id"].astext == str(node_id),
                    AskDataEmbedding.deleted_at.is_(None),
                )
            )
            if row is None:
                return 0
            row.deleted_at = row.updated_at = datetime.now(UTC)
            db.commit()
            return 1

    def delete_all(self):
        with SessionLocal() as db:
            result = db.execute(
                update(AskDataEmbedding)
                .where(
                    AskDataEmbedding.model_version_id == self.version_id,
                    AskDataEmbedding.collection == self.collection,
                    AskDataEmbedding.deleted_at.is_(None),
                )
                .values(deleted_at=datetime.now(UTC), updated_at=datetime.now(UTC))
                .execution_options(access_internal=True)
            )
            db.commit()
            return result.rowcount or 0

    def close(self):
        return None


class PlatformSemanticEmbedder:
    """Adapter matching ``SemanticEmbedder.embed_term`` without an OpenAI endpoint."""

    def __init__(self, version_id, database_name):
        self.vdb = PlatformVectorStore(version_id, SEMANTIC_COLLECTION, database_name)
        self.database_name = database_name

    def embed_term(self, term, attrs):
        from auto_ontology.semantic.embed import _build_rows
        from auto_ontology.utils.embedding import EmbedParams, embed_rows_into_vdb

        rows = _build_rows(self.database_name, term, attrs)
        return embed_rows_into_vdb(
            rows,
            EmbedParams(model_name="platform-local", embed_invoke_url="platform://embedding"),
            self.vdb,
            what="Term",
        )


def vector_factory(*, database_name=None, collection_name=SEMANTIC_COLLECTION, reset=False):
    version_id = _required_version()
    return PlatformVectorStore(version_id, collection_name, database_name, reset)


def embedding_factory(texts, input_type="passage", params=None, timeout=None, retries=None):
    from app.services.knowledge.index import encoder

    requested = [str(text or "") for text in texts]
    positions = [index for index, text in enumerate(requested) if text.strip()]
    if not positions:
        return [None] * len(requested)
    with _encoder_lock:
        model = encoder()
        batch = [requested[index] for index in positions]
        generated = model.query_embed(batch) if input_type == "query" else model.embed(batch)
        vectors = [item.tolist() for item in generated]
    result: list[list[float] | None] = [None] * len(requested)
    for index, vector in zip(positions, vectors, strict=True):
        if len(vector) != _VECTOR_SIZE:
            raise ValueError("embedding_dimension_mismatch")
        result[index] = vector
    return result


def settings_reader(key):
    # Semantic builds use bounded row samples only; PII classification and
    # distinct-value scans are separate features and stay off in this stage.
    return {
        "distinct_value_probing_enabled": "false",
        "pii_detection_enabled": "false",
        "sql_query_timeout_seconds": "30",
    }.get(key)


def _required_version():
    from auto_ontology.host import require

    return require().model_version_id


def _jsonable(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))
