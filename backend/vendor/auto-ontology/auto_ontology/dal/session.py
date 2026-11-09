# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Connection and transaction scope for the Postgres DAL.

Two entry points, used by every read and write in the DAL:

* :func:`store` returns the active transaction if there is one, otherwise a
  handle that runs each statement in its own autocommitted transaction — the
  same split the DAL relies on for single-statement writes.
* :func:`write_transaction` makes every statement in the block one unit of
  work. Nesting reuses the outermost scope, so a routine that calls another
  transactional routine still commits once.

**Pooling.** The DAL is called from three places with different concurrency
shapes: FastAPI request handlers, ingestion ``ThreadPoolExecutor`` threads
(``write_to_graph`` fans out), and a *spawned* chat-worker subprocess
(:mod:`auto_ontology.server.chat.worker`). SQLAlchemy's ``QueuePool`` is thread-safe, and
the ``ContextVar`` holding the active transaction is per-thread and per-task, so
two threads inside ``write_transaction`` get genuinely separate transactions
rather than trampling one connection.

The subprocess is the case worth naming: a pool cannot be inherited across a
fork, and file descriptors carried into a child produce corruption that only
shows under load. :func:`dispose_engine` exists for that reason and is called on
process boundaries; ``spawn`` (which is what the worker uses) starts a fresh
interpreter and so builds its own engine anyway.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from sqlalchemy import Connection, text
from sqlalchemy.engine import Engine

from auto_ontology.infra.postgres import get_postgres_connection_string

logger = logging.getLogger(__name__)

# psycopg / libpq reject a statement with more than 65535 bind parameters.
# Callers that expand an ``IN`` list of catalog ids stay at this size, well
# under the cap and in the same range illumex used for the equivalent Neo4j
# writes. Read at call time so tests can shrink it without rewriting queries.
IN_QUERY_BATCH = 1000

# Auto Ontology's tables are unqualified and resolve through `search_path`, which
# `get_engine` pins to `public` on every connection. There is deliberately no
# schema constant: naming the default schema is what produced permanent
# autogenerate drift and made Alembic stop recognising its own version table,
# and a name that appears in one place inevitably has to be repeated in every
# raw query, view definition, ops probe and chart comment. The pin is the one
# exception, and it is an assertion about the connection rather than a name the
# model reads.

_engine: Engine | None = None
_active: ContextVar[StoreConn | None] = ContextVar("_active_pg_conn", default=None)


def sqlalchemy_url() -> str:
    """The shared Postgres URL, pinned to the psycopg 3 driver.

    ``get_postgres_connection_string()`` returns a bare ``postgresql://`` URL,
    which SQLAlchemy resolves to **psycopg2** — a package this repo does not
    install. The rewrite mirrors ``auto_ontology/vdb/postgres.py``'s ``_to_async_url``,
    which does the same thing for asyncpg.
    """
    url = get_postgres_connection_string()
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    return url


def get_engine() -> Engine:
    """The process-wide engine, built on first use."""
    raise RuntimeError("Platform owns the database engine and migrations")


def dispose_engine() -> None:
    """Close every pooled connection. Idempotent.

    Called from ``auto_ontology.dal.close_store()`` on shutdown, and before handing a
    process to a child, since pooled sockets must not be shared across a fork.
    """
    global _engine
    if _engine is not None:
        try:
            _engine.dispose()
        except Exception:
            logger.warning("dispose_engine: engine failed to dispose", exc_info=True)
        finally:
            _engine = None


def _rows(result: Any) -> list[dict[str, Any]]:
    """Normalise a result to ``list[dict]``."""
    if result.returns_rows:
        return [dict(row) for row in result.mappings()]
    return []


class StoreConn:
    """A thin handle over a SQLAlchemy connection.

    The ``query_read`` / ``query_write`` split is naming only: it documents
    intent at the call site, and Postgres needs no routing hint.
    """

    def __init__(self, connection: Connection | None = None) -> None:
        self._connection = connection

    def query(
        self, statement: Any, parameters: dict[str, Any] | None = None, **_: Any
    ) -> list[dict[str, Any]]:
        compiled = text(statement) if isinstance(statement, str) else statement
        if self._connection is not None:
            return _rows(self._connection.execute(compiled, parameters or {}))
        # No open transaction: one autocommitted statement, connection returned
        # to the pool immediately. This is why a multi-statement write that
        # must be all-or-nothing has to go through write_transaction().
        with (
            get_engine()
            .connect()
            .execution_options(isolation_level="AUTOCOMMIT") as connection
        ):
            return _rows(connection.execute(compiled, parameters or {}))

    def query_read(
        self, statement: Any, parameters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        return self.query(statement, parameters)

    def query_write(
        self, statement: Any, parameters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        return self.query(statement, parameters)


def store() -> StoreConn:
    """Return the active transaction, or an autocommitting handle."""
    from auto_ontology.host import require

    return require().store_factory()


@contextmanager
def write_transaction() -> Iterator[None]:
    """Run every :func:`store` query in the block in one transaction.

    Commits on clean exit, rolls back on any exception. Nesting reuses the
    outermost transaction so the whole block stays a single unit of work —
    ``model_interchange``'s import depends on this.
    """
    from auto_ontology.host import require

    with require().transaction_factory():
        yield
    return
