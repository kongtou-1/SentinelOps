from datetime import UTC, datetime, timedelta

from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.db.session import SessionLocal, engine
from app.models import AlertEvent, Incident, IncidentAlert, PostmortemReport, Server


def test_upgrade_preserves_closed_incident_and_approved_snapshot(
    client, make_user, migrated_database
):
    user_id = make_user()
    now = datetime.now(UTC)
    with SessionLocal() as db:
        server = Server(
            name="migration-server",
            host="127.0.0.1",
            node_exporter_target="node:9100",
            cadvisor_target="cadvisor:8080",
            created_at=now - timedelta(days=30),
        )
        incident = Incident(
            title="Historical closed",
            severity="warning",
            status="closed",
            started_at=now - timedelta(minutes=5),
            last_alert_started_at=now - timedelta(minutes=5),
            recovered_at=now - timedelta(minutes=2),
            closed_at=now - timedelta(minutes=1),
        )
        db.add_all([server, incident])
        db.flush()
        alert = AlertEvent(
            fingerprint="cc11",
            server_id=server.id,
            severity="warning",
            status="resolved",
            starts_at=incident.started_at,
            ends_at=incident.recovered_at,
            labels={},
            annotations={},
        )
        report = PostmortemReport(
            incident_id=incident.id,
            requested_by=user_id,
            status="approved",
            revision=1,
            snapshot={"immutable": "approved evidence"},
            snapshot_hash="a" * 64,
            config={},
            reviewed_by=user_id,
            reviewed_at=now,
        )
        db.add_all([alert, report])
        db.flush()
        db.add(IncidentAlert(incident_id=incident.id, alert_event_id=alert.id))
        db.commit()
        identifiers = server.id, incident.id, alert.id, report.id
        closed_times = incident.recovered_at, incident.closed_at, incident.updated_at
        report_time = report.updated_at
    try:
        command.downgrade(migrated_database, "0010_postmortems")
        assert client.get("/health/ready").status_code == 503
        command.upgrade(migrated_database, "head")
        assert client.get("/health/ready").status_code == 200
        command.check(migrated_database)
        with SessionLocal() as db:
            server, incident, alert, report = (
                db.get(model, identifier)
                for model, identifier in zip(
                    (Server, Incident, AlertEvent, PostmortemReport), identifiers, strict=True
                )
            )
            assert server.collection_config_changed_at > now
            assert alert.status == "resolved" and alert.recovery_status == "pending"
            assert alert.recovery_reason == "historical_unverified"
            assert not alert.rule_snapshot and alert.recovery_evidence_at is None
            assert incident.status == "closed"
            assert (incident.recovered_at, incident.closed_at, incident.updated_at) == closed_times
            assert report.status == "approved" and report.updated_at == report_time
            assert report.snapshot == {"immutable": "approved evidence"}
        head = ScriptDirectory.from_config(migrated_database).get_current_head()
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version")) == head
            ), head
    finally:
        command.upgrade(migrated_database, "head")
