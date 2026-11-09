"""Direct database reads; credentials never leave this adapter."""

import json
import re
import ssl
import tempfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import pymysql

from app.core.secrets import cipher
from app.utils.redaction import TOKEN_COUNT_FIELDS, redact

MAX_BYTES = 128 * 1024


class DatabaseUnavailable(Exception):
    pass


def secrets(row):
    try:
        return json.loads(cipher().decrypt(row.encrypted_config.encode()))
    except Exception:
        raise DatabaseUnavailable("credential_unavailable") from None


def error_code(exc):
    code = (
        exc.args[0] if exc.args and isinstance(exc.args[0], int) else getattr(exc, "sqlstate", None)
    )
    if code in ("57014", "55P03", 3024, 1205):
        return "query_timeout"
    if code in ("42501", 1142, 1143):
        return "permission_denied"
    if code in ("42P01", "42703", 1054, 1146):
        return "schema_changed"
    if code in ("28P01", "28000", 1045):
        return "authentication_failed"
    return "database_unavailable"


@contextmanager
def connect(row):
    config = secrets(row)
    connection = None
    try:
        with tempfile.TemporaryDirectory(prefix="sentinelops-db-") as folder:
            if row.engine == "postgresql":
                options = {"sslmode": row.tls_mode}
                if config.get("ca_certificate"):
                    ca = Path(folder) / "ca.pem"
                    ca.write_text(config["ca_certificate"], encoding="utf-8")
                    options["sslrootcert"] = str(ca)
                connection = psycopg.connect(
                    host=row.host,
                    port=row.port,
                    dbname=row.database,
                    user=row.username,
                    password=config.get("password", ""),
                    connect_timeout=3,
                    autocommit=True,
                    options="-c statement_timeout=5000 -c lock_timeout=1000 "
                    "-c idle_in_transaction_session_timeout=5000 "
                    "-c search_path=pg_catalog -c timezone=UTC",
                    **options,
                )
                connection.execute("BEGIN READ ONLY")
            else:
                tls = (
                    ssl.create_default_context(cadata=config.get("ca_certificate") or None)
                    if row.tls_mode == "verify-full"
                    else None
                )
                connection = pymysql.connect(
                    host=row.host,
                    port=row.port,
                    database=row.database,
                    user=row.username,
                    password=config.get("password", ""),
                    ssl=tls,
                    ssl_disabled=row.tls_mode == "disable",
                    charset="utf8mb4",
                    connect_timeout=3,
                    read_timeout=6,
                    write_timeout=3,
                    local_infile=False,
                    cursorclass=pymysql.cursors.SSCursor,
                )
                if tls and not isinstance(connection._sock, ssl.SSLSocket):
                    raise DatabaseUnavailable("tls_required")
                with connection.cursor() as cursor:
                    cursor.execute("SET SESSION time_zone='+00:00'")
                    cursor.execute("SET SESSION max_execution_time=5000")
                    cursor.execute("SET SESSION innodb_lock_wait_timeout=1")
                    cursor.execute("START TRANSACTION READ ONLY")
            yield connection
    except DatabaseUnavailable:
        raise
    except Exception as exc:
        raise DatabaseUnavailable(error_code(exc)) from None
    finally:
        if connection:
            try:
                connection.rollback()
            except Exception:
                pass  # A broken connection must not replace the original read error.
            try:
                connection.close()
            except Exception:
                pass


def fetch(connection, sql, parameters=()):
    with connection.cursor() as cursor:
        cursor.execute(sql, parameters) if parameters else cursor.execute(sql)
        return cursor.fetchall()


def permissions(row, connection):
    """Fail closed on role grants we cannot interpret. Never attempt a write."""
    if row.engine == "postgresql":
        flags = fetch(
            connection,
            """
            SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_roles
              WHERE pg_catalog.pg_has_role(current_user, oid, 'USAGE')
              AND (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls)),
              pg_catalog.has_database_privilege(current_user, current_database(), 'CREATE')
        """,
        )[0]
        if any(flags):
            return "unsafe"
        for key, columns in row.allowed_tables.items():
            schema, table = key.split(".")
            tables = fetch(
                connection,
                """
                SELECT c.oid, pg_catalog.pg_has_role(current_user, c.relowner, 'USAGE'),
                  pg_catalog.has_schema_privilege(current_user, n.oid, 'CREATE')
                FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname=%s AND c.relname=%s AND c.relkind IN ('r','p')
            """,
                (schema, table),
            )
            if len(tables) != 1:
                return "unverified"
            oid, owner, schema_create = tables[0]
            if (
                owner
                or schema_create
                or any(
                    fetch(
                        connection,
                        """
                SELECT pg_catalog.has_table_privilege(current_user, %s::oid,
                  'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'),
                  pg_catalog.has_any_column_privilege(
                    current_user, %s::oid, 'INSERT,UPDATE,REFERENCES')
            """,
                        (oid, oid),
                    )[0]
                )
            ):
                return "unsafe"
            if not fetch(
                connection,
                "SELECT bool_and(pg_catalog.has_column_privilege("
                "current_user,%s::oid,col,'SELECT')) "
                "FROM unnest(%s::text[]) AS fields(col)",
                (oid, columns),
            )[0][0]:
                return "unverified"
        return "verified"
    for (grant,) in fetch(connection, "SHOW GRANTS"):
        match = re.match(r"GRANT (.*?) ON (.*?) TO ", grant, re.I)
        if not match:
            return "unverified"
        rights = match.group(1).upper()
        if rights not in ("SELECT", "USAGE") or "WITH GRANT OPTION" in grant.upper():
            return "unsafe"
    # Object-level access is also checked by the actual SELECT; no write probe is needed.
    return "verified"


def schema(row, connection):
    schema_filter = "c.table_schema NOT IN ('pg_catalog','information_schema')"
    comment = (
        "pg_catalog.col_description((pg_catalog.quote_ident(c.table_schema)||'.'||"
        "pg_catalog.quote_ident(c.table_name))::regclass::oid,c.ordinal_position)"
    )
    params = ()
    if row.engine == "mysql":
        schema_filter, params = "c.table_schema=%s", (row.database,)
        comment = "c.column_comment"
    rows = fetch(
        connection,
        f"""
        SELECT c.table_schema,c.table_name,c.column_name,c.data_type,c.is_nullable,{comment}
        FROM information_schema.columns c JOIN information_schema.tables t
          ON t.table_schema=c.table_schema AND t.table_name=c.table_name
        WHERE {schema_filter} AND t.table_type='BASE TABLE'
        ORDER BY c.table_schema,c.table_name,c.ordinal_position LIMIT 10001
    """,
        params,
    )
    tables = {}
    truncated = len(rows) > 10000
    for namespace, table, column, datatype, nullable, column_comment in rows[:10000]:
        key = f"{namespace}.{table}"
        if key not in tables and len(tables) >= 200:
            truncated = True
            continue
        entry = tables.setdefault(
            key,
            {"columns": [], "indexes": [], "comment": "", "primary_key": [], "foreign_keys": []},
        )
        entry["columns"].append(
            {
                "name": column,
                "type": datatype,
                "nullable": nullable == "YES",
                "comment": column_comment or "",
            }
        )
    if row.engine == "postgresql":
        for ns, table, definition in fetch(
            connection, "SELECT schemaname,tablename,indexdef FROM pg_catalog.pg_indexes LIMIT 5000"
        ):
            if f"{ns}.{table}" in tables:
                tables[f"{ns}.{table}"]["indexes"].append(definition)
        for ns, table, comment in fetch(
            connection,
            """
            SELECT n.nspname,c.relname,pg_catalog.obj_description(c.oid,'pg_class')
            FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON c.relnamespace=n.oid
            WHERE c.relkind IN ('r','p') AND n.nspname NOT IN ('pg_catalog','information_schema')
            LIMIT 200
        """,
        ):
            if f"{ns}.{table}" in tables:
                tables[f"{ns}.{table}"]["comment"] = comment or ""
    else:
        for ns, table, index, column in fetch(
            connection,
            "SELECT TABLE_SCHEMA,TABLE_NAME,"
            "INDEX_NAME,COLUMN_NAME FROM information_schema.statistics "
            "WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME,INDEX_NAME,SEQ_IN_INDEX LIMIT 5000",
            (row.database,),
        ):
            if f"{ns}.{table}" in tables:
                tables[f"{ns}.{table}"]["indexes"].append(f"{index}: {column}")
        for table, comment in fetch(
            connection,
            "SELECT TABLE_NAME,TABLE_COMMENT FROM "
            "information_schema.tables WHERE TABLE_SCHEMA=%s LIMIT 200",
            (row.database,),
        ):
            if f"{row.database}.{table}" in tables:
                tables[f"{row.database}.{table}"]["comment"] = comment or ""
    if row.engine == "postgresql":
        constraints = fetch(
            connection,
            """
            SELECT n.nspname,c.relname,k.conname,k.contype,
                   array_agg(a.attname ORDER BY ord.position),rn.nspname,rc.relname,
                   array_agg(ra.attname ORDER BY ord.position)
            FROM pg_catalog.pg_constraint k
            JOIN pg_catalog.pg_class c ON c.oid=k.conrelid
            JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            CROSS JOIN LATERAL unnest(k.conkey) WITH ORDINALITY ord(attnum,position)
            JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum=ord.attnum
            LEFT JOIN pg_catalog.pg_class rc ON rc.oid=k.confrelid
            LEFT JOIN pg_catalog.pg_namespace rn ON rn.oid=rc.relnamespace
            LEFT JOIN pg_catalog.pg_attribute ra ON ra.attrelid=rc.oid
              AND ra.attnum=k.confkey[ord.position]
            WHERE k.contype IN ('p','f') AND n.nspname NOT IN ('pg_catalog','information_schema')
            GROUP BY n.nspname,c.relname,k.conname,k.contype,rn.nspname,rc.relname
            ORDER BY n.nspname,c.relname,k.conname LIMIT 10001
        """,
        )
        truncated = truncated or len(constraints) > 10000
        for (
            namespace,
            table,
            name,
            kind,
            columns,
            ref_namespace,
            ref_table,
            ref_columns,
        ) in constraints[:10000]:
            entry = tables.get(f"{namespace}.{table}")
            if entry is None:
                continue
            if kind == "p":
                entry["primary_key"] = list(columns)
            elif f"{ref_namespace}.{ref_table}" in tables:
                entry["foreign_keys"].append(
                    {
                        "name": name,
                        "columns": list(columns),
                        "referenced_table": f"{ref_namespace}.{ref_table}",
                        "referenced_columns": list(ref_columns),
                    }
                )
    else:
        constraints = fetch(
            connection,
            """
            SELECT TABLE_SCHEMA,TABLE_NAME,CONSTRAINT_NAME,COLUMN_NAME,
                   REFERENCED_TABLE_SCHEMA,REFERENCED_TABLE_NAME,REFERENCED_COLUMN_NAME
            FROM information_schema.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA=%s
              AND (CONSTRAINT_NAME='PRIMARY' OR REFERENCED_TABLE_NAME IS NOT NULL)
            ORDER BY TABLE_NAME,CONSTRAINT_NAME,ORDINAL_POSITION LIMIT 10001
        """,
            (row.database,),
        )
        truncated = truncated or len(constraints) > 10000
        foreign_keys = {}
        for namespace, table, name, column, ref_namespace, ref_table, ref_column in constraints[
            :10000
        ]:
            entry = tables.get(f"{namespace}.{table}")
            if entry is None:
                continue
            if name == "PRIMARY":
                entry["primary_key"].append(column)
            elif f"{ref_namespace}.{ref_table}" in tables:
                key = (namespace, table, name)
                if key not in foreign_keys:
                    foreign_keys[key] = {
                        "name": name,
                        "columns": [],
                        "referenced_table": f"{ref_namespace}.{ref_table}",
                        "referenced_columns": [],
                    }
                    entry["foreign_keys"].append(foreign_keys[key])
                foreign_keys[key]["columns"].append(column)
                foreign_keys[key]["referenced_columns"].append(ref_column)
    result = redact({"tables": tables, "truncated": truncated})
    if len(json.dumps(result, ensure_ascii=False).encode()) > MAX_BYTES:
        raise DatabaseUnavailable("schema_too_large")
    return result


_INTEGER_TYPES = frozenset({1, 2, 3, 8, 9, 20, 21, 23, 24, 26})
_DECIMAL_TYPES = frozenset({4, 5, 246, 700, 701, 1700})
_DATE_TYPES = frozenset({10, 14, 1082})
_DATETIME_TYPES = frozenset({7, 12, 1114, 1184})
_JSON_TYPES = frozenset({114, 245, 3802})
_BINARY_TYPES = frozenset({17, 249, 250, 251, 252})


def _column_meta(description):
    """Keep the driver's type so ask-data can render numbers without losing precision."""
    columns = []
    for item in description or ():
        code = item[1]
        if code in _INTEGER_TYPES:
            kind = "integer"
        elif code in _DECIMAL_TYPES:
            kind = "decimal"
        elif code == 16:
            kind = "boolean"
        elif code in _DATE_TYPES:
            kind = "date"
        elif code in _DATETIME_TYPES:
            kind = "datetime"
        elif code in _JSON_TYPES:
            kind = "json"
        elif code in _BINARY_TYPES:
            kind = "binary"
        else:
            kind = "text"
        columns.append(
            {
                "name": item[0],
                "kind": kind,
                "type_code": code if isinstance(code, (int, str)) else str(code),
            }
        )
    return columns


def query(connection, sql, include_types=False):
    deadline = time.monotonic() + 5
    cursor = (
        connection.cursor(name="sentinelops_read")
        if isinstance(connection, psycopg.Connection)
        else connection.cursor()
    )
    if isinstance(cursor, psycopg.ServerCursor):
        cursor.itersize = 1
    with cursor:
        cursor.execute(sql)
        columns = [col[0] for col in cursor.description]
        result = {
            "columns": columns,
            "rows": [],
            "truncated": False,
            "observed_at": datetime.now(UTC).isoformat(),
            "state": "at_query_time",
        }
        if include_types:
            result["column_types"] = _column_meta(cursor.description)
        size = len(json.dumps(result).encode())
        for i in range(201):
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                raise DatabaseUnavailable("query_timeout")
            if isinstance(cursor, psycopg.ServerCursor):
                # FETCH is a separate statement: retain one deadline for the whole query.
                connection.execute(f"SET LOCAL statement_timeout={remaining_ms}")
            values = cursor.fetchone()
            if values is None:
                break
            if i >= 200:
                result["truncated"] = True
                break
            if any(v is not None and len(str(v)) > 4096 for v in values):
                result["truncated"] = True
            cells = [str(v)[:4096] if v is not None else None for v in values]
            safe = redact(dict(zip(columns, cells, strict=True)))
            for column, value, cell in zip(columns, values, cells, strict=True):
                if column.lower() in TOKEN_COUNT_FIELDS and (
                    value is None
                    or (isinstance(value, (int, float, Decimal)) and not isinstance(value, bool))
                ):
                    safe[column] = cell
            cells = [safe[c] for c in columns]
            size += len(json.dumps(cells, ensure_ascii=False).encode())
            if size > MAX_BYTES - 4096:
                result["truncated"] = True
                break
            result["rows"].append(cells)
        result["limit_reached"] = len(result["rows"]) == 200
        return result
