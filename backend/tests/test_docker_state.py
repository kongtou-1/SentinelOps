import pytest

import app.integrations.docker_state as exporter

IDENTIFIER = "a" * 64


def engine_fixture(monkeypatch, events):
    def read(endpoint, params):
        if endpoint == "/containers/json":
            return [
                {"Id": IDENTIFIER, "State": "running", "Names": ["/bounded-test"], "Image": "test"}
            ]
        assert endpoint == "/events"
        return [e for e in events if params["since"] <= e["time"] <= params["until"]]

    monkeypatch.setattr(exporter, "read_engine", read)


def event(at, action):
    return {"time": at, "timeNano": at * 10**9, "Actor": {"ID": IDENTIFIER}, "Action": action}


def test_complete_window_counts_each_start_and_oom_once(monkeypatch):
    engine_fixture(
        monkeypatch,
        [
            event(1200, "die"),
            event(1201, "start"),
            event(1210, "oom"),
            event(1211, "die"),
            event(1212, "start"),
        ],
    )
    collector = exporter.Collector()
    for at in range(1000, 1300, 5):
        collector.poll(at)
    assert "docker_state_window_complete 0" in collector.metrics(1295)
    assert "docker_container_restarts_5m" not in collector.metrics(1295)
    collector.poll(1300)
    metrics = collector.metrics(1300)
    assert "docker_state_window_complete 1" in metrics
    assert next(
        line for line in metrics.splitlines() if line.startswith("docker_container_restarts_5m")
    ).endswith(" 2")
    assert next(
        line for line in metrics.splitlines() if line.startswith("docker_container_oom_5m")
    ).endswith(" 1")
    for at in range(1305, 1516, 5):
        collector.poll(at)
    assert next(
        line
        for line in collector.metrics(1515).splitlines()
        if line.startswith("docker_container_restarts_5m")
    ).endswith(" 0")


def test_first_startup_is_not_a_restart(monkeypatch):
    engine_fixture(monkeypatch, [event(1200, "create"), event(1201, "start")])
    collector = exporter.Collector()
    for at in range(1000, 1301, 5):
        collector.poll(at)
    assert next(
        line
        for line in collector.metrics(1300).splitlines()
        if line.startswith("docker_container_restarts_5m")
    ).endswith(" 0")


def test_collector_failure_or_poll_gap_does_not_report_healthy_zeros(monkeypatch):
    engine_fixture(monkeypatch, [])
    collector = exporter.Collector()
    for at in range(1000, 1301, 5):
        collector.poll(at)
    collector.poll(1400)
    assert "docker_state_window_complete 0" in collector.metrics(1400)
    assert "docker_container_oom_5m" not in collector.metrics(1400)

    def unavailable(*_):
        raise OSError

    monkeypatch.setattr(exporter, "read_engine", unavailable)
    collector.poll(1405)
    assert "docker_state_up 0" in collector.metrics(1405)
    assert "docker_container_restarts_5m" not in collector.metrics(1405)


def test_engine_path_is_fixed_and_truncated_history_is_unknown(monkeypatch):
    with pytest.raises(ValueError, match="rejected"):
        exporter.read_engine("/containers/arbitrary/exec", {})
    engine_fixture(monkeypatch, [])
    collector = exporter.Collector()
    collector.poll(1000)
    monkeypatch.setattr(
        exporter,
        "read_engine",
        lambda endpoint, _params: (
            [{"Id": IDENTIFIER, "State": "running"}]
            if endpoint == "/containers/json"
            else [event(1001, "start")] * 256
        ),
    )
    collector.poll(1005)
    assert "docker_state_up 0" in collector.metrics(1005)


@pytest.mark.parametrize(
    "status,health,binary",
    [
        ("Up 10 seconds (healthy)", "healthy", 0),
        ("Up 10 seconds (unhealthy)", "unhealthy", 1),
        ("Up 10 seconds (health: starting)", "starting", None),
        ("Up 10 seconds", "none", None),
        ("unexpected", "unknown", None),
    ],
)
def test_health_is_current_before_complete_lifecycle_window(monkeypatch, status, health, binary):
    monkeypatch.setattr(
        exporter,
        "read_engine",
        lambda *_: [{"Id": IDENTIFIER, "State": "running", "Status": status}],
    )
    collector = exporter.Collector()
    collector.poll(1000)
    text = collector.metrics(1000)
    assert "docker_state_window_complete 0" in text
    assert f'health="{health}"' in text
    lines = [line for line in text.splitlines() if line.startswith("docker_container_unhealthy{")]
    assert len(lines) == (0 if binary is None else 1)
    if lines:
        assert lines[0].endswith(f" {binary}")
    assert "docker_container_health_status{" not in collector.metrics(1016)
    assert "docker_container_unhealthy{" not in collector.metrics(1016)


def test_structured_health_and_stopped_container():
    assert (
        exporter.health_status({"State": "running", "Health": {"Status": "unhealthy"}})
        == "unhealthy"
    )
    assert exporter.health_status({"State": "exited", "Status": "Exited (0)"}) == "stopped"


def test_identity_uses_config_id_and_whitelisted_labels(monkeypatch):
    monkeypatch.setattr(
        exporter,
        "read_engine",
        lambda *_: [
            {
                "Id": IDENTIFIER,
                "State": "running",
                "Image": "same:latest",
                "ImageID": "sha256:" + "b" * 64,
                "Labels": {
                    "org.opencontainers.image.revision": "c" * 40,
                    "org.opencontainers.image.source": "https://secret@example.invalid/repo",
                    "sentinelops.application": "inference-api",
                    "com.docker.compose.service": "api",
                    "api_token": "do-not-expose",
                },
            }
        ],
    )
    collector = exporter.Collector()
    collector.poll(1000)
    text = collector.metrics(1000)
    line = next(x for x in text.splitlines() if x.startswith("docker_container_identity_info{"))
    assert 'image_id="sha256:' in line and 'compose_service="api"' in line
    assert "do-not-expose" not in text and "secret@example" not in text
    assert "same:latest" not in line
    assert "docker_container_identity_info{" not in collector.metrics(1016)


def test_invalid_identity_is_not_published():
    assert (
        exporter.identity_labels(
            {
                "ImageID": "same:latest",
                "Labels": {
                    "org.opencontainers.image.revision": "main",
                    "sentinelops.application": "a\nsecret",
                    "com.docker.compose.service": 'bad"label',
                },
            }
        )
        == {}
    )


def test_state_and_network_membership_are_read_only_and_fresh(monkeypatch):
    monkeypatch.setattr(
        exporter,
        "read_engine",
        lambda *_: [
            {
                "Id": IDENTIFIER,
                "State": "paused",
                "Names": ["/api"],
                "Image": "api:1",
                "NetworkSettings": {"Networks": {"private": {}, "backend": {}}},
                "Labels": {"secret": "never-publish"},
            }
        ],
    )
    collector = exporter.Collector()
    collector.poll(1000)
    metrics = collector.metrics(1000)
    assert 'state="paused"' in metrics
    assert 'network="private"' in metrics and 'network="backend"' in metrics
    assert "docker_container_network_inventory{" in metrics
    assert "never-publish" not in metrics
    assert "docker_container_network_info{" not in collector.metrics(1016)
