# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Load and cache SQLDatabase connectors from CONNECTION_STRINGS."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from auto_ontology.catalog.table_filter import TableFilter, from_connection
from auto_ontology.connectors.base import SQLDatabase

from auto_ontology.connectors.connection_string_factory import build_connection_string

logger = logging.getLogger(__name__)

# Every connector here MUST expose a ``dialect`` that sqlglot recognises
# (one of ``sqlglot.dialects.DIALECTS``). Callers pass ``connector.dialect``
# straight into ``sqlglot.parse_one(read=...)`` / ``Expression.sql(dialect=...)``
# with no translation layer in between, and sqlglot raises on an unknown
# dialect *name* before it ever looks at the SQL — so a bad name is a hard
# error, not a silent fall back to the generic parser.
#
# An engine sqlglot has no dialect for must map itself onto the closest one it
# does have; see ``HeavyDBDatabase.dialect``, which reports ``"postgres"``.
# ``test_registry_dialects.py`` enforces this.
#
# Note the keys below are connection-string schemes, NOT dialects: ``postgresql``,
# ``heavydb`` and ``kyuubi`` are valid keys but none is a sqlglot dialect name.
CONNECTOR_REGISTRY: dict[str, type[SQLDatabase]] = {}


_connectors: list[SQLDatabase] | None = None


def parse_connection_strings(raw: str) -> list[str]:
    """Return connection URLs separated by commas, newlines, or both."""
    return [
        connection_string.strip()
        for connection_string in re.split(r"[,\r\n]+", raw)
        if connection_string.strip()
    ]


def _redact(connection_string: str) -> str:
    """Mask the password/token in a connection string before logging it.

    Connection strings carry a PAT (or, with per-user auth, a caller's exchanged
    Databricks token), so the raw value must never reach the logs.

    An uploaded Kyuubi truststore is also collapsed: it is base64 keystore bytes
    and runs to hundreds of kilobytes, which would otherwise be emitted verbatim
    on every connector failure.
    """
    connection_string = re.sub(
        r"(truststore_data=)[^&]+", r"\1<keystore>", connection_string
    )
    parsed = urlparse(connection_string)
    if not parsed.password:
        return connection_string
    return connection_string.replace(parsed.password, "***", 1)


def _schema_filter(connection: dict) -> list[str] | None:
    """Clean optional ``schemas`` ingestion allowlist off a connection dict.

    Returns a list of non-empty schema names, or ``None`` when absent (meaning
    "all schemas"). Only honoured by connectors that support schema filtering.
    """
    raw = connection.get("schemas")
    if not isinstance(raw, list):
        return None
    schemas = [str(s).strip() for s in raw if str(s).strip()]
    return schemas or None


def _table_filter(connection: dict) -> TableFilter:
    """Build the regex table allow/deny filter off a connection dict.

    A malformed pattern is logged and dropped here rather than raised: this runs
    while loading every configured connection, and one bad pattern must not stop
    the others from loading. The API validates patterns on save (422), so an
    invalid one should never reach this point.
    """
    try:
        return from_connection(connection)
    except ValueError:
        logger.exception(
            "Ignoring invalid table filter on connection %r",
            connection.get("database"),
        )
        return TableFilter()


def invalidate_connectors_cache() -> None:
    """Drop cached connectors so the next :func:`get_connectors` reloads."""
    global _connectors
    if _connectors is not None:
        for connector in _connectors:
            try:
                connector.close()
            except Exception:
                logger.exception("Failed to close connector during cache invalidation")
    _connectors = None


def create_connector(
    connection_string: str,
    schemas: list[str] | None = None,
    table_filter: "TableFilter | None" = None,
) -> SQLDatabase:
    """Parse *connection_string*, select a connector class, and return an instance.

    *schemas* is an optional ingestion allowlist. It is only honoured by
    connectors that support schema filtering (currently Databricks, Snowflake,
    Kyuubi, and Trino); for others it is ignored so their behaviour is unchanged.

    *table_filter* is the connection's regex allow/deny pair. Unlike *schemas*
    it applies to every connector, because it is enforced centrally in
    :func:`auto_ontology.catalog.extract.create_dataframe` rather than pushed
    down into each driver's introspection query.
    """
    raise RuntimeError("Use platform-authorized connector_factory")


def get_connectors_for_subject_token(
    subject_token: str | None,
) -> list[SQLDatabase]:
    """Build connectors, authenticating federated connections as the caller.

    Connections with "Authenticate as signed-in user" enabled get their stored
    PAT replaced by a token exchanged from *subject_token* (the caller's SSO
    JWT), so SQL runs under that user's Unity Catalog grants. Every other
    connection is untouched and reuses the shared cache.

    Fail-closed: a federated connection with no usable subject token raises
    rather than falling back to the stored PAT — otherwise the query would
    silently run with the PAT's broader privileges.

    Raises:
        DatabricksOAuthError: no subject token, or the exchange was rejected.
    """
    from auto_ontology.connectors.databricks_oauth import (
        DatabricksOAuthError,
        exchange_subject_token,
        uses_sso_federation,
    )
    from auto_ontology.dal.connections import list_connections

    try:
        connections = list_connections()
    except Exception:
        logger.exception("Failed to load connections for per-user Databricks auth")
        raise

    federated = [conn for conn in connections if uses_sso_federation(conn)]
    if not federated:
        return get_connectors()

    if not subject_token:
        raise DatabricksOAuthError(
            "This connection authenticates as the signed-in user, but the "
            "request carried no SSO token"
        )

    loaded: list[SQLDatabase] = []
    federated_names: set[str] = set()
    for conn in federated:
        access_token = exchange_subject_token(
            str(conn.get("host") or ""), subject_token
        )
        connection_string = build_connection_string(
            {**conn, "access_token_override": access_token}
        )
        connector = create_connector(
            connection_string,
            schemas=_schema_filter(conn),
            table_filter=_table_filter(conn),
        )
        federated_names.add(connector.database_name)
        loaded.append(connector)

    # Non-federated connections keep their own credentials; reuse the shared
    # cache rather than rebuilding them per request.
    loaded.extend(
        connector
        for connector in get_connectors()
        if connector.database_name not in federated_names
    )
    return loaded


def get_connectors() -> list[SQLDatabase]:
    """Return cached connectors for all configured connections.

    Text-to-SQL resolves the execution connector from
    ``relevant_tables[*].database_name`` (see
    ``auto_ontology.retrieval.text_to_sql.connector_routing``).
    Each connector's ``database_name`` must therefore be unique across the
    returned list — use ``metadata_database`` on Snowflake URLs (or distinct
    physical databases) when wiring multiple connections.
    """
    from auto_ontology.host import require

    return list(require().connector_factory())
