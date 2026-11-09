"""Trusted definitions only. Knowledge text and model output cannot define commands."""

import hashlib
import json
import re

RESTART = {
    "code": "restart_container",
    "version": "1",
    "risk": "medium",
    "requires_human_approval": True,
    "parameters": {"container_id": "asset UUID"},
    "precheck": ["pinned_ssh", "container_identity", "not_paused_restarting_removing"],
    "action": "docker restart -t 10 <asset-docker-id>",
    "verify": {
        "timeout_seconds": 60,
        "stable_samples": 2,
        "interval_seconds": 2,
        "running": True,
        "healthy_if_configured": True,
    },
    "rollback": None,
}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def docker_identifier(value):
    match = re.fullmatch(r"(?:/docker/|/system.slice/docker-)?([a-f0-9]{64})(?:\.scope)?", value)
    if not match:
        raise ValueError("Invalid asset Docker ID")
    return match[1]
