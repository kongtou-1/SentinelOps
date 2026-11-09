"""Explicit entry points into the pinned, locally installed ontology library."""

from collections.abc import Iterator
from contextlib import contextmanager

from auto_ontology.host import HostBindings, bind

UPSTREAM_REVISION = "c4495670947eb7b4616f0dd9900e5ced2a48ca1e"


@contextmanager
def task(bindings: HostBindings) -> Iterator[None]:
    """The caller owns authorization, persistence and transaction factories."""
    with bind(bindings):
        yield


def compile_model(database_name: str, **kwargs):
    from auto_ontology.host import require

    require()
    from auto_ontology.semantic.compile import run_semantic_compilation

    return run_semantic_compilation(database_name, **kwargs)


def query(payload):
    from auto_ontology.host import require

    require()
    from auto_ontology.retrieval.text_to_sql.main import get_agent_response

    return get_agent_response(payload)
