import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import Notification, WorkerHeartbeat
from app.services.operations import platform


def test_components_do_not_change_database_readiness(client, admin_headers, monkeypatch):
    monkeypatch.setattr(platform, "probe", lambda url: {"status": "unavailable"})
    with SessionLocal() as db:
        db.add(
            WorkerHeartbeat(
                id="ai", observed_at=datetime.now(UTC) - timedelta(hours=1), state="polling"
            )
        )
        db.commit()
    response = client.get("/api/v1/system/components", headers=admin_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert next(x for x in data["workers"] if x["name"] == "ai")["status"] == "stale"
    assert data["components"]["prometheus"]["status"] == "unavailable"
    assert client.get("/health/ready").status_code == 200
    assert all(row["oldest_wait_seconds"] is None for row in data["queues"])
    assert data["backup"]["restore_target_met"] is None


def test_manual_backup_idempotency_overdue_and_measured_restore(client, admin_headers, monkeypatch):
    monkeypatch.setattr(platform, "probe", lambda url: {"status": "healthy"})
    body = {
        "kind": "backup",
        "idempotency_key": str(uuid4()),
        "artifacts": [{"name": "postgres.dump", "sha256": "a" * 64, "bytes": 1000}],
        "completed_at": (datetime.now(UTC) - timedelta(hours=25)).isoformat(),
        "duration_seconds": 120,
        "verification": "verified",
        "evidence": "Actual isolated pg_dump and checksum",
    }
    path = "/api/v1/system/backup-records"
    response = client.post(path, headers=admin_headers, json=body)
    assert response.status_code == 201, response.text
    backup = response.json()
    assert client.post(path, headers=admin_headers, json=body).json()["id"] == backup["id"]
    with SessionLocal() as db:
        platform.remind_backup(db)
        platform.remind_backup(db)
        assert (
            len(db.scalars(select(Notification).where(Notification.kind == "backup_overdue")).all())
            == 1
        )
    restore = {
        **body,
        "kind": "restore",
        "backup_id": backup["id"],
        "idempotency_key": str(uuid4()),
        "duration_seconds": 7300,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    assert client.post(path, headers=admin_headers, json=restore).status_code == 201
    status = client.get("/api/v1/system/components", headers=admin_headers).json()["backup"]
    assert (
        status["target_restore_seconds"] == 7200 and status["last_restore_duration_seconds"] == 7300
    )
    assert status["restore_target_met"] is False
    invalid = {
        **body,
        "idempotency_key": str(uuid4()),
        "artifacts": [{"name": "../key", "sha256": "b" * 64, "bytes": 1}],
    }
    assert client.post(path, headers=admin_headers, json=invalid).status_code == 422


def test_independent_watchdog_needs_no_database_and_deduplicates(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "deployment/watchdog/watchdog.py"
    spec = importlib.util.spec_from_file_location("standalone_watchdog", path)
    watchdog = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(watchdog)
    delivered = []
    monkeypatch.setattr(watchdog, "check", lambda target: "unavailable")
    monkeypatch.setattr(
        watchdog, "send", lambda config, failures: delivered.append(failures) or "unknown"
    )
    config = {
        "enabled": True,
        "targets": [{"name": "api", "url": "http://127.0.0.1:1/health/ready"}],
        "smtp": {"sender": "watchdog@example.test", "recipients": ["operator@example.test"]},
    }
    state = tmp_path / "state.json"
    assert watchdog.run_once(config, state)["delivery"] == "unknown"
    assert watchdog.run_once(config, state)["delivery"] == "unchanged"
    assert len(delivered) == 1
    monkeypatch.setattr(watchdog, "check", lambda target: "healthy")
    watchdog.run_once(config, state)
    monkeypatch.setattr(watchdog, "check", lambda target: "unavailable")
    watchdog.run_once(config, state)
    assert len(delivered) == 2
