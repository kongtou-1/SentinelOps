# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from importlib import import_module
from auto_ontology.connectors.base import SQLDatabase

_DRIVERS = {
    "ClickHouseDatabase": "clickhouse",
    "DatabricksDatabase": "databricks",
    "DuckDBDatabase": "duckdb",
    "HeavyDBDatabase": "heavydb",
    "KyuubiDatabase": "kyuubi",
    "MySQLDatabase": "mysql",
    "PostgresDatabase": "postgres",
    "SnowflakeDatabase": "snowflake",
    "SQLiteDatabase": "sqlite",
    "TrinoDatabase": "trino",
}


def __getattr__(name):
    if name in _DRIVERS:
        return getattr(
            import_module(f"auto_ontology.connectors.{_DRIVERS[name]}"), name
        )
    if name in {"get_connectors", "invalidate_connectors_cache"}:
        return getattr(import_module("auto_ontology.connectors.registry"), name)
    raise AttributeError(name)


__all__ = ["SQLDatabase", *_DRIVERS, "get_connectors", "invalidate_connectors_cache"]
