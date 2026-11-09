from datetime import UTC, datetime
from urllib.parse import urlsplit

from fastapi import HTTPException

from app.core.config import get_settings
from app.core.secrets import cipher, file_secret
from app.db.session import SessionLocal
from app.models import AgentChannel
from app.repositories import agent_config as repository
from app.repositories.notifications import audit
from app.schemas.agent_config import ChannelInput, ChannelResponse


def response(db, module):
    row = repository.configuration(db, module)
    if not row:
        raise HTTPException(404, "Agent module not found")
    return {
        "module": module,
        "enabled": row.enabled,
        "platform_enabled": get_settings().ai_enabled,
        "version": row.version,
        "channels": [ChannelResponse.model_validate(c) for c in repository.channels(db, module)],
    }


def listing(db):
    return [response(db, module) for module in repository.MODULES]


def locked(db, module, version):
    row = repository.configuration(db, module, lock=True)
    if not row:
        raise HTTPException(404, "Agent module not found")
    if row.version != version:
        raise HTTPException(409, "Agent configuration changed; refresh and retry")
    return row


def changed(db, row, user, action, identifier=None):
    row.version += 1
    audit(
        db, user, action, "agent_config", identifier, {"module": row.module, "version": row.version}
    )
    db.commit()
    return response(db, row.module)


def update(db, module, body, user):
    row = locked(db, module, body.expected_version)
    row.enabled = body.enabled
    return changed(db, row, user, "agent_config_updated")


def save_channel(db, module, identifier, body, user):
    row = locked(db, module, body.expected_version)
    channel = repository.channel(db, module, identifier) if identifier else None
    if identifier and not channel:
        raise HTTPException(404, "Agent channel not found")
    if not channel:
        if body.api_key is None:
            raise HTTPException(422, "API key is required")
        existing = repository.channels(db, module)
        channel = AgentChannel(module=module, position=len(existing))
        db.add(channel)
    for key, value in body.model_dump(exclude={"expected_version", "api_key"}).items():
        setattr(channel, key, value)
    if body.api_key is not None:
        channel.encrypted_api_key = (
            cipher().encrypt(body.api_key.get_secret_value().encode()).decode()
        )
    channel.last_used_at = channel.last_success = channel.last_error = channel.last_latency_ms = (
        None
    )
    db.flush()
    return changed(db, row, user, "agent_channel_saved", channel.id)


def delete_channel(db, module, identifier, version, user):
    row = locked(db, module, version)
    channel = repository.channel(db, module, identifier)
    if not channel:
        raise HTTPException(404, "Agent channel not found")
    channel.deleted_at = channel.updated_at = datetime.now(UTC)
    db.flush()
    for position, current in enumerate(repository.channels(db, module)):
        current.position = position
    return changed(db, row, user, "agent_channel_deleted", identifier)


def reorder(db, module, body, user):
    row = locked(db, module, body.expected_version)
    channels = {c.id: c for c in repository.channels(db, module)}
    if len(body.channel_ids) != len(channels) or set(body.channel_ids) != set(channels):
        raise HTTPException(422, "Provide every active channel exactly once")
    for position, identifier in enumerate(body.channel_ids):
        channels[identifier].position = position
    return changed(db, row, user, "agent_channels_reordered")


def profile(module="diagnosis"):
    settings = get_settings()
    with SessionLocal() as db:
        row = repository.configuration(db, module)
        channels = repository.channels(db, module) if row else []
        active = [c for c in channels if c.enabled]
        return {
            "module": module,
            "config_version": row.version if row else 0,
            "enabled": bool(settings.ai_enabled and row and row.enabled),
            "model": active[0].model if active else "",
            "base_url": active[0].base_url if active else "",
            "channels": [
                {
                    "id": str(c.id),
                    "model": c.model,
                    "base_url": c.base_url,
                    "timeout_seconds": c.timeout_seconds,
                }
                for c in active
            ],
            "harness_version": "0.1.5rc1",
            "timeout_seconds": settings.ai_timeout_seconds,
            "profile_version": "2",
            "diagnosis_mode": "read_only",
            "provider_kind": settings.ai_provider_kind,
        }


def available(module="diagnosis"):
    config = profile(module)
    if not config["enabled"] or not config["channels"]:
        raise HTTPException(503, "Agent module is disabled or has no enabled model channel")


def channel_key(module, identifier, version):
    from uuid import UUID

    with SessionLocal() as db:
        row = repository.configuration(db, module)
        channel = repository.channel(db, module, UUID(str(identifier)))
        if (
            not row
            or row.version != version
            or not row.enabled
            or not channel
            or not channel.enabled
        ):
            raise ValueError("configuration_changed")
        return cipher().decrypt(channel.encrypted_api_key.encode()).decode()


def import_environment(db):
    candidates = [repository.configuration(db, m, lock=True) for m in repository.MODULES]
    candidates = [
        row
        for row in candidates
        if row and row.version == 1 and not repository.channels(db, row.module)
    ]
    if not candidates:
        db.commit()
        return []
    settings = get_settings()
    values = ChannelInput(
        name="主用",
        base_url=settings.ai_base_url,
        model=settings.ai_model,
        api_key=file_secret(settings.ai_key_file),
        expected_version=1,
    )
    imported = []
    for row in candidates:
        module = row.module
        # Never repopulate an intentionally emptied or previously configured module.
        if row and row.version == 1 and not repository.channels(db, module):
            channel = AgentChannel(
                module=module,
                name=values.name,
                enabled=True,
                position=0,
                base_url=values.base_url,
                model=values.model,
                timeout_seconds=60,
                encrypted_api_key=cipher()
                .encrypt(values.api_key.get_secret_value().encode())
                .decode(),
            )
            db.add(channel)
            row.version += 1
            audit(db, None, "agent_config_imported", "agent_config", detail={"module": module})
            imported.append(module)
    db.commit()
    return imported


async def test_channel(db, module, identifier, version, user):
    import asyncio
    import time
    from uuid import uuid4

    from app.integrations.agent_transport import ProviderFailure, buffered_request
    from app.integrations.usage_capture import UsageCapture
    from app.repositories.notifications import active_user

    locked(db, module, version)
    channel = repository.channel(db, module, identifier)
    if not channel:
        raise HTTPException(404, "Agent channel not found")
    config = {
        "base_url": channel.base_url,
        "model": channel.model,
        "timeout_seconds": channel.timeout_seconds,
    }
    key = cipher().decrypt(channel.encrypted_api_key.encode()).decode()
    user_id = user.id
    audit(db, user, "agent_channel_tested", "agent_config", identifier, {"module": module})
    db.commit()

    def authorize():
        with SessionLocal() as current:
            actor = active_user(current, user_id)
            row = repository.configuration(current, module)
            if not actor or actor.role.code != "admin":
                raise ProviderFailure("requester_unavailable", False)
            if not row or row.version != version:
                raise ProviderFailure("configuration_changed", False)

    async def guard():
        await asyncio.to_thread(authorize)

    capture = UsageCapture(
        ("agent_channel", uuid4(), "connection_test"),
        1,
        config["model"],
        "model",
        {
            "agent_module": module,
            "agent_channel_id": identifier,
            "config_version": version,
            "request_index": 1,
            "attempt_index": 1,
        },
    )
    started, error = time.monotonic(), None
    body = {
        "model": config["model"],
        "messages": [{"role": "user", "content": "Reply OK"}],
        "max_tokens": 64,
        "stream": False,
    }
    if urlsplit(config["base_url"]).hostname == "api.deepseek.com":
        # A connectivity probe needs an answer, not the provider's default thinking budget.
        body["thinking"] = {"type": "disabled"}
    try:
        await guard()
        await capture.start()
        await buffered_request(
            body,
            config,
            key,
            capture,
            set(),
            guard,
        )
        await guard()
    except ProviderFailure as exc:
        error = exc.code
    finally:
        if capture.key:
            await capture.finish(error)
    return {
        "success": error is None,
        "error_code": error,
        "latency_ms": (time.monotonic() - started) * 1000,
    }
