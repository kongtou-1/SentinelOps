"""Explicit Admin switch with runtime verification and restoration on failure."""

import json
import os

import httpx2
import yaml
from fastapi import HTTPException

from app.core.config import get_settings


def write_atomic(path, content):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.chmod(0o644)
    os.replace(temporary, path)


def active_configuration():
    response = httpx2.get(
        get_settings().alertmanager_url.rstrip("/") + "/api/v2/status",
        timeout=5,
        trust_env=False,
        follow_redirects=False,
    )
    response.raise_for_status()
    return yaml.safe_load(response.json()["config"]["original"])


def reload_and_verify(managed):
    response = httpx2.post(
        get_settings().alertmanager_url.rstrip("/") + "/-/reload",
        timeout=5,
        trust_env=False,
        follow_redirects=False,
    )
    response.raise_for_status()
    platform = [r for r in active_configuration().get("receivers", []) if r["name"] == "platform"]
    if len(platform) != 1 or bool(platform[0].get("email_configs")) == managed:
        raise ValueError("Effective notification mode differs")


def restore(previous):
    directory = get_settings().notification_config_dir
    write_atomic(directory / "alertmanager.yml", previous["config"])
    mode_path = directory / "mode.json"
    if previous["mode"] is None:
        mode_path.unlink(missing_ok=True)
    else:
        write_atomic(mode_path, previous["mode"])
    reload_and_verify(previous["managed"])


def switch(managed):
    directory = get_settings().notification_config_dir
    path, mode_path = directory / "alertmanager.yml", directory / "mode.json"
    if not path.is_file():
        raise HTTPException(503, "Prepare the shared notification configuration volume first")
    previous = {
        "config": path.read_bytes(),
        "mode": mode_path.read_bytes() if mode_path.exists() else None,
    }
    config = yaml.safe_load(previous["config"])
    mode = json.loads(previous["mode"]) if previous["mode"] else {}
    receivers = [r for r in config.get("receivers", []) if r["name"] == "platform"]
    if len(receivers) != 1:
        raise HTTPException(409, "Expected one platform Alertmanager receiver")
    receiver = receivers[0]
    previous["managed"] = not bool(receiver.get("email_configs"))
    legacy = receiver.get("email_configs") or mode.get("legacy_email_configs")
    if not legacy:
        raise HTTPException(409, "Legacy email configuration is unavailable")
    if managed:
        receiver.pop("email_configs", None)
    else:
        receiver["email_configs"] = legacy
    try:
        write_atomic(path, yaml.safe_dump(config, sort_keys=False).encode())
        reload_and_verify(managed)
        write_atomic(
            mode_path, json.dumps({"managed": managed, "legacy_email_configs": legacy}).encode()
        )
    except Exception:
        try:
            restore(previous)
        except Exception:
            raise HTTPException(
                503, "Effective notification state is unknown; restore connectivity and reapply"
            ) from None
        raise HTTPException(
            502, "Notification switch failed; previous runtime configuration restored"
        ) from None
    return previous
