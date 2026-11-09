"""Regression checks for stage-7 storage and deployment smoke boundaries."""

import importlib
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from auto_ontology.dal import schema as source
from sqlalchemy import create_engine, select, union_all
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import sessionmaker

from app.db.session import SessionLocal
from app.integrations.askdata_semantic import (
    DATA_COLLECTION,
    PlatformVectorStore,
    _BuildOntologyStore,
)
from app.models import AskDataEmbedding, AskDataModelVersion, ServiceDatabase, User
from app.services import askdata as settings
from app.services.askdata_build import BuildFailure, _counts, _require_content
from tests.test_askdata import create_connection, version


def _model(client, headers):
    identifier = create_connection(client, headers)
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "admin"))
        model = version(db, identifier, user.id)
        other = version(db, identifier, user.id)
        return identifier, user.id, model.id, other.id


def _record(text="original"):
    return [
        [
            {
                "text": text,
                "path": "node",
                "metadata": {
                    "embedding": [0.1] * 512,
                    "source_path": "node",
                    "database_name": "target",
                    "id": "node",
                },
            }
        ]
    ]


def test_vector_reset_and_deletion_preserve_other_collections_and_versions(client, admin_headers):
    _, _, model, other = _model(client, admin_headers)
    semantic = PlatformVectorStore(model)
    data = PlatformVectorStore(model, DATA_COLLECTION)
    other_data = PlatformVectorStore(other, DATA_COLLECTION)
    for store in (semantic, data, other_data):
        assert store.run(_record()) == 1
    PlatformVectorStore(model, DATA_COLLECTION, reset=True)
    with SessionLocal() as db:
        rows = list(db.scalars(select(AskDataEmbedding).execution_options(access_internal=True)))
        assert len(rows) == 3  # Reset soft-deletes derived rows.
        assert sum(row.deleted_at is not None for row in rows) == 1
        assert all(row.deleted_at is None for row in rows if row.collection == "semantic")
    assert data.run(_record("rebuilt")) == 1
    with SessionLocal() as db:
        rebuilt = db.scalar(
            select(AskDataEmbedding)
            .where(
                AskDataEmbedding.model_version_id == model,
                AskDataEmbedding.collection == "data",
            )
            .execution_options(access_internal=True)
        )
        assert rebuilt.deleted_at is None and rebuilt.content == "rebuilt"
    assert len(data.delete_by_database("target")) == 1
    assert semantic.delete_by_id("node") == 1
    with SessionLocal() as db:
        visible = list(
            db.scalars(
                select(AskDataEmbedding)
                .where(AskDataEmbedding.deleted_at.is_(None))
                .execution_options(access_internal=True)
            )
        )
        assert len(visible) == 1 and visible[0].model_version_id == other


def test_code_vectors_do_not_substitute_for_compiled_semantic_vectors(client, admin_headers):
    _, _, model, _ = _model(client, admin_headers)
    record = _record()
    record[0][0]["metadata"]["source"] = "code_rule"
    PlatformVectorStore(model).run(record)
    assert _counts(model)["semantic_embeddings"] == 0


def test_manual_table_notes_reach_catalog_ingestion_without_mutating_cache(client, admin_headers):
    connection_id, user_id, model_id, _ = _model(client, admin_headers)
    with SessionLocal() as db:
        connection = db.get(ServiceDatabase, connection_id)
        connection.query_enabled = True
        connection.permission_status = "verified"
        connection.allowed_tables = {"public.jobs": ["id"]}
        connection.schema_cache = {
            "tables": {
                "public.jobs": {
                    "columns": [{"name": "id", "type": "integer", "nullable": False}],
                    "comment": "physical comment",
                    "primary_key": ["id"],
                    "foreign_keys": [],
                }
            }
        }
        connection.table_notes = {"public.jobs": "人工补充的业务口径"}
        connection.schema_hash = "a" * 64
        db.get(AskDataModelVersion, model_id).schema_hash = connection.schema_hash
        db.commit()
    connector = settings.database_connector(user_id, connection_id, model_id, gate="askdata")
    assert connector.get_tables().iloc[0]["description"] == "人工补充的业务口径"
    with SessionLocal() as db:
        assert (
            db.get(ServiceDatabase, connection_id).schema_cache["tables"]["public.jobs"]["comment"]
            == "physical comment"
        )


def test_multi_block_insert_keeps_every_row_and_scopes_the_upsert(client, admin_headers):
    connection, user_id, model, _ = _model(client, admin_headers)
    identities = [str(uuid4()) for _ in range(2)]
    with SessionLocal() as db:
        user = db.get(User, user_id)
        with settings.storage(db, user, connection, model, "askdata:manage") as storage:
            statement = (
                insert(source.term)
                .values([{"id": identities[0], "name": "first", "source": "semantic"}])
                .values([{"id": identities[1], "name": "second", "source": "semantic"}])
            )
            statement = statement.on_conflict_do_update(
                index_elements=[source.term.c.id], set_={"name": statement.excluded.name}
            )
            storage.store_factory().query_write(statement)
            rows = storage.store_factory().query_read(select(source.term.c.id))
            assert {row["id"] for row in rows} == set(identities)
            db.commit()


def test_union_catalog_reads_scope_every_branch_to_the_bound_version(client, admin_headers):
    connection, user_id, model, other = _model(client, admin_headers)
    with SessionLocal() as db:
        user = db.get(User, user_id)
        for version_id, name in ((model, "selected"), (other, "hidden")):
            with settings.storage(db, user, connection, version_id, "askdata:manage") as storage:
                storage.store_factory().query_write(
                    insert(source.term).values(id=name, name=name, source="semantic")
                )
        with settings.storage(db, user, connection, model, "askdata:read") as storage:
            rows = storage.store_factory().query_read(
                union_all(select(source.term.c.id), select(source.term.c.id))
            )
            assert [row["id"] for row in rows] == ["selected", "selected"]


def test_authorization_is_rechecked_before_build_autocommit():
    commits, guards = [], []

    def guard(writing):
        guards.append(writing)
        if len(guards) == 2:
            raise PermissionError("revoked")

    db = SimpleNamespace(execute=lambda *args, **kwargs: SimpleNamespace(returns_rows=False))
    def commit(*, failed=False):
        if not failed:
            commits.append(True)

    store = _BuildOntologyStore(db, uuid4(), guard, commit)
    with pytest.raises(PermissionError, match="revoked"):
        store.query_write(insert(source.term).values(id="x", name="test", source="semantic"))
    assert not commits


def test_store_read_returns_connection_to_pool(monkeypatch):
    from app.db.session import engine
    from app.integrations import askdata_semantic

    pool = create_engine(engine.url, pool_size=1, max_overflow=0, pool_timeout=1)
    monkeypatch.setattr(askdata_semantic, "SessionLocal", sessionmaker(bind=pool))
    storage = askdata_semantic.BuildStorage(uuid4(), lambda writing: None)
    try:
        assert storage.store().query_read(select(source.term.c.id)) == []
        assert pool.pool.checkedout() == 0
        with storage.transaction():
            storage.store().query_read(select(source.term.c.id))
            assert pool.pool.checkedout() == 1
        assert pool.pool.checkedout() == 0
    finally:
        storage.close(success=False)
        pool.dispose()


def test_chinese_result_summary_reports_values_nulls_and_preview_limits():
    from app.schemas.askdata_runs import AskDataColumnMeta
    from app.services.askdata_query import _chinese_result_summary

    columns = [
        AskDataColumnMeta(name="status", kind="text"),
        AskDataColumnMeta(name="total", kind="integer"),
    ]
    assert "total：30" in _chinese_result_summary(columns, [[None, 30]])
    assert "status：未设置" in _chinese_result_summary(columns, [[None, 30]])
    assert "没有符合条件" in _chinese_result_summary(columns, [])
    preview = _chinese_result_summary(columns, [["a", 1]] * 4, truncated=True)
    assert "至少 4 行" in preview and "部分结果" in preview


def test_english_sql_explanation_is_replaced_from_final_masked_rows(
    client, admin_headers, monkeypatch
):
    from app.services import askdata_query, askdata_settings

    connection, _, model, _ = _model(client, admin_headers)
    monkeypatch.setattr(askdata_settings, "mask_result", lambda *args: [["***"]])
    connector = SimpleNamespace(
        execute_raw=lambda sql: {
            "columns": ["name"], "rows": [["private-value"]], "truncated": False
        }
    )
    result, status = askdata_query._result(
        uuid4(), {"connection_id": connection, "version_id": model},
        {"response": "This query lists names.", "sql_code": "SELECT name FROM public.jobs"},
        connector,
    )
    assert status == "completed"
    assert "查询返回 1 行" in result.answer and "***" in result.answer
    assert "private-value" not in result.answer and "This query" not in result.answer


@pytest.mark.parametrize("raw", [False, True])
@pytest.mark.parametrize("catalog", ["current", "other"])
def test_connector_routes_only_its_own_catalog_prefix(raw, catalog):
    from app.integrations.askdata_database import PlatformDatabase
    from app.integrations.askdata_sql import checked_askdata_query

    identifier = str(uuid4())
    tables = {"public.jobs": {"columns": [{"name": "id"}]}}
    captured = []

    def execute(sql):
        checked, _, _ = checked_askdata_query(
            sql, "postgresql", {"public.jobs": ["id"]}, tables
        )
        captured.append(checked)
        return {"columns": ["total"], "rows": [[1]], "truncated": False, "observed_at": None}

    connector = PlatformDatabase(
        identifier, lambda: {"engine": "postgresql", "tables": tables}, execute
    )
    prefix = identifier if catalog == "current" else "other_database"
    sql = f'SELECT COUNT(*) AS total FROM "{prefix}".public.jobs'
    invoke = connector.execute_raw if raw else connector.execute
    if catalog == "current":
        invoke(sql)
        assert identifier not in captured[0]
    else:
        with pytest.raises(ValueError, match="table_not_allowed"):
            invoke(sql)


@pytest.mark.parametrize(
    "data, semantic, error", [(0, 2, "data_embedding_empty"), (2, 0, "embedding_empty")]
)
def test_build_requires_both_retrieval_collections(data, semantic, error):
    with pytest.raises(BuildFailure, match=error):
        _require_content(
            {
                "tables": 1,
                "terms": 1,
                "embeddings": data + semantic,
                "data_embeddings": data,
                "semantic_embeddings": semantic,
            },
            uuid4(),
        )


def _smoke_module(monkeypatch, name):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module(name)


def test_smoke_requires_external_credentials_and_never_prints_login_tokens(monkeypatch, capsys):
    module = _smoke_module(monkeypatch, "smoke_askdata_stage7")
    monkeypatch.setenv("ASKDATA_SMOKE_PASSWORD", "Acceptance-only-password")
    monkeypatch.setattr("sys.argv", ["smoke"])

    def respond(request):
        if request.url.path == "/api/v1/auth/login":
            return httpx.Response(200, json={"access_token": "TOKEN-MUST-NOT-BE-PRINTED"})
        return httpx.Response(
            200, json={"status": "ready"} if request.url.path == "/health/ready" else []
        )

    client = httpx.Client(transport=httpx.MockTransport(respond), base_url="http://test")
    monkeypatch.setattr(module.httpx, "Client", lambda **kwargs: client)
    assert module.main() == 0
    output = capsys.readouterr().out
    assert "TOKEN-MUST-NOT-BE-PRINTED" not in output
    assert "Acceptance-only-password" not in output


def test_missing_smoke_credentials_fail_before_network(monkeypatch):
    module = _smoke_module(monkeypatch, "askdata_smoke_support")
    monkeypatch.delenv("ASKDATA_SMOKE_PASSWORD", raising=False)
    with pytest.raises(ValueError, match="credentials-file"):
        module.admin_credentials()


@pytest.mark.parametrize(
    "role, endpoint, thinking_disabled",
    [
        ("non_reasoning", "https://api.deepseek.com", True),
        ("reasoning", "https://api.deepseek.com", False),
        ("non_reasoning", "https://compatible.example/v1", False),
    ],
)
def test_deepseek_light_role_disables_default_thinking_only_on_official_endpoint(
    monkeypatch, role, endpoint, thinking_disabled
):
    import langchain_openai

    from app.integrations import askdata_model

    channel = {
        "id": str(uuid4()),
        "model": "deepseek-flash",
        "base_url": endpoint,
        "timeout_seconds": 60,
    }
    monkeypatch.setattr(
        askdata_model.agent_config,
        "profile",
        lambda module: {
            "enabled": True,
            "channels": [channel],
            "config_version": 1,
        },
    )
    monkeypatch.setattr(askdata_model.agent_config, "channel_key", lambda *a: "acceptance-only-key")
    monkeypatch.setattr(askdata_model, "_bindings", lambda: None)
    monkeypatch.setattr(langchain_openai, "ChatOpenAI", lambda **kwargs: kwargs)
    config = askdata_model.model_factory(role=role)
    assert (config.get("extra_body") == {"thinking": {"type": "disabled"}}) is thinking_disabled
