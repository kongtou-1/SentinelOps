from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException

from app.services.sources.identity import identity_inventory
from app.utils.docker import docker_identifier


def test_identity_enrichment_requires_current_epoch_fresh_samples_and_real_config_id():
    now = datetime.now(UTC)
    server = SimpleNamespace(
        id=uuid4(), docker_state_target="collector:9400", collection_config_changed_at=now
    )
    identifier = "a" * 64
    labels = {
        "server_id": str(server.id),
        "source_epoch": now.isoformat(),
        "docker_id": identifier,
        "image_id": "sha256:" + "b" * 64,
        "compose_service": "worker",
        "arbitrary_secret": "never-store",
    }

    def fetch(path, params, **kwargs):
        assert "docker_state_last_poll_timestamp_seconds" in params["query"]
        assert "up{" in params["query"]
        return {
            "result": [
                {"metric": labels, "value": [now.timestamp(), "1"]},
                {"metric": {**labels, "docker_id": "c" * 64}, "value": [now.timestamp() - 90, "1"]},
                {
                    "metric": {**labels, "docker_id": "d" * 64, "source_epoch": "old"},
                    "value": [now.timestamp(), "1"],
                },
            ]
        }

    result = identity_inventory(server, fetch)
    assert set(result) == {identifier}
    assert result[identifier]["identity"] == {"compose_service": "worker"}
    assert docker_identifier("/system.slice/docker-" + identifier + ".scope") == identifier


def test_optional_collector_failure_does_not_block_inventory():
    server = SimpleNamespace(
        id=uuid4(),
        docker_state_target="collector:9400",
        collection_config_changed_at=datetime.now(UTC),
    )

    def unavailable(*args, **kwargs):
        raise HTTPException(502, "offline")

    assert identity_inventory(server, unavailable) == {}
    server.docker_state_target = None
    assert identity_inventory(server, lambda *_: (_ for _ in ()).throw(AssertionError())) == {}
