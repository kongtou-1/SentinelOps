"""Auto-Ontology connector interface over platform-authorized data access."""

from datetime import UTC, datetime

import pandas as pd
from auto_ontology.connectors.base import SQLDatabase


class PlatformDatabase(SQLDatabase):
    def __init__(self, connection_id, loader, executor, query_provider=None):
        self.connection_id = connection_id
        self._loader, self._executor = loader, executor
        self._query_provider = query_provider

    @property
    def database_name(self):
        # Physical names are not unique across monitored environments.
        return str(self.connection_id)

    @property
    def dialect(self):
        return "postgres" if self._loader()["engine"] == "postgresql" else "mysql"

    def execute(self, sql, parameters=None):
        if parameters is not None:
            raise ValueError("ontology_positional_parameters_not_supported")
        from app.integrations.askdata_sampling import sample_statement

        snapshot = self._loader()
        sql = self._local_sql(sql, snapshot)
        statement = sample_statement(
            sql,
            self.dialect,
            snapshot["tables"],
            excluded=snapshot.get("sensitive_exclusions"),
        )
        if statement is None:
            result = self._executor(sql)
        elif not statement or not snapshot.get("query_enabled", False):
            # Structure-only builds, and tables outside the grant, contribute
            # no row samples. Upstream keeps its schema-derived descriptions.
            frame = pd.DataFrame()
            frame.attrs["truncated"] = False
            frame.attrs["observed_at"] = None
            return frame
        else:
            result = self._executor(statement)
        frame = pd.DataFrame(result["rows"], columns=result["columns"])
        specs = snapshot.get("sensitive_specs") or {}
        if specs:
            from app.services.askdata_settings import apply_mask

            for name, spec in specs.items():
                if name in frame.columns:
                    frame[name] = frame[name].map(
                        lambda value: apply_mask(value, spec["strategy"], spec["argument"])
                    )
        frame.attrs["truncated"] = result["truncated"]
        frame.attrs["observed_at"] = result["observed_at"]
        return frame

    def authorized_tables(self):
        """Authorization-filtered schema cache, safe to use for SQL gating."""
        return self._loader()["tables"]

    def execute_raw(self, sql):
        """Final ask-data query, without the semantic-build sampling rewrite.

        Returns the platform's own bounded result dict (including column types),
        so the answer, table and chart all consume the same executed rows.
        """
        if not sql or not str(sql).strip():
            raise ValueError("empty_sql")
        return self._executor(self._local_sql(sql, self._loader()))

    def _local_sql(self, sql, snapshot):
        """Remove only this connector's logical routing prefix before SQL gating."""
        import sqlglot
        from sqlglot import exp
        from sqlglot.errors import ParseError

        dialect = "postgres" if snapshot["engine"] == "postgresql" else "mysql"
        try:
            statements = sqlglot.parse(sql, read=dialect)
        except ParseError:
            return sql  # The platform gate owns rejection and stable error codes.
        if len(statements) != 1 or statements[0] is None:
            return sql
        tree = statements[0]
        changed = False
        for table in tree.find_all(exp.Table):
            if table.catalog == self.database_name:
                table.set("catalog", None)
                changed = True
        return tree.sql(dialect=dialect) if changed else sql

    def get_tables(self):
        return pd.DataFrame(
            [
                {
                    "table_schema": key.split(".", 1)[0],
                    "table_name": key.split(".", 1)[1],
                    "description": table.get("comment", ""),
                    "table_type": "base table",
                }
                for key, table in self._loader()["tables"].items()
            ],
            columns=["table_schema", "table_name", "description", "table_type"],
        )

    def get_columns(self):
        return pd.DataFrame(
            [
                {
                    "table_schema": key.split(".", 1)[0],
                    "table_name": key.split(".", 1)[1],
                    "column_name": column["name"],
                    "data_type": column["type"],
                    "is_nullable": column["nullable"],
                    "description": column.get("comment", ""),
                    "ordinal_position": position,
                }
                for key, table in self._loader()["tables"].items()
                for position, column in enumerate(table["columns"], 1)
            ],
            columns=[
                "table_schema",
                "table_name",
                "column_name",
                "data_type",
                "is_nullable",
                "description",
                "ordinal_position",
            ],
        )

    def get_pks(self):
        return pd.DataFrame(
            [
                {
                    "table_schema": key.split(".", 1)[0],
                    "table_name": key.split(".", 1)[1],
                    "column_name": column,
                    "ordinal_position": position,
                }
                for key, table in self._loader()["tables"].items()
                for position, column in enumerate(table.get("primary_key", []), 1)
            ],
            columns=["table_schema", "table_name", "column_name", "ordinal_position"],
        )

    def get_fks(self):
        return pd.DataFrame(
            [
                {
                    "table_schema": key.split(".", 1)[0],
                    "table_name": key.split(".", 1)[1],
                    "column_name": column,
                    "referenced_schema": fk["referenced_table"].split(".", 1)[0],
                    "referenced_table": fk["referenced_table"].split(".", 1)[1],
                    "referenced_column": reference,
                }
                for key, table in self._loader()["tables"].items()
                for fk in table.get("foreign_keys", [])
                for column, reference in zip(fk["columns"], fk["referenced_columns"], strict=True)
            ],
            columns=[
                "table_schema",
                "table_name",
                "column_name",
                "referenced_schema",
                "referenced_table",
                "referenced_column",
            ],
        )

    def get_queries(self, hours=24):
        """Stage-4 query history: statically resolved, authorized code SQL.

        The upstream catalog writer parses these into Sql nodes, which
        ``suggest_sql_attributes`` later turns into metric expressions. Dynamic
        fragments and SQL outside the connection grant never reach here.
        """
        columns = ["end_time", "query_text", "count"]
        snapshot = self._loader()
        if not snapshot.get("query_enabled") or self._query_provider is None:
            return pd.DataFrame(columns=columns)
        try:
            statements = self._query_provider(snapshot) or []
        except Exception:
            return pd.DataFrame(columns=columns)
        if not statements:
            return pd.DataFrame(columns=columns)
        observed = datetime.now(UTC)
        return pd.DataFrame(
            [
                {"end_time": observed, "query_text": str(statement), "count": 1}
                for statement in statements
            ],
            columns=columns,
        )

    def get_views(self):
        self._loader()
        return pd.DataFrame(columns=["table_schema", "table_name", "view_definition"])

    def close(self):
        # Every external read owns a short-lived connection in the platform executor.
        pass
