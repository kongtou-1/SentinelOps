"""SentinelOps embedding hooks. No credentials, engines or clients are global.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar, copy_context
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any
from types import MappingProxyType
from uuid import UUID


@dataclass(frozen=True)
class HostBindings:
    """Factories supplied by a single authorized platform task.

    Factories must validate authorization when performing I/O, not just at bind
    time. A store factory returns a task/thread-local handle, never a Session
    shared between threads. Transaction ownership remains with the platform.
    """

    connection_id: UUID
    model_version_id: UUID
    user_id: UUID
    model_factory: Callable[..., Any] = field(repr=False)
    store_factory: Callable[[], Any] = field(repr=False)
    transaction_factory: Callable[[], AbstractContextManager] = field(repr=False)
    vector_factory: Callable[..., Any] = field(repr=False)
    connector_factory: Callable[[], list] = field(repr=False)
    embedding_factory: Callable[..., Any] = field(repr=False)
    settings_reader: Callable[[str], Any] = field(repr=False)
    cancelled: Callable[[], bool] = field(repr=False)
    model_config: Mapping[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "model_config", MappingProxyType(dict(self.model_config))
        )


_bindings: ContextVar[HostBindings | None] = ContextVar("ontology_host", default=None)


def current() -> HostBindings | None:
    return _bindings.get()


def require() -> HostBindings:
    bindings = current()
    if bindings is None:
        raise RuntimeError("Auto-Ontology requires an authorized platform task")
    return bindings


@contextmanager
def bind(bindings: HostBindings) -> Iterator[HostBindings]:
    token = _bindings.set(bindings)
    try:
        yield bindings
    finally:
        _bindings.reset(token)


def contextual_call(function: Callable, *args: Any, **kwargs: Any) -> Callable[[], Any]:
    """Capture a fresh context for one executor submission; never reuse it."""
    context = copy_context()
    return lambda: context.run(function, *args, **kwargs)


class ContextThreadPoolExecutor(ThreadPoolExecutor):
    """Propagate task bindings to upstream parallel compilation stages."""

    def submit(self, fn: Callable, /, *args: Any, **kwargs: Any):
        return super().submit(contextual_call(fn, *args, **kwargs))
