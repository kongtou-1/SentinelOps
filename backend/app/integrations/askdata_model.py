"""LangChain chat model backed by the platform's ask-data channel selection.

The selected reasoning and light-processing channels come from the connection's
execution settings and are copied into the task's ``model_config`` mapping.
Every call records provider-reported token usage so the ask-data statistics page
reports real model calls instead of a proxy count.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import uuid4

from langchain_core.callbacks import BaseCallbackHandler

from app.services import agent_config

logger = logging.getLogger(__name__)

CHANNEL_KEYS = {
    "reasoning": "ASKDATA_REASONING_CHANNEL",
    "non_reasoning": "ASKDATA_LIGHT_CHANNEL",
}


def model_factory(*, role="reasoning", model=None, temperature=0.0, max_tokens=8192):
    if role not in CHANNEL_KEYS:
        raise ValueError("unsupported_model_role")
    profile = agent_config.profile("diagnosis")
    if not profile["enabled"] or not profile["channels"]:
        raise ValueError("askdata_model_unavailable")
    bindings = _bindings()
    channel = _channel(profile, role, bindings)
    key = agent_config.channel_key("diagnosis", channel["id"], profile["config_version"])
    from langchain_openai import ChatOpenAI

    callbacks = []
    if bindings is not None:
        callbacks.append(
            _UsageRecorder(
                connection_id=bindings.connection_id,
                model_version_id=bindings.model_version_id,
                channel_id=channel["id"],
                model=model or channel["model"],
                role=role,
            )
        )
    options = {}
    if role == "non_reasoning" and urlsplit(channel["base_url"]).hostname == "api.deepseek.com":
        # DeepSeek defaults to thinking even for flash models. Honour the
        # upstream light-role contract explicitly on its official endpoint.
        options["extra_body"] = {"thinking": {"type": "disabled"}}
    return ChatOpenAI(
        model=model or channel["model"],
        api_key=key,
        base_url=channel["base_url"],
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=channel["timeout_seconds"],
        max_retries=0,
        callbacks=callbacks,
        **options,
    )


def _bindings():
    try:
        from auto_ontology.host import current

        return current()
    except Exception:  # noqa: BLE001 - outside a bound task there is nothing to record
        return None


def _channel(profile, role, bindings):
    channels = profile["channels"]
    wanted = (bindings.model_config.get(CHANNEL_KEYS[role]) if bindings else "") or ""
    for channel in channels:
        if wanted and str(channel["id"]) == wanted:
            return channel
    return channels[0]


class _UsageRecorder(BaseCallbackHandler):
    """One usage row per model call; failures never break the answer."""

    def __init__(self, *, connection_id, model_version_id, channel_id, model, role):
        self.connection_id = connection_id
        self.model_version_id = model_version_id
        self.channel_id = channel_id
        self.model = model
        self.role = role
        self._started: dict[str, float] = {}

    def on_llm_start(self, serialized, prompts, *, run_id=None, **kwargs):
        self._started[str(run_id)] = time.monotonic()

    def on_llm_end(self, response, *, run_id=None, **kwargs):
        started = self._started.pop(str(run_id), None)
        duration = time.monotonic() - started if started else None
        try:
            usage = (response.llm_output or {}).get("token_usage") or {}
            _insert(
                connection_id=self.connection_id,
                model_version_id=self.model_version_id,
                channel_id=self.channel_id,
                model=self.model,
                role=self.role,
                input_tokens=usage.get("prompt_tokens"),
                output_tokens=usage.get("completion_tokens"),
                duration=duration,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Ask-data model usage was not recorded")

    def on_llm_error(self, error, *, run_id=None, **kwargs):
        started = self._started.pop(str(run_id), None)
        try:
            _insert(
                connection_id=self.connection_id,
                model_version_id=self.model_version_id,
                channel_id=self.channel_id,
                model=self.model,
                role=self.role,
                input_tokens=None,
                output_tokens=None,
                duration=time.monotonic() - started if started else None,
                failed=True,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Ask-data failed model usage was not recorded")


def _insert(
    *,
    connection_id,
    model_version_id,
    channel_id,
    model,
    role,
    input_tokens,
    output_tokens,
    duration,
    failed=False,
):
    from app.db.session import SessionLocal
    from app.repositories import usage as repository

    with SessionLocal() as db:
        repository.insert_usage(
            db,
            {
                "dedupe_key": f"askdata:{model_version_id}:{uuid4().hex}",
                "source": "askdata",
                "provider_kind": "agent_channel",
                "application_id": None,
                "resource_kind": "service_database",
                "resource_id": connection_id,
                "purpose": "askdata",
                "operation": role,
                "model": (model or "")[:100],
                "agent_module": "askdata",
                "agent_channel_id": channel_id,
                "occurred_at": datetime.now(UTC),
                "calls": 1,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "unknown_calls": int(input_tokens is None or output_tokens is None),
                "outcome": "failed" if failed else "success",
                "duration_seconds": duration,
                "detail": {"reason": "askdata_model_call"},
            },
        )
        db.commit()
