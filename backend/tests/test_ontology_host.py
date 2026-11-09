"""Offline contracts for the vendored library's platform integration boundary."""

import asyncio
import hashlib
import json
import os
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from auto_ontology.host import (
    ContextThreadPoolExecutor,
    HostBindings,
    bind,
    current,
    require,
)


def bindings(**overrides):
    return HostBindings(
        connection_id=uuid4(),
        model_version_id=uuid4(),
        user_id=uuid4(),
        model_factory=overrides.get("model_factory", lambda **kw: kw),
        store_factory=lambda: "platform-store",
        transaction_factory=lambda: nullcontext(),
        vector_factory=lambda **kw: kw,
        connector_factory=lambda: ["authorized-connection"],
        embedding_factory=lambda texts, **kw: [[float(len(text))] for text in texts],
        settings_reader=lambda key: "5" if key == "timeout" else None,
        cancelled=lambda: False,
        model_config={"REASONING_API_KEY": "sensitive-test-value"},
    )


def test_unbound_operations_fail_closed():
    from auto_ontology.connectors import get_connectors
    from auto_ontology.dal.session import get_engine, store, write_transaction
    from auto_ontology.vdb import get_vdb

    from app.integrations.ontology import compile_model, query

    for operation in [require, store, get_engine, get_connectors, get_vdb]:
        with pytest.raises(RuntimeError):
            operation()
    with pytest.raises(RuntimeError), write_transaction():
        pytest.fail("An unbound transaction was opened")
    with pytest.raises(RuntimeError):
        compile_model("business")
    with pytest.raises(RuntimeError):
        query({"question": "How many?"})


def test_nested_bindings_restore_on_error_and_do_not_change_environment():
    before = dict(os.environ)
    outer, inner = bindings(), bindings()
    with bind(outer):
        with pytest.raises(ValueError), bind(inner):
            assert require() is inner
            raise ValueError("cancelled")
        assert require() is outer
    assert current() is None
    assert dict(os.environ) == before
    assert "sensitive-test-value" not in repr(outer)


def test_parallel_submissions_have_distinct_task_contexts():
    first, second = bindings(), bindings()
    with ContextThreadPoolExecutor(max_workers=2) as pool:
        with bind(first):
            a = pool.submit(require)
        with bind(second):
            b = pool.submit(require)
        assert a.result() is first
        assert b.result() is second
        assert pool.submit(current).result() is None


def test_async_tasks_have_distinct_bindings():
    async def run(value):
        with bind(value):
            await asyncio.sleep(0)
            return require()

    async def main():
        first, second = bindings(), bindings()
        results = await asyncio.gather(run(first), run(second))
        assert results == [first, second]

    asyncio.run(main())


def test_upstream_entrypoints_use_platform_factories():
    from auto_ontology.connectors import get_connectors
    from auto_ontology.dal.session import store, write_transaction
    from auto_ontology.infra.feature_flags import _read_configuration_row
    from auto_ontology.semantic.cancellation import is_cancelled
    from auto_ontology.utils.embedding import EmbedParams, embed_texts
    from auto_ontology.utils.llm_invoke import get_llm_client
    from auto_ontology.utils.model_config import resolve
    from auto_ontology.vdb import get_vdb
    from auto_ontology.vdb.postgres import PostgresVDB

    with bind(bindings()):
        assert get_connectors() == ["authorized-connection"]
        assert store() == "platform-store"
        with write_transaction():
            assert store() == "platform-store"
        assert _read_configuration_row("timeout") == ("5",)
        assert _read_configuration_row("unknown") is None
        assert get_llm_client()["role"] == "reasoning"
        assert resolve("REASONING", "API_KEY") == "sensitive-test-value"
        assert resolve("NON_REASONING", "API_KEY") == ""
        assert get_vdb(database_name="selected")["database_name"] == "selected"
        assert PostgresVDB(collection_name="terms")["collection_name"] == "terms"
        assert embed_texts(["中文", "Java"], EmbedParams("local", ""), "query") == [[2.0], [4.0]]
        assert is_cancelled() is False
    with bind(replace(bindings(), cancelled=lambda: True)):
        assert is_cancelled() is True


def test_core_modules_import_without_optional_drivers():
    import auto_ontology.catalog.extract
    import auto_ontology.retrieval.text_to_sql.main
    import auto_ontology.semantic.compile

    assert callable(auto_ontology.catalog.extract.extract_tabular_db_data)
    assert callable(auto_ontology.semantic.compile.run_semantic_compilation)
    assert callable(auto_ontology.retrieval.text_to_sql.main.get_agent_response)


def test_vendor_baseline_and_patch_inventory_are_complete():
    root = Path(__file__).resolve().parents[1] / "vendor" / "auto-ontology"
    manifest = json.loads((root / "SENTINELOPS_UPSTREAM.json").read_text(encoding="utf-8"))
    assert manifest["revision"] == "c4495670947eb7b4616f0dd9900e5ced2a48ca1e"
    changed = set(manifest["patched_files"])
    for name, digest in manifest["original_files"].items():
        file = root / name
        assert file.is_file(), f"Missing upstream file: {name}"
        if name not in changed:
            assert hashlib.sha256(file.read_bytes()).hexdigest() == digest, name
    assert (
        hashlib.sha256((root / "pyproject.upstream.toml").read_bytes()).hexdigest()
        == (manifest["original_files"]["pyproject.toml"])
    )
    assert (root / "LICENSE").is_file()
    assert (root / "THIRD_PARTY_NOTICES.md").is_file()
