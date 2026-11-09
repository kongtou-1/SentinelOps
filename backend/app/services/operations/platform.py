import hashlib
import json
import shutil
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx2
from fastapi import HTTPException

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import BackupRecord
from app.repositories import platform as repository
from app.repositories.notifications import admins, audit
from app.repositories.operations import heartbeat
from app.services.operations.notifications import enqueue
from app.services.pagination import page
from app.services.records import active_record
from app.utils.redaction import redact

WORKERS = {
    "inspection": 150,
    "operations": 150,
    "prediction": 150,
    "ai": 210,
    "source": 630,
    "report": 240,
    "askdata": 150,
    "execution": 130,
}


def pulse(name):
    with SessionLocal() as db:
        heartbeat(db, name, datetime.now(UTC), state="polling")
        db.commit()


def probe(url):
    try:
        with httpx2.Client(timeout=3, trust_env=False, follow_redirects=False) as client:
            response = client.get(url)
        return {
            "status": "healthy" if response.status_code == 200 else "unhealthy",
            "http_status": response.status_code,
        }
    except httpx2.HTTPError:
        return {"status": "unavailable"}


def storage():
    try:
        usage = shutil.disk_usage(get_settings().source_store_dir)
        return {
            "status": "available",
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
        }
    except OSError:
        return {"status": "unavailable"}


def backup_status(db, now=None):
    now = now or datetime.now(UTC)
    backup, restore = repository.latest_backup(db), repository.latest_restore(db)
    return {
        "target_restore_seconds": 7200,
        "backup_max_age_hours": 24,
        "backup_id": str(backup.id) if backup else None,
        "backup_completed_at": backup.completed_at if backup else None,
        "overdue": not backup or now - backup.completed_at > timedelta(hours=24),
        "last_restore_verification": restore.verification if restore else None,
        "last_restore_duration_seconds": restore.duration_seconds if restore else None,
        "restore_target_met": (
            restore.duration_seconds <= 7200 and restore.verification == "verified"
        )
        if restore and restore.duration_seconds is not None
        else None,
    }


def status(db):
    now = datetime.now(UTC)
    rows = {row.id: row for row in repository.heartbeats(db)}
    expected = set(get_settings().platform_workers.split(","))
    workers = []
    for name, maximum in WORKERS.items():
        row = rows.get(name)
        age = max(0, (now - row.observed_at).total_seconds()) if row else None
        workers.append(
            {
                "name": name,
                "expected": name in expected,
                "status": (
                    "stale" if age > maximum else "unhealthy" if row.state == "error" else "healthy"
                )
                if age is not None
                else "not_observed",
                "observed_at": row.observed_at if row else None,
                "age_seconds": age,
                "max_age_seconds": maximum,
                "state": row.state if row else None,
                "error_code": (row.detail or {}).get("error_code") if row else None,
            }
        )
    settings = get_settings()
    return {
        "checked_at": now,
        "workers": workers,
        "queues": repository.queue_status(db, now),
        "components": {
            "prometheus": probe(settings.prometheus_url.rstrip("/") + "/-/ready"),
            "alertmanager": probe(settings.alertmanager_url.rstrip("/") + "/-/ready"),
        },
        "storage": storage(),
        "database_bytes": repository.database_bytes(db),
        "backup": backup_status(db, now),
    }


def register_backup(db, body, user):
    request_hash = hashlib.sha256(
        json.dumps(body.model_dump(mode="json"), sort_keys=True).encode()
    ).hexdigest()
    old = repository.idempotent(db, body.idempotency_key)
    if old:
        if old.request_hash != request_hash or old.requested_by != user.id:
            raise HTTPException(409, "Idempotency key is already reserved")
        return old
    if body.completed_at > datetime.now(UTC) + timedelta(minutes=5):
        raise HTTPException(422, "Completion time is in the future")
    if body.backup_id:
        backup = active_record(db, BackupRecord, body.backup_id)
        if backup.kind != "backup":
            raise HTTPException(422, "Select a backup record")
    row = BackupRecord(
        **body.model_dump(exclude={"artifacts"}),
        artifacts=[item.model_dump() for item in body.artifacts],
        requested_by=user.id,
        request_hash=request_hash,
    )
    row.evidence = redact(row.evidence)
    db.add(row)
    db.flush()
    audit(
        db,
        user,
        "backup_recorded",
        "backup_record",
        row.id,
        {
            "kind": row.kind,
            "verification": row.verification,
            "duration_seconds": row.duration_seconds,
        },
    )
    db.commit()
    return row


def listing(db, response, kind=None, offset=0, limit=50):
    return page(db, repository.backups(kind), response, offset, limit)


def remind_backup(db):
    if not backup_status(db)["overdue"]:
        return
    backup = repository.latest_backup(db)
    identifier = backup.id if backup else UUID(int=0)
    enqueue(
        db,
        [user.id for user in admins(db)],
        "backup_overdue",
        "backup_record",
        identifier,
        "备份超过 24 小时" if backup else "尚无已校验备份记录",
    )
    db.commit()
