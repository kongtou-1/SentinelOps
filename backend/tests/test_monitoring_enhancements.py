from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from test_resources_alerts import isolated_secrets as isolated_secrets
from test_resources_alerts import server_body as server_body
from test_resources_alerts import server_id as server_id

from app.db.session import SessionLocal
from app.models import Container
from app.services.monitoring.metrics import metric_availability
from app.services.monitoring.summary import container_health, server_summaries


def asset(**changes):
    return SimpleNamespace(
        id=uuid4(),
        monitoring_enabled=True,
        collection_config_changed_at=datetime.now(UTC),
        node_exporter_target="node:9100",
        cadvisor_target="cadvisor:8080",
        docker_state_target=None,
        health_url=None,
        **changes,
    )


def install_prometheus(monkeypatch, servers, fail_memory=False, overrides=None):
    calls = []
    targets = [
        {
            "labels": {
                "server_id": str(s.id),
                "source_epoch": s.collection_config_changed_at.isoformat(),
                "job": job,
            },
            "lastScrape": datetime.now(UTC).isoformat(),
            "health": "up",
        }
        for s in servers
        for job in ("node", "cadvisor")
    ]
    if overrides:
        for t in targets:
            t.update(overrides)

    def prom(path, params, **kwargs):
        calls.append(path)
        if path.endswith("targets"):
            return {"activeTargets": targets}
        if fail_memory and "MemAvailable" in params["query"]:
            raise HTTPException(502)
        return {
            "result": [
                {
                    "metric": {
                        "server_id": str(s.id),
                        "source_epoch": s.collection_config_changed_at.isoformat(),
                    },
                    "value": [datetime.now(UTC).timestamp(), "0"],
                }
                for s in servers
            ]
        }

    monkeypatch.setattr("app.services.monitoring.summary.prometheus", prom)
    return calls, targets


@pytest.mark.parametrize("count", [1, 50, 100])
def test_summary_request_count_is_constant_and_real_zero_survives(monkeypatch, count):
    servers = [asset() for _ in range(count)]
    calls, _ = install_prometheus(monkeypatch, servers)
    result = server_summaries(servers)
    assert len(calls) == 6 and len(result) == count
    assert all(s["metrics"]["host_cpu_percent"]["value"] == 0 for s in result)
    assert all(s["metrics"]["target_up"]["value"] == 1 for s in result)
    assert result[0]["collectors"]["docker-state"]["availability"] == "unconfigured"


def test_summary_isolates_metric_failure_and_configuration_epoch(monkeypatch):
    servers = [asset(), asset()]
    _, targets = install_prometheus(monkeypatch, servers, fail_memory=True)
    targets[2]["labels"]["source_epoch"] = "previous-configuration"
    result = server_summaries(servers)
    assert result[0]["metrics"]["host_cpu_percent"]["value"] == 0
    assert result[0]["metrics"]["host_memory_percent"]["reason"] == "query_failed"
    assert result[1]["metrics"]["host_cpu_percent"]["value"] is None
    assert result[1]["collectors"]["node"]["reason"] == "awaiting_discovery"


@pytest.mark.parametrize(
    "override,expected",
    [
        ({"health": "down"}, "unavailable"),
        ({"lastScrape": (datetime.now(UTC) - timedelta(minutes=3)).isoformat()}, "stale"),
    ],
)
def test_stale_or_failed_collector_does_not_show_healthy_samples(monkeypatch, override, expected):
    server = asset()
    install_prometheus(monkeypatch, [server], overrides=override)
    result = server_summaries([server])[0]
    assert result["collectors"]["node"]["availability"] == expected
    assert result["metrics"]["host_cpu_percent"]["value"] is None
    assert result["metrics"]["target_up"]["value"] != 1


def test_disabled_summary_does_not_query_old_data(monkeypatch):
    server = asset()
    server.monitoring_enabled = False
    monkeypatch.setattr(
        "app.services.monitoring.summary.prometheus", lambda *_a, **_k: pytest.fail("paused")
    )
    result = server_summaries([server])[0]
    assert result["metrics"]["host_cpu_percent"]["availability"] == "paused"
    assert result["metrics"]["target_up"]["value"] is None


def test_range_fills_missing_and_invalid_points_but_preserves_zero():
    start = datetime.fromtimestamp(1000, UTC)
    result = metric_availability(
        {
            "resultType": "matrix",
            "result": [
                {"metric": {}, "values": [[1000, "0"], [1030, "NaN"], [1090, "12"], [1120, "+Inf"]]}
            ],
        },
        start,
        start + timedelta(seconds=120),
        30,
    )
    assert result["result"][0]["values"] == [
        [1000, "0"],
        [1030, None],
        [1060, None],
        [1090, "12"],
        [1120, None],
    ]
    assert result["step"] == 30


def test_range_marks_a_stale_tail_without_erasing_history():
    start = datetime.fromtimestamp(1000, UTC)
    result = metric_availability(
        {"resultType": "matrix", "result": [{"metric": {}, "values": [[1000, "0"]]}]},
        start,
        start + timedelta(seconds=180),
        30,
    )
    assert result["availability"] == "stale"
    assert result["result"][0]["values"][-1] == [1180, None]


@pytest.mark.parametrize("state", ["healthy", "unhealthy", "starting", "none", "stopped"])
def test_health_preserves_all_states_and_requires_fresh_engine_poll(monkeypatch, state):
    server = asset()
    server.docker_state_target = "docker-state:9400"
    poll_at = datetime.now(UTC).timestamp()
    calls = []

    def prom(_path, params, **kwargs):
        calls.append(params["query"])
        return {
            "result": [
                {
                    "metric": {"health": state},
                    "value": [poll_at, str(poll_at) if "last_poll" in params["query"] else "1"],
                }
            ]
        }

    monkeypatch.setattr("app.services.monitoring.summary.prometheus", prom)
    assert container_health(server, "/docker/" + "a" * 64)["status"] == state
    assert 'docker_id="' + "a" * 64 + '"' in calls[-1]
    poll_at -= 120
    assert container_health(server, "/docker/" + "a" * 64)["status"] == "unknown"


@pytest.fixture
def container_id(server_id):
    with SessionLocal() as db:
        c = Container(
            server_id=UUID(server_id),
            docker_id="/docker/" + "a" * 64,
            name="fixture",
            image="fixture",
            status="running",
            labels={},
            last_seen_at=datetime.now(UTC),
        )
        db.add(c)
        db.commit()
        return str(c.id)


@pytest.mark.parametrize("role,expected", [("viewer", 403), ("operator", 200), ("admin", 200)])
def test_manual_diagnostics_permission_and_target_binding(
    client, make_user, login, container_id, monkeypatch, role, expected
):
    make_user("reader-" + role, role)
    headers = {"Authorization": "Bearer " + login("reader-" + role)["access_token"]}
    calls = []

    def read(server, credential, docker_id, logs=False):
        calls.append((docker_id, logs))
        return {"logs" if logs else "state": "[REDACTED]", "truncated": True}

    monkeypatch.setattr("app.services.resources.crud.read_container", read)
    for kind in ("state", "logs"):
        response = client.get(
            f"/api/v1/containers/{container_id}/diagnostics?kind={kind}", headers=headers
        )
        assert response.status_code == expected
        if expected == 200:
            assert response.json()[kind] == "[REDACTED]" and response.json()["truncated"]
    assert calls == (
        [] if role == "viewer" else [("/docker/" + "a" * 64, False), ("/docker/" + "a" * 64, True)]
    )
    if expected == 200:
        assert (
            client.get(
                f"/api/v1/containers/{container_id}/diagnostics?kind=exec", headers=headers
            ).status_code
            == 422
        )


def test_summary_api_bounds_and_deleted_asset_filter(client, admin_headers, server_id, monkeypatch):
    monkeypatch.setattr(
        "app.services.resources.crud.server_summaries",
        lambda servers: [{"server_id": s.id, "metrics": {}, "collectors": {}} for s in servers],
    )
    path = "/api/v1/monitoring/servers-summary"
    assert client.get(path, headers=admin_headers).status_code == 422
    assert (
        client.get(
            path, params=[("server_ids", server_id)] * 101, headers=admin_headers
        ).status_code
        == 422
    )
    assert (
        len(client.get(path, params={"server_ids": server_id}, headers=admin_headers).json()) == 1
    )
    client.delete("/api/v1/servers/" + server_id, headers=admin_headers)
    assert client.get(path, params={"server_ids": server_id}, headers=admin_headers).json() == []


def test_range_rate_query_is_gated_by_scrape_health(monkeypatch):
    from app.services.resources.metrics import metrics_result

    expressions = []

    def prom(path, params):
        expressions.append(params["query"])
        return {"resultType": "matrix", "result": []}

    monkeypatch.setattr("app.services.resources.metrics.prometheus", prom)
    start = datetime.now(UTC) - timedelta(minutes=10)
    metrics_result("host_cpu_percent", None, None, start, datetime.now(UTC), 30)
    metrics_result("container_memory_bytes", None, None, start, datetime.now(UTC), 30)
    assert 'up{job="node"} == 1' in expressions[0]
    assert 'up{job="cadvisor"} == 1' in expressions[1]
