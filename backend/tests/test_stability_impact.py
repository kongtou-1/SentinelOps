from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

from app.db.session import SessionLocal
from app.models import AlertEvent, Application, Incident, IncidentAlert, ServiceMonitor
from app.services.operations import impact, stability


def test_graph_cycles_depth_and_total_limits():
    rows = [SimpleNamespace(id=str(i), depends_on=[str((i + 1) % 8)]) for i in range(8)]
    nodes, upstream, downstream = impact.graph(rows)
    values, truncated = impact.walk("0", upstream)
    assert values == [("1", 1), ("2", 2), ("3", 3)] and truncated
    values, truncated = impact.walk("0", {"0": {str(i) for i in range(1, 8)}}, remaining=2)
    assert len(values) == 2 and truncated
    assert impact.walk("0", {"0": {"1"}, "1": {"0"}}) == ([("1", 1)], False)
    apps = [SimpleNamespace(id="a", depends_on=[]), SimpleNamespace(id="b", depends_on=[])]
    containers = [
        SimpleNamespace(id="one", application_id="a", depends_on=["two"]),
        SimpleNamespace(id="two", application_id="b", depends_on=[]),
    ]
    assert impact.graph(apps, containers)[1]["a"] == {"b"}


def test_stability_distinct_observations_failure_and_history():
    at = datetime.now(UTC)
    event = SimpleNamespace(
        rule_snapshot={"stability_seconds": 180},
        stability_started_at=None,
        stability_last_evidence_at=None,
    )
    assert stability.advance(event, "verified", "normal", at)[0] == "pending"
    assert stability.advance(event, "verified", "normal", at)[1] == "stability_waiting_new_sample"
    assert (
        stability.advance(event, "verified", "normal", at + timedelta(seconds=90))[0] == "pending"
    )
    assert (
        stability.advance(event, "verified", "normal", at + timedelta(seconds=180), False)[0]
        == "pending"
    )
    assert event.stability_started_at == at + timedelta(seconds=180)
    assert stability.advance(event, "pending", "missing", None)[0] == "pending"
    assert event.stability_started_at is None
    stability.advance(event, "verified", "normal", at)
    stability.advance(event, "verified", "normal", at + timedelta(seconds=90))
    assert (
        stability.advance(event, "verified", "normal", at + timedelta(seconds=180), True)[0]
        == "verified"
    )
    event.rule_snapshot = {}
    assert stability.advance(event, "verified", "legacy", at) == ("verified", "legacy", at)


def test_window_history_rejects_gap_or_spike(monkeypatch):
    now = datetime.now(UTC)
    event = SimpleNamespace(
        service_monitor_id=None,
        prediction_task_id=None,
        llm_endpoint_id=None,
        server_id=uuid4(),
        labels={},
    )
    event.rule_snapshot = {"source_epoch": now.isoformat()}
    rule = SimpleNamespace(metric="host_cpu_percent", threshold=80)
    points = [[now.timestamp() - (180 - i), "20"] for i in range(181)]

    def history(path, parameters, **kwargs):
        assert parameters["step"] == 1
        return {"result": [{"metric": {}, "values": points}]}

    monkeypatch.setattr(stability, "prometheus", history)
    assert stability.window_normal(None, event, rule, now, 180)
    points[5][1] = "90"
    assert not stability.window_normal(None, event, rule, now, 180)
    points.pop(5)
    assert not stability.window_normal(None, event, rule, now, 180)


def test_policy_version_and_impact_current_observation(client, admin_headers):
    now = datetime.now(UTC)
    with SessionLocal() as db:
        first = Application(id=uuid4(), code="impact-first", name="First", depends_on=[])
        second = Application(
            id=uuid4(), code="impact-second", name="Second", depends_on=[str(first.id)]
        )
        db.add_all([first, second])
        db.flush()
        db.add(
            ServiceMonitor(
                application_id=first.id,
                name="Health",
                protocol="http",
                target="http://business/health",
                method="GET",
                route="/health",
                dns_server="",
                importance="critical",
                expected_status=200,
                interval_seconds=60,
                timeout_seconds=5,
                last_probe_at=now,
                last_probe_success=False,
            )
        )
        db.commit()
        first_id, second_id = first.id, second.id
    path = f"/api/v1/business-services/{first_id}/operations-policy"
    assert client.get(path, headers=admin_headers).json()["version"] == 0
    body = {"expected_version": 0, "recovery_seconds": 300}
    assert client.put(path, json=body, headers=admin_headers).json()["version"] == 1
    assert client.put(path, json=body, headers=admin_headers).status_code == 409
    observed = client.get(
        f"/api/v1/business-services/{second_id}/impact", headers=admin_headers
    ).json()
    assert observed["upstream"][0]["id"] == str(first_id)
    assert observed["upstream"][0]["impact"] == "observed_failure"


def test_recurrence_candidate_is_idempotent_and_preserves_closed_incident(client, admin_headers):
    now = datetime.now(UTC)
    with SessionLocal() as db:
        application = Application(id=uuid4(), code="recurrence", name="Service")
        monitor = ServiceMonitor(
            id=uuid4(),
            application_id=application.id,
            name="Health",
            protocol="http",
            target="http://business/health",
        )
        db.add(application)
        db.flush()
        db.add(monitor)
        db.flush()
        old = Incident(
            id=uuid4(),
            title="Old",
            severity="warning",
            status="closed",
            started_at=now - timedelta(minutes=20),
            last_alert_started_at=now - timedelta(minutes=20),
            recovered_at=now - timedelta(minutes=10),
            closed_at=now - timedelta(minutes=9),
        )
        current = Incident(
            id=uuid4(),
            title="New",
            severity="warning",
            status="open",
            started_at=now,
            last_alert_started_at=now,
        )
        db.add_all([old, current])
        db.flush()
        for incident in (old, current):
            event = AlertEvent(
                id=uuid4(),
                fingerprint=uuid4().hex,
                severity="warning",
                status="resolved" if incident is old else "firing",
                starts_at=incident.started_at,
                service_monitor_id=monitor.id,
                labels={},
                annotations={},
            )
            db.add(event)
            db.flush()
            db.add(IncidentAlert(incident_id=incident.id, alert_event_id=event.id))
        db.flush()
        impact.add_candidates(db, current)
        db.flush()
        impact.add_candidates(db, current)
        db.commit()
        old_id, current_id = old.id, current.id
    response = client.get(f"/api/v1/incidents/{current_id}/relations", headers=admin_headers)
    rows = response.json()
    assert len(rows) == 1 and rows[0]["kind"] == "recurrence"
    decision = client.post(
        f"/api/v1/incident-relations/{rows[0]['id']}/decision",
        headers=admin_headers,
        json={"expected_version": 1, "status": "confirmed", "comment": "same service and symptom"},
    )
    assert decision.status_code == 200, decision.text
    with SessionLocal() as db:
        assert db.get(Incident, UUID(str(old_id))).status == "closed"
        assert db.get(Incident, current_id).status == "open"
