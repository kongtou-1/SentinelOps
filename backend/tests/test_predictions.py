import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import numpy as np
import pytest
import yaml
from fastapi import HTTPException
from sqlalchemy import select, text
from test_resources_alerts import (
    SERVICE_HEADERS,
    webhook_body,
)
from test_resources_alerts import (
    isolated_secrets as isolated_secrets,
)
from test_resources_alerts import (
    server_body as server_body,
)
from test_resources_alerts import (
    server_id as server_id,
)

from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.models import LLMEndpoint, PredictionResult, PredictionTask, Server
from app.services.predictions.engine import (
    backtest,
    compute,
    detect_anomaly,
    forecast,
    prepare_series,
    risk,
)
from app.services.predictions.tasks import (
    claim,
    complete,
    enqueue,
    interrupt_runs,
    load_history,
    schedule_due,
    snapshot,
)

END = 1800000000
CONFIG = {"metric": "host_cpu_percent", "threshold": 80}


def samples(values, end=END):
    return [[end - (len(values) - 1 - i) * 300, float(value)] for i, value in enumerate(values)]


@pytest.fixture
def task_body(server_id):
    return {
        "name": "CPU forecast",
        "server_id": server_id,
        "metric": "host_cpu_percent",
        "threshold": 80,
    }


@pytest.fixture
def task_id(client, admin_headers, task_body):
    response = client.post("/api/v1/prediction-tasks", headers=admin_headers, json=task_body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def finished_run(task_id, values=None, hours=None, enabled=True):
    with SessionLocal() as db:
        task = db.get(PredictionTask, UUID(task_id))
        task.enabled = enabled
        db.commit()
        result = enqueue(db, task)
        result.status = "running"
        db.commit()
        end = int(datetime.now(UTC).timestamp()) // 300 * 300
        answer = compute(
            samples(values if values is not None else np.full(120, 30), end), end, CONFIG
        )
        if hours:
            answer["horizons"].update(hours)
        complete(db, result.id, answer)
        return str(result.id)


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
def test_prediction_permissions(client, make_user, login, role, server_body):
    make_user(role, role)
    auth = {"Authorization": f"Bearer {login(role)['access_token']}"}
    assert client.get("/api/v1/prediction-tasks", headers=auth).status_code == 200
    assert client.get("/api/v1/predictions", headers=auth).status_code == 200
    body = {"name": "x", "server_id": str(uuid4()), "metric": "host_cpu_percent", "threshold": 80}
    assert client.post("/api/v1/prediction-tasks", headers=auth, json=body).status_code == (
        404 if role == "admin" else 403
    )
    for method in ("put", "delete"):
        kwargs = {"headers": auth, **({"json": body} if method == "put" else {})}
        assert getattr(client, method)(
            f"/api/v1/prediction-tasks/{uuid4()}", **kwargs
        ).status_code == (404 if role == "admin" else 403)
    assert client.post(f"/api/v1/prediction-tasks/{uuid4()}/run", headers=auth).status_code == (
        403 if role == "viewer" else 404
    )
    assert client.get("/api/v1/monitoring/prediction-metrics", headers=auth).status_code == 401


def test_crud_queue_conflicts_and_soft_delete(client, admin_headers, task_body, task_id):
    base = f"/api/v1/prediction-tasks/{task_id}"
    before = client.get(base, headers=admin_headers).json()
    assert before["enabled"] is False and before["interval_seconds"] == 300
    changed = {**task_body, "threshold": 75}
    after = client.put(base, headers=admin_headers, json=changed).json()
    assert after["updated_at"] > before["updated_at"]
    run = client.post(base + "/run", headers=admin_headers)
    assert run.status_code == 202
    assert run.json()["status"] == "queued"
    assert client.get(base, headers=admin_headers).json()["latest_result"]["id"] == run.json()["id"]
    assert client.post(base + "/run", headers=admin_headers).status_code == 409
    assert client.put(base, headers=admin_headers, json=task_body).status_code == 409
    assert client.delete(base, headers=admin_headers).status_code == 409
    with SessionLocal() as db:
        claimed = claim(db)
        assert claimed.status == "running"
        interrupt_runs(db)
    assert (
        client.get("/api/v1/predictions/" + run.json()["id"], headers=admin_headers).json()[
            "status"
        ]
        == "interrupted"
    )
    assert client.delete(base, headers=admin_headers).status_code == 204
    assert client.get(base, headers=admin_headers).status_code == 404
    assert client.get("/api/v1/predictions", headers=admin_headers).json() == []
    assert (
        client.post("/api/v1/prediction-tasks", headers=admin_headers, json=task_body).status_code
        == 409
    )
    with SessionLocal() as db:
        deleted = db.get(PredictionTask, UUID(task_id))
        assert deleted.deleted_at and deleted.updated_at >= deleted.deleted_at


@pytest.mark.parametrize(
    "change",
    [
        {"interval_seconds": 299},
        {"interval_seconds": 86401},
        {"threshold": 101},
        {"metric": "target_up"},
        {"metric": "llm_error_rate"},
        {"selector": {"device": "x"}},
        {"container_id": str(uuid4())},
        {"metric": "host_disk_percent"},
        {"metric": "host_network_receive_bytes"},
    ],
)
def test_task_validation(client, admin_headers, task_body, change):
    assert (
        client.post(
            "/api/v1/prediction-tasks", headers=admin_headers, json={**task_body, **change}
        ).status_code
        == 422
    )


def test_durable_scheduler_and_disabled_deleted_sources(task_id):
    with SessionLocal() as db:
        task = db.get(PredictionTask, UUID(task_id))
        assert schedule_due(db) == 0
        task.enabled = True
        task.next_run_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
        assert schedule_due(db) == 1
        assert schedule_due(db, datetime.now(UTC) + timedelta(hours=1)) == 0
        run = claim(db)
        interrupt_runs(db)
        task.next_run_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
        assert schedule_due(db) == 1
        claim(db)
        complete(
            db,
            db.scalar(select(PredictionResult.id).where(PredictionResult.status == "running")),
            error_code="test_failure",
        )
        server = db.get(Server, task.server_id)
        server.monitoring_enabled = False
        task.next_run_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
        assert schedule_due(db) == 0
        server.monitoring_enabled = True
        server.deleted_at = datetime.now(UTC)
        db.commit()
        assert schedule_due(db) == 0
        assert db.get(PredictionResult, run.id).status == "interrupted"


def test_concurrent_enqueue_once(task_id):
    def queue():
        with SessionLocal() as db:
            try:
                return enqueue(db, db.get(PredictionTask, UUID(task_id))).status
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        answers = list(executor.map(lambda _: queue(), range(2)))
    assert sorted(answers, key=str) == sorted([409, "queued"], key=str)


def test_result_discarded_when_source_changes(task_id):
    with SessionLocal() as db:
        task = db.get(PredictionTask, UUID(task_id))
        run = enqueue(db, task)
        server = db.get(Server, task.server_id)
        server.monitoring_enabled = False
        db.commit()
        result = complete(db, run.id, compute(samples(np.ones(120)), END, CONFIG))
        assert result.status == "failed" and not result.horizons


def test_timestamp_trigger_for_prediction_tables(task_id):
    with SessionLocal() as db:
        run = enqueue(db, db.get(PredictionTask, UUID(task_id)))
        run_id = run.id
    with engine.begin() as connection:
        for table, row_id in (("prediction_tasks", task_id), ("prediction_results", run_id)):
            before = connection.scalar(
                text(f"SELECT updated_at FROM {table} WHERE id=:id"), {"id": row_id}
            )
            after = connection.scalar(
                text(f"UPDATE {table} SET updated_at=updated_at WHERE id=:id RETURNING updated_at"),
                {"id": row_id},
            )
            assert after > before


def test_metrics_quality_expiry_disable_and_filters(client, admin_headers, task_id):
    result_id = finished_run(task_id)
    result = client.get(f"/api/v1/predictions/{result_id}", headers=admin_headers).json()
    assert result["fresh"] is True
    exported = client.get("/api/v1/monitoring/prediction-metrics", headers=SERVICE_HEADERS)
    assert exported.status_code == 200
    assert 'horizon_hours="1"' in exported.text
    assert 'horizon_hours="6"' not in exported.text
    assert "secret" not in exported.text and "points" not in exported.text
    for query in (
        f"task_id={task_id}",
        f"server_id={result['config']['server_id']}",
        "status=completed",
    ):
        assert len(client.get("/api/v1/predictions?" + query, headers=admin_headers).json()) == 1
    assert (
        client.get(
            "/api/v1/predictions?start=2026-01-01T00:00:00Z", headers=admin_headers
        ).status_code
        == 422
    )
    with SessionLocal() as db:
        run = db.get(PredictionResult, UUID(result_id))
        run.data_end_at = datetime.now(UTC) - timedelta(hours=1)
        db.commit()
    exported = client.get("/api/v1/monitoring/prediction-metrics", headers=SERVICE_HEADERS).text
    assert "prediction_risk{" not in exported
    assert "prediction_data_usable{" in exported
    with SessionLocal() as db:
        task = db.get(PredictionTask, UUID(task_id))
        task.enabled = False
        db.commit()
    assert (
        task_id
        not in client.get("/api/v1/monitoring/prediction-metrics", headers=SERVICE_HEADERS).text
    )


def test_history_fixed_queries_selection_and_epoch(task_id, monkeypatch):
    with SessionLocal() as db:
        config = snapshot(db, db.get(PredictionTask, UUID(task_id)))
    calls = []

    def prometheus(path, params):
        calls.append((path, params))
        return {"result": [{"metric": {"device": "sda"}, "values": [[END, "12"]]}]}

    monkeypatch.setattr("app.services.predictions.tasks.prometheus", prometheus)
    values, _ = load_history(config, datetime.fromtimestamp(END, UTC))
    assert values == [[END, "12"]]
    assert "avg_over_time" in calls[0][1]["query"]
    assert calls[0][1]["step"] == 300
    config.update(metric="host_network_receive_bytes", selector={"device": "eth0"})
    assert load_history(config, datetime.fromtimestamp(END, UTC))[0] == []
    monkeypatch.setattr(
        "app.services.predictions.tasks.prometheus",
        lambda *a, **k: {"result": [{"metric": {"device": "eth0"}}] * 2},
    )
    with pytest.raises(HTTPException, match="Ambiguous"):
        load_history(config, datetime.fromtimestamp(END, UTC))


def test_llm_config_epoch_resets_history(client, admin_headers, monkeypatch):
    body = {
        "name": "provider",
        "base_url": "https://example.test/v1",
        "model": "model-a",
        "api_key": "test-key",
        "probe_enabled": False,
    }
    endpoint = client.post("/api/v1/llm-endpoints", headers=admin_headers, json=body).json()
    endpoint_id = UUID(endpoint["id"])
    with SessionLocal() as db:
        before = db.get(LLMEndpoint, endpoint_id).probe_config_changed_at
    body.update(model="model-b")
    assert (
        client.put(
            f"/api/v1/llm-endpoints/{endpoint_id}", headers=admin_headers, json=body
        ).status_code
        == 200
    )
    with SessionLocal() as db:
        after = db.get(LLMEndpoint, endpoint_id).probe_config_changed_at
        assert after > before
        task = PredictionTask(
            name="latency",
            llm_endpoint_id=endpoint_id,
            metric="llm_latency_avg_seconds",
            threshold=5,
        )
        db.add(task)
        db.commit()
        config = snapshot(db, task)
        assert config["source_epoch"] == after.isoformat()
        assert "key" not in json.dumps(config)
    seen = []
    monkeypatch.setattr(
        "app.services.predictions.tasks.prometheus", lambda path, params: seen.append(params) or {"result": []}
    )
    load_history(config, after + timedelta(hours=1))
    assert seen[0]["start"] > after.timestamp()


def test_prediction_alert_rules_webhook_and_recovery(client, admin_headers, task_id, monkeypatch):
    finished_run(task_id)
    body = {
        "code": "CPUForecastRisk",
        "prediction_task_id": task_id,
        "prediction_horizon_hours": 1,
        "metric": "prediction_risk",
        "threshold": 1,
    }
    rule = client.post("/api/v1/alert-rules", headers=admin_headers, json=body)
    assert rule.status_code == 201, rule.text
    monkeypatch.setattr("app.services.monitoring.config.prometheus", lambda *a, **k: {})
    monkeypatch.setattr("app.services.monitoring.config.verify_active_rules", lambda *a, **k: None)
    assert client.post("/api/v1/monitoring/apply-rules", headers=admin_headers).status_code == 200
    applied = yaml.safe_load((get_settings().monitoring_rules_dir / "platform.yml").read_text())
    generated = applied["groups"][0]["rules"][0]
    assert task_id in generated["expr"] and 'horizon_hours="1"' in generated["expr"]
    assert "first_breach" in generated["annotations"]
    payload = webhook_body()
    payload["alerts"][0]["labels"].update(task_id=task_id, rule_id=rule.json()["id"])
    assert (
        client.post("/api/v1/hooks/alertmanager", json=payload, headers=SERVICE_HEADERS).json()[
            "changed"
        ]
        == 1
    )
    assert (
        client.post("/api/v1/hooks/alertmanager", json=payload, headers=SERVICE_HEADERS).json()[
            "changed"
        ]
        == 0
    )
    event = client.get(
        "/api/v1/alerts?prediction_task_id=" + task_id, headers=admin_headers
    ).json()[0]
    assert event["prediction_task_id"] == task_id
    payload["alerts"][0].update(status="resolved", endsAt=datetime.now(UTC).isoformat())
    assert (
        client.post("/api/v1/hooks/alertmanager", json=payload, headers=SERVICE_HEADERS).json()[
            "changed"
        ]
        == 1
    )
    assert (
        client.get("/api/v1/alerts/" + event["id"], headers=admin_headers).json()["status"]
        == "resolved"
    )
    with SessionLocal() as db:
        task = db.get(PredictionTask, UUID(task_id))
        task.enabled = False
        db.commit()
    client.post("/api/v1/monitoring/apply-rules", headers=admin_headers)
    applied = yaml.safe_load((get_settings().monitoring_rules_dir / "platform.yml").read_text())
    assert applied["groups"][0]["rules"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"metric": "prediction_risk", "threshold": 1},
        {"metric": "prediction_anomaly", "threshold": 0, "prediction_horizon_hours": 1},
        {"metric": "prediction_risk", "threshold": 2, "prediction_horizon_hours": 1},
    ],
)
def test_prediction_rule_validation(client, admin_headers, task_id, body):
    assert (
        client.post(
            "/api/v1/alert-rules",
            headers=admin_headers,
            json={"code": "Invalid", "prediction_task_id": task_id, **body},
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "positions,reason",
    [
        ([-1], "latest_bin_missing"),
        ([20, 21, 22], "usable_tail"),
        (list(range(20)), "too_many_missing_samples"),
    ],
)
def test_missing_data(positions, reason):
    data = samples(np.ones(120))
    for i in positions:
        data[i][1] = "NaN"
    _, quality = prepare_series(data, END)
    # Leading absent samples start the actual history, rather than creating fake coverage.
    if positions == list(range(20)):
        assert quality["samples"] == 100
    elif reason == "usable_tail":
        assert quality["status"] == "usable" and quality["samples"] == 97
        assert quality["excluded_gaps"][0]["bins"] == 3
    else:
        assert quality["reason"] == reason


def test_short_gaps_interpolated_and_never_zero():
    data = samples(np.linspace(30, 50, 120))
    data[10][1] = None
    data[11][1] = "+Inf"
    (times, values, observed), quality = prepare_series(data, END)
    assert quality["status"] == "usable" and not observed[10]
    assert 30 < values[10] < 50
    assert prepare_series([], END)[1]["reason"] == "no_samples"
    for i in range(0, 120, 5):
        data[i][1] = "NaN"
    assert prepare_series(data, END)[1]["reason"] == "too_many_missing_samples"


def test_cold_start_and_zero_series():
    cold = compute(samples(np.zeros(72)), END, CONFIG)
    assert cold["horizons"]["1"]["quality"] == "provisional"
    assert cold["horizons"]["6"]["quality"] == "insufficient_data"
    assert cold["horizons"]["1"]["risk"]["level"] == "unknown"
    assert json.dumps(cold, allow_nan=False)
    constant = compute(samples(np.zeros(120)), END, CONFIG)
    assert constant["horizons"]["1"]["quality"] == "validated"
    assert constant["detector_state"]["scale"] > 0


@pytest.mark.parametrize(
    "metric,value,ceiling",
    [
        ("host_cpu_percent", 120, 100),
        ("container_cpu_percent", 250, 250),
        ("llm_error_rate", 1.2, 1),
    ],
)
def test_forecast_bounds(metric, value, ceiling):
    (mean, lo, hi), _ = forecast(np.full(100, value), "naive", 12, metric)
    assert mean.max() == ceiling and lo.min() >= 0 and hi.max() == ceiling


@pytest.mark.parametrize("weekly", [False, True])
def test_periodic_forecast_beats_straight_line(weekly):
    rng = np.random.default_rng(20261002)
    n = 8064 if weekly else 2016
    t = np.arange(n + 288)
    y = 40 + 15 * np.sin(2 * np.pi * t / 288) + 0.001 * t
    if weekly:
        y += 10 * np.cos(2 * np.pi * t / 2016)
    y += rng.normal(0, 0.25, len(t))
    name = "mstl_daily_weekly" if weekly else "mstl_daily"
    (predicted, _, _), _ = forecast(y[:n], name, 288, "host_cpu_percent")
    coefficients = np.polyfit(t[n - 288 : n], y[n - 288 : n], 1)
    linear = np.polyval(coefficients, t[n:])
    assert np.mean(np.abs(predicted - y[n:])) < 0.5 * np.mean(np.abs(linear - y[n:]))


def test_backtest_never_observes_future(monkeypatch):
    seen = []
    real = forecast

    def checked(values, name, steps, metric):
        seen.append(len(values))
        return real(values, name, steps, metric)

    monkeypatch.setattr("app.services.predictions.engine.forecast", checked)
    result = backtest(np.linspace(30, 35, 120), np.ones(120, bool), 1, "host_cpu_percent")
    assert result["status"] == "validated"
    assert set(seen) == {84, 96, 108}
    assert "mae" in result["scores"][result["selected_model"]]


def detector(previous_timestamp=END):
    return {
        "detector_state": {"center": 0, "scale": 1, "last_timestamp": previous_timestamp},
        "horizons": {
            "1": {"points": [{"timestamp": END + i * 300, "value": 50} for i in range(1, 13)]}
        },
    }


def test_normal_periodic_peak_spike_and_persisted_drift():
    old = detector()
    assert detect_anomaly(END + 300, 50, old)[0] == []
    spike, state = detect_anomaly(END + 300, 60, old)
    assert spike[0]["kind"] == "spike"
    assert detect_anomaly(END + 300, 60, {**old, "detector_state": state})[0] == []
    assert detect_anomaly(END + 300, 60, old, observed=False)[0] == []
    old = detector()
    for i in range(1, 8):
        anomalies, state = detect_anomaly(END + i * 300, 53, old)
        old["detector_state"] = json.loads(json.dumps(state))
    assert any(item["kind"] == "drift" for item in anomalies)


def test_fifteen_minute_run_detects_intermediate_spike_and_accumulates_drift():
    spike = compute(samples([50, 60, 50], END + 900), END + 900, CONFIG, detector())
    assert any(a["timestamp"] == END + 600 and a["kind"] == "spike" for a in spike["anomalies"])
    assert not any(a["timestamp"] == END + 900 for a in spike["anomalies"])
    assert spike["detector_state"]["count"] == 3
    drift = compute(samples([53.2, 53.2, 53.2], END + 900), END + 900, CONFIG, detector())
    assert any(a["timestamp"] == END + 900 and a["kind"] == "drift" for a in drift["anomalies"])
    assert drift["detector_state"]["streak"] == 3
    previous = detector()
    previous["detector_state"]["last_actual"] = 60
    previous["anomalies"] = [{"timestamp": END, "kind": "spike"}]
    fresh = compute(samples([60, 50, 50, 50], END + 900), END + 900, CONFIG, previous)
    assert fresh["anomalies"] == []


def test_old_long_gap_retains_twenty_seven_days_of_continuous_history():
    points = samples(np.full(8064, 30))
    for index in (290, 291, 292):
        points[index][1] = "NaN"
    (times, values, observed), quality = prepare_series(points, END)
    assert quality["status"] == "usable" and len(times) == 8064 - 293
    assert quality["history_hours"] > 24 * 26 and observed.all()
    assert times[0] == points[293][0] and values.min() == 30
    assert quality["excluded_gaps"] == [{"start": points[290][0], "end": points[292][0], "bins": 3}]


def test_collection_switch_invalidates_forecast_but_rename_does_not(
    client, admin_headers, task_id, server_id, server_body
):
    from app.services.predictions.tasks import is_fresh

    identifier = finished_run(task_id)
    with SessionLocal() as db:
        old = snapshot(db, db.get(PredictionTask, UUID(task_id)))
    renamed = {**server_body, "name": "Renamed"}
    assert (
        client.put("/api/v1/servers/" + server_id, headers=admin_headers, json=renamed).status_code
        == 200
    )
    with SessionLocal() as db:
        task = db.get(PredictionTask, UUID(task_id))
        assert snapshot(db, task) == old
        assert is_fresh(db, db.get(PredictionResult, UUID(identifier)), task)
    assert (
        client.put(
            "/api/v1/servers/" + server_id,
            headers=admin_headers,
            json={**renamed, "node_exporter_target": "replacement:9100"},
        ).status_code
        == 200
    )
    with SessionLocal() as db:
        task = db.get(PredictionTask, UUID(task_id))
        assert snapshot(db, task)["source_epoch"] != old["source_epoch"]
        assert not is_fresh(db, db.get(PredictionResult, UUID(identifier)), task)


def test_risk_first_breach_and_untrusted_interval():
    curve = [
        {"timestamp": END, "value": 70, "upper": 85},
        {"timestamp": END + 300, "value": 90, "upper": 95},
    ]
    assert risk(curve, 80, "validated") == {"level": "expected", "first_breach_at": END + 300}
    assert risk(curve[:1], 80, "validated")["level"] == "possible"
    assert risk(curve[:1], 80, "interval_low_confidence")["level"] == "normal"
    assert risk(curve, 80, "provisional")["level"] == "unknown"


def test_worker_subprocess_timeout_and_no_api_block(client, monkeypatch):
    import subprocess

    from app.workers.predictions import calculate

    def slow(*a, **k):
        time.sleep(0.15)
        raise subprocess.TimeoutExpired("python", 1)

    monkeypatch.setattr("app.workers.predictions.subprocess.run", slow)
    with ThreadPoolExecutor(max_workers=1) as executor:
        job = executor.submit(calculate, {}, 1)
        assert client.get("/health/live").status_code == 200
        assert job.result() == (None, "calculation_timeout")


def test_real_worker_child_calculation():
    from app.workers.predictions import calculate

    answer, error = calculate(
        {"points": samples(np.full(120, 20)), "end": END, "config": CONFIG}, timeout=60
    )
    assert error is None and answer["horizons"]["1"]["quality"] == "validated"


def test_real_child_is_killed_at_deadline():
    from app.workers.predictions import calculate

    before = time.monotonic()
    answer, error = calculate(
        {"points": samples(np.full(120, 20)), "end": END, "config": CONFIG}, timeout=0.05
    )
    assert answer is None and error == "calculation_timeout"
    assert time.monotonic() - before < 5


def test_sparse_llm_probe_is_unknown():
    answer = compute([], END, {**CONFIG, "probe_interval_seconds": 600})
    assert answer["quality"]["reason"] == "probe_interval_too_large"
    assert all(h["risk"]["level"] == "unknown" for h in answer["horizons"].values())


def test_pending_run_keeps_previous_fresh_metrics(client, task_id):
    finished_run(task_id)
    with SessionLocal() as db:
        enqueue(db, db.get(PredictionTask, UUID(task_id)))
    exported = client.get("/api/v1/monitoring/prediction-metrics", headers=SERVICE_HEADERS).text
    assert 'horizon_hours="1"' in exported


def test_historical_spike_is_saved_but_does_not_keep_live_alarm(client, task_id):
    identifier = finished_run(task_id)
    with SessionLocal() as db:
        row = db.get(PredictionResult, UUID(identifier))
        end = int(row.data_end_at.timestamp())
        row.anomalies = [{"timestamp": end - 300, "kind": "spike"}]
        db.commit()
    exported = client.get("/api/v1/monitoring/prediction-metrics", headers=SERVICE_HEADERS).text
    lines = [line for line in exported.splitlines() if line.startswith("prediction_anomaly{")]
    assert len(lines) == 1 and lines[0].endswith(" 0.0")
    with SessionLocal() as db:
        row = db.get(PredictionResult, UUID(identifier))
        assert row.anomalies[0]["timestamp"] == end - 300
        row.anomalies = [{"timestamp": end, "kind": "spike"}]
        db.commit()
    exported = client.get("/api/v1/monitoring/prediction-metrics", headers=SERVICE_HEADERS).text
    assert next(
        line for line in exported.splitlines() if line.startswith("prediction_anomaly{")
    ).endswith(" 1.0")


def test_worker_reports_prometheus_error(task_id, monkeypatch):
    from app.workers.predictions import run_once

    with SessionLocal() as db:
        run_id = enqueue(db, db.get(PredictionTask, UUID(task_id))).id

    def unavailable(*args, **kwargs):
        raise HTTPException(502, "Prometheus request failed")

    monkeypatch.setattr("app.workers.predictions.load_history", unavailable)
    assert run_once() is True
    with SessionLocal() as db:
        run = db.get(PredictionResult, run_id)
        assert run.status == "failed" and run.error_code == "prometheus_unavailable"


def test_restart_result_cannot_be_overwritten_by_old_worker(task_id):
    with SessionLocal() as db:
        enqueue(db, db.get(PredictionTask, UUID(task_id)))
        run = claim(db)
        interrupt_runs(db)
        result = complete(db, run.id, compute(samples(np.ones(120)), END, CONFIG))
        assert result.status == "interrupted" and not result.horizons


def test_backtest_cache_reused_between_daily_runs(monkeypatch):
    first = compute(samples(np.full(120, 30)), END, CONFIG)
    monkeypatch.setattr(
        "app.services.predictions.engine.backtest", lambda *a, **k: pytest.fail("unexpected retrain")
    )
    second = compute(samples(np.full(121, 30), END + 300), END + 300, CONFIG, first)
    assert second["backtest"]["1"] == first["backtest"]["1"]


def test_repeated_closed_bin_preserves_alarm_without_advancing_ewma():
    previous = detector()
    anomalies, state = detect_anomaly(END + 300, 60, previous)
    previous.update(anomalies=anomalies, detector_state=state)
    again, next_state = detect_anomaly(END + 300, 60, previous)
    assert again == anomalies and next_state["count"] == state["count"]


def test_long_interval_uses_matching_forecast_baseline():
    previous = {
        "detector_state": {"baselines": {"6": {"center": 0, "scale": 1}}},
        "horizons": {"6": {"points": [{"timestamp": END, "value": 30}]}},
    }
    anomalies, _ = detect_anomaly(END, 50, previous)
    assert anomalies[0]["kind"] == "spike"


def test_actual_periodic_peak_is_not_anomaly():
    from app.services.predictions.engine import residual_baseline

    rng = np.random.default_rng(20261002)
    count = 2076
    t = np.arange(count + 12)
    values = 40 + 15 * np.sin(2 * np.pi * t / 288) + rng.normal(0, 0.25, len(t))
    (mean, _, _), fitted = forecast(values[:count], "mstl_daily", 12, "host_cpu_percent")
    center, scale = residual_baseline(fitted, values[:count])
    previous = {
        "detector_state": {"center": center, "scale": scale},
        "horizons": {
            "1": {
                "points": [
                    {"timestamp": END + (i + 1) * 300, "value": float(mean[i])} for i in range(12)
                ]
            }
        },
    }
    assert values[count] > 54
    anomalies, _ = detect_anomaly(END + 300, float(values[count]), previous)
    assert anomalies == []
