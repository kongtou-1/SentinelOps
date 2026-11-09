import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select
from test_resources_alerts import SERVICE_HEADERS
from test_resources_alerts import isolated_secrets as isolated_secrets
from test_resources_alerts import server_body as server_body
from test_resources_alerts import server_id as server_id

from app.db.session import SessionLocal
from app.models import (
    AlertEvent,
    AlertRule,
    Incident,
    IncidentAlert,
    LLMEndpoint,
    PredictionResult,
    PredictionTask,
    Server,
)
from app.services.monitoring.config import rule_config
from app.services.monitoring.recovery import reconcile
from app.services.reports.logic import snapshot


@pytest.fixture
def occurrence(client, admin_headers, server_id):
    rule = client.post(
        "/api/v1/alert-rules",
        headers=admin_headers,
        json={
            "code": "RecoveryCPU",
            "metric": "host_cpu_percent",
            "threshold": 80,
            "server_id": server_id,
        },
    ).json()
    now = datetime.now(UTC)
    with SessionLocal() as db:
        server = db.get(Server, UUID(server_id))
        frozen = {
            **rule_config(db.get(AlertRule, UUID(rule["id"]))),
            "source_epoch": server.collection_config_changed_at.isoformat(),
        }
    item = {
        "fingerprint": "aabb11",
        "status": "firing",
        "startsAt": (now - timedelta(minutes=1)).isoformat(),
        "endsAt": "0001-01-01T00:00:00Z",
        "labels": {
            "alertname": "RecoveryCPU",
            "server_id": server_id,
            "rule_id": rule["id"],
            "source_epoch": frozen["source_epoch"],
        },
        "annotations": {"platform_rule_snapshot": json.dumps(frozen)},
    }

    def send(status):
        response = client.post(
            "/api/v1/hooks/alertmanager",
            headers=SERVICE_HEADERS,
            json={
                "alerts": [
                    {
                        **item,
                        "status": status,
                        "endsAt": (now - timedelta(seconds=10)).isoformat()
                        if status == "resolved"
                        else item["endsAt"],
                    }
                ]
            },
        )
        assert response.status_code == 200, response.text

    send("firing")
    send("resolved")
    event = client.get("/api/v1/alerts", headers=admin_headers).json()[0]
    return event, rule, item, now


def normal_query(occurrence, metric_value=30):
    event, _, _, now = occurrence

    def query(expression):
        value = now.timestamp() if expression.startswith("timestamp(") else metric_value
        return [
            {
                "metric": {
                    "server_id": event["server_id"],
                    "source_epoch": event["rule_snapshot"]["source_epoch"],
                },
                "value": [now.timestamp(), str(value)],
            }
        ]

    return query


def check():
    with SessionLocal() as db:
        return reconcile(db)


def test_missing_signal_then_fresh_normal_evidence(client, admin_headers, occurrence, monkeypatch):
    event, _, _, now = occurrence
    base = "/api/v1/incidents/" + event["incident_id"]
    assert client.get(base, headers=admin_headers).json()["status"] == "open"
    monkeypatch.setattr("app.services.monitoring.recovery.query", lambda _: [])
    check()
    pending = client.get("/api/v1/alerts/" + event["id"], headers=admin_headers).json()
    assert pending["status"] == "resolved" and pending["recovery_status"] == "pending"
    assert (
        client.post(
            base + "/status",
            headers=admin_headers,
            json={"status": "closed", "comment": "通知结束"},
        ).status_code
        == 409
    )
    monkeypatch.setattr("app.services.monitoring.recovery.query", normal_query(occurrence))
    assert check() == 1
    recovered = client.get(base, headers=admin_headers).json()
    assert recovered["status"] == "recovered"
    assert datetime.fromisoformat(recovered["recovered_at"]) == now
    assert datetime.fromisoformat(event["ends_at"]) < now
    assert check() == 0


@pytest.mark.parametrize("value", [90, "NaN", "+Inf"])
def test_non_normal_sample_cannot_recover(client, admin_headers, occurrence, monkeypatch, value):
    monkeypatch.setattr("app.services.monitoring.recovery.query", normal_query(occurrence, value))
    check()
    row = client.get(
        "/api/v1/incidents/" + occurrence[0]["incident_id"], headers=admin_headers
    ).json()
    assert row["status"] == "open" and row["recovered_at"] is None


@pytest.mark.parametrize(
    "change,reason",
    [
        ("rule_disabled", "rule_disabled_or_deleted"),
        ("rule_changed", "rule_changed"),
        ("retired", "resource_retired"),
        ("monitoring_disabled", "monitoring_disabled"),
        ("target_changed", "collection_changed"),
    ],
)
def test_termination_requires_human_comment_and_never_claims_recovery(
    client, admin_headers, occurrence, change, reason, monkeypatch
):
    event, rule, _, _ = occurrence
    with SessionLocal() as db:
        server = db.get(Server, UUID(event["server_id"]))
        row = db.get(AlertRule, UUID(rule["id"]))
        if change == "rule_disabled":
            row.enabled = False
        elif change == "rule_changed":
            row.threshold = 70
        elif change == "retired":
            server.deleted_at = datetime.now(UTC)
        elif change == "monitoring_disabled":
            server.monitoring_enabled = False
        else:
            server.collection_config_changed_at = datetime.now(UTC)
        db.commit()
    monkeypatch.setattr(
        "app.services.monitoring.recovery.query", lambda _: pytest.fail("Terminated source must not be queried")
    )
    check()
    event = client.get("/api/v1/alerts/" + event["id"], headers=admin_headers).json()
    assert event["recovery_status"] == "terminated" and event["recovery_reason"] == reason
    base = "/api/v1/incidents/" + event["incident_id"]
    assert client.get(base, headers=admin_headers).json()["status"] == "open"
    result = client.post(
        base + "/status",
        headers=admin_headers,
        json={"status": "closed", "comment": "规则停用或资源退役，人工确认终止，未验证恢复"},
    )
    assert result.status_code == 200 and result.json()["recovered_at"] is None
    with SessionLocal() as db:
        evidence = snapshot(db, db.get(Incident, UUID(event["incident_id"])))
        assert any("未通过资源恢复核验" in gap for gap in evidence["data_gaps"])
        alert = next(r["data"] for r in evidence["records"] if r["kind"] == "alert")
        assert alert["recovery_status"] == "terminated" and alert["recovery_evidence_at"] is None


def test_old_unverified_alert_is_not_backfilled(occurrence, monkeypatch):
    with SessionLocal() as db:
        row = db.get(AlertEvent, UUID(occurrence[0]["id"]))
        row.rule_snapshot = {}
        db.commit()
    monkeypatch.setattr("app.services.monitoring.recovery.query", lambda _: pytest.fail("No trustworthy snapshot"))
    check()
    with SessionLocal() as db:
        row = db.get(AlertEvent, UUID(occurrence[0]["id"]))
        assert row.recovery_status == "pending" and row.recovery_evidence_at is None


def test_source_changes_during_evidence_request(occurrence, monkeypatch):
    normal = normal_query(occurrence)

    def changing(expression):
        if not expression.startswith("timestamp("):
            with SessionLocal() as db:
                db.get(
                    Server, UUID(occurrence[0]["server_id"])
                ).collection_config_changed_at = datetime.now(UTC)
                db.commit()
        return normal(expression)

    monkeypatch.setattr("app.services.monitoring.recovery.query", changing)
    check()
    with SessionLocal() as db:
        assert db.get(AlertEvent, UUID(occurrence[0]["id"])).recovery_status == "terminated"
        assert db.scalar(select(Incident)).recovered_at is None


def test_normal_other_asset_is_not_evidence(occurrence, monkeypatch):
    normal = normal_query(occurrence)

    def wrong(expression):
        result = normal(expression)
        if not expression.startswith("timestamp("):
            result[0]["metric"]["server_id"] = "another-asset"
        return result

    monkeypatch.setattr("app.services.monitoring.recovery.query", wrong)
    check()
    with SessionLocal() as db:
        assert db.get(AlertEvent, UUID(occurrence[0]["id"])).recovery_status == "pending"


def test_merging_pending_recovery_reopens_verified_target(
    client, admin_headers, occurrence, monkeypatch
):
    monkeypatch.setattr("app.services.monitoring.recovery.query", normal_query(occurrence))
    check()
    target = occurrence[0]["incident_id"]
    now = datetime.now(UTC)
    with SessionLocal() as db:
        peer = Incident(
            title="Unknown recovery",
            severity="warning",
            status="open",
            started_at=now - timedelta(minutes=1),
            last_alert_started_at=now - timedelta(minutes=1),
        )
        event = AlertEvent(
            fingerprint="dd11",
            severity="warning",
            status="resolved",
            starts_at=peer.started_at,
            ends_at=now,
            labels={},
            annotations={},
        )
        db.add_all([peer, event])
        db.flush()
        db.add(IncidentAlert(incident_id=peer.id, alert_event_id=event.id))
        db.commit()
        source = str(peer.id)
    result = client.post(
        f"/api/v1/incidents/{source}/merge",
        headers=admin_headers,
        json={"target_incident_id": target, "comment": "有关联告警，但恢复仍待核验"},
    )
    assert result.status_code == 200 and result.json()["status"] == "open"
    assert result.json()["recovered_at"] is None


def test_legacy_retirement_can_be_closed_without_backfilling_success(occurrence):
    with SessionLocal() as db:
        event = db.get(AlertEvent, UUID(occurrence[0]["id"]))
        event.rule_snapshot = {}
        db.get(Server, event.server_id).deleted_at = datetime.now(UTC)
        db.commit()
    check()
    with SessionLocal() as db:
        event = db.get(AlertEvent, UUID(occurrence[0]["id"]))
        assert event.recovery_status == "terminated"
        assert event.recovery_evidence_at is None


def test_unbound_legacy_notifications_do_not_delay_verifiable_recovery(occurrence, monkeypatch):
    now = datetime.now(UTC)
    with SessionLocal() as db:
        for index in range(20):
            incident = Incident(
                title="Legacy unbound",
                severity="warning",
                status="open",
                started_at=now - timedelta(days=1),
                last_alert_started_at=now - timedelta(days=1),
            )
            alert = AlertEvent(
                fingerprint=f"{index:04x}",
                severity="warning",
                status="resolved",
                starts_at=incident.started_at,
                ends_at=now - timedelta(hours=1),
                labels={},
                annotations={},
            )
            db.add_all([incident, alert])
            db.flush()
            db.add(IncidentAlert(incident_id=incident.id, alert_event_id=alert.id))
        db.commit()
    monkeypatch.setattr("app.services.monitoring.recovery.query", normal_query(occurrence))
    with SessionLocal() as db:
        assert reconcile(db, limit=1) == 1
        assert db.get(AlertEvent, UUID(occurrence[0]["id"])).recovery_status == "verified"
        assert (
            len(db.scalars(select(AlertEvent).where(AlertEvent.recovery_status == "pending")).all())
            == 20
        )


@pytest.mark.parametrize("state", ["stale", "failed", "healthy"])
def test_llm_notification_end_requires_a_new_successful_probe(occurrence, monkeypatch, state):
    event, rule, _, now = occurrence
    with SessionLocal() as db:
        endpoint = LLMEndpoint(
            name="recovery-llm",
            base_url="http://local",
            model="test",
            encrypted_api_key="encrypted",
            last_probe_at=now - timedelta(seconds=600 if state == "stale" else 1),
            last_probe_success=state == "healthy",
        )
        db.add(endpoint)
        db.flush()
        row = db.get(AlertRule, UUID(rule["id"]))
        row.server_id, row.llm_endpoint_id, row.metric, row.threshold = (
            None,
            endpoint.id,
            "llm_authentication_failed",
            0,
        )
        alert = db.get(AlertEvent, UUID(event["id"]))
        alert.server_id, alert.llm_endpoint_id = None, endpoint.id
        alert.labels = {
            "endpoint_id": str(endpoint.id),
            "source_epoch": endpoint.probe_config_changed_at.isoformat(),
        }
        alert.rule_snapshot = {
            **rule_config(row),
            "source_epoch": endpoint.probe_config_changed_at.isoformat(),
        }
        labels = dict(alert.labels)
        db.commit()
    monkeypatch.setattr(
        "app.services.monitoring.recovery.query", lambda _: [{"metric": labels, "value": [now.timestamp(), "0"]}]
    )
    check()
    with SessionLocal() as db:
        row = db.get(AlertEvent, UUID(event["id"]))
        assert row.recovery_status == ("verified" if state == "healthy" else "pending")
        assert (db.scalar(select(Incident)).recovered_at is not None) == (state == "healthy")


def test_prediction_unknown_is_not_recovery_and_configuration_change_terminates(occurrence):
    from app.services.predictions.tasks import snapshot as prediction_snapshot

    event, rule, _, now = occurrence
    end = datetime.fromtimestamp(int(now.timestamp()) // 300 * 300, UTC)
    with SessionLocal() as db:
        db.get(Server, UUID(event["server_id"])).collection_config_changed_at = now - timedelta(
            days=1
        )
        task = PredictionTask(
            name="recovery-prediction",
            server_id=UUID(event["server_id"]),
            metric="host_cpu_percent",
            selector={},
            threshold=80,
            enabled=True,
        )
        db.add(task)
        db.flush()
        config = prediction_snapshot(db, task)
        result = PredictionResult(
            task_id=task.id,
            status="completed",
            config=config,
            finished_at=now,
            data_end_at=end,
            quality={"status": "usable"},
            horizons={"1": {"quality": "provisional", "risk": {"level": "unknown"}}},
        )
        db.add(result)
        row = db.get(AlertRule, UUID(rule["id"]))
        (
            row.server_id,
            row.prediction_task_id,
            row.prediction_horizon_hours,
            row.metric,
            row.threshold,
        ) = None, task.id, 1, "prediction_risk", 0
        alert = db.get(AlertEvent, UUID(event["id"]))
        alert.prediction_task_id, alert.ends_at = task.id, end - timedelta(seconds=5)
        alert.rule_snapshot = {
            **rule_config(row),
            "source_epoch": config["source_epoch"],
            "prediction_config": config,
        }
        db.commit()
        task_id = task.id
    check()
    with SessionLocal() as db:
        assert db.get(AlertEvent, UUID(event["id"])).recovery_status == "pending"
        assert db.scalar(select(Incident)).recovered_at is None
        db.get(PredictionTask, task_id).threshold = 99
        db.commit()
    check()
    with SessionLocal() as db:
        row = db.get(AlertEvent, UUID(event["id"]))
        assert (
            row.recovery_status == "terminated"
            and row.recovery_reason == "prediction_configuration_changed"
        )


@pytest.mark.parametrize(
    "poll_age,value,expected",
    [(120, "0", "pending"), (0, None, "pending"), (0, "1", "pending"), (0, "0", "verified")],
)
def test_health_recovery_requires_fresh_conclusive_normal_evidence(
    client, admin_headers, occurrence, monkeypatch, poll_age, value, expected
):
    event, rule, _, now = occurrence
    with SessionLocal() as db:
        r = db.get(AlertRule, UUID(rule["id"]))
        r.metric, r.threshold = "container_unhealthy", 0
        e = db.get(AlertEvent, UUID(event["id"]))
        e.rule_snapshot = {**e.rule_snapshot, "metric": r.metric, "threshold": 0}
        db.commit()

    def query(expression):
        if expression.startswith("timestamp("):
            assert 'job="docker-state"' in expression
            number = now.timestamp()
        elif expression.startswith("docker_state_last_poll"):
            number = now.timestamp() - poll_age
        else:
            assert "docker_container_unhealthy" in expression
            if value is None:
                return []
            number = value
        return [
            {
                "metric": {
                    "server_id": event["server_id"],
                    "source_epoch": event["rule_snapshot"]["source_epoch"],
                },
                "value": [now.timestamp(), str(number)],
            }
        ]

    monkeypatch.setattr("app.services.monitoring.recovery.query", query)
    check()
    updated = client.get("/api/v1/alerts/" + event["id"], headers=admin_headers).json()
    assert updated["recovery_status"] == expected


def test_new_unchecked_occurrence_is_not_delayed_by_bound_historical_backlog(
    occurrence, monkeypatch
):
    event, rule, _, now = occurrence
    with SessionLocal() as db:
        old = AlertEvent(
            fingerprint="older-bound",
            severity="warning",
            status="resolved",
            starts_at=now - timedelta(days=1),
            ends_at=now - timedelta(hours=1),
            server_id=UUID(event["server_id"]),
            rule_id=UUID(rule["id"]),
            labels=event["labels"],
            annotations={},
            rule_snapshot=event["rule_snapshot"],
        )
        db.add(old)
        db.flush()
        db.add(IncidentAlert(incident_id=UUID(event["incident_id"]), alert_event_id=old.id))
        db.commit()
        old_id = old.id
    monkeypatch.setattr("app.services.monitoring.recovery.query", normal_query(occurrence))
    with SessionLocal() as db:
        assert reconcile(db, limit=1) == 1
        assert db.get(AlertEvent, UUID(event["id"])).recovery_status == "verified"
        assert db.get(AlertEvent, old_id).recovery_checked_at is None
        assert reconcile(db, limit=1) == 1
        assert db.get(AlertEvent, old_id).recovery_status == "verified"


def test_recent_missing_evidence_retry_and_historical_backlog_both_progress(
    occurrence, monkeypatch
):
    event, rule, _, now = occurrence
    with SessionLocal() as db:
        current = db.get(AlertEvent, UUID(event["id"]))
        current.recovery_checked_at = now
        current.recovery_reason = "collector_unavailable"
        old_ids = []
        for i in range(4):
            old = AlertEvent(
                fingerprint=f"backlog-{i}",
                severity="warning",
                status="resolved",
                starts_at=now - timedelta(days=1, minutes=i),
                ends_at=now - timedelta(hours=1),
                server_id=UUID(event["server_id"]),
                rule_id=UUID(rule["id"]),
                labels=event["labels"],
                annotations={},
                rule_snapshot=event["rule_snapshot"],
            )
            db.add(old)
            db.flush()
            db.add(IncidentAlert(incident_id=UUID(event["incident_id"]), alert_event_id=old.id))
            old_ids.append(old.id)
        db.commit()
    monkeypatch.setattr("app.services.monitoring.recovery.query", normal_query(occurrence))
    with SessionLocal() as db:
        assert reconcile(db, limit=2) == 2
        assert db.get(AlertEvent, UUID(event["id"])).recovery_status == "verified"
        assert sum(db.get(AlertEvent, i).recovery_status == "verified" for i in old_ids) == 1
