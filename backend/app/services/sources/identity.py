import json
import re
from datetime import UTC, datetime

from fastapi import HTTPException


def identity_inventory(server, query):
    """Optional enrichment: never make cAdvisor discovery depend on this collector."""
    if not server.docker_state_target:
        return {}
    selector = f'job="docker-state",server_id="{server.id}",source_epoch=' + json.dumps(
        server.collection_config_changed_at.isoformat()
    )
    expression = (
        f"docker_container_identity_info{{{selector}}} "
        f"and on(server_id, source_epoch) (docker_state_up{{{selector}}} == 1) "
        f"and on(server_id, source_epoch) "
        f"(time() - docker_state_last_poll_timestamp_seconds{{{selector}}} < 30) "
        f"and on(server_id, source_epoch) (up{{{selector}}} == 1)"
    )
    try:
        data = query("/api/v1/query", {"query": expression}, timeout_seconds=2)
        result = {}
        now = datetime.now(UTC).timestamp()
        for item in data.get("result", []):
            labels = item["metric"]
            at = float(item["value"][0])
            identifier = labels.get("docker_id", "")
            image_id = labels.get("image_id", "")
            if (
                not -5 <= now - at <= 60
                or float(item["value"][1]) != 1
                or not re.fullmatch(r"[a-f0-9]{64}", identifier)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)
                or labels.get("source_epoch") != server.collection_config_changed_at.isoformat()
                or labels.get("server_id") != str(server.id)
            ):
                continue
            allowed = {
                k: v
                for k, v in labels.items()
                if k
                in (
                    "revision",
                    "application",
                    "release",
                    "compose_project",
                    "compose_service",
                )
                and isinstance(v, str)
                and len(v) <= 200
            }
            result[identifier] = {
                "image_id": image_id,
                "identity": allowed,
                "identity_observed_at": datetime.fromtimestamp(at, UTC),
            }
        return result
    except (HTTPException, ValueError, KeyError, TypeError, OSError):
        return {}
