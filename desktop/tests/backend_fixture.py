"""Isolated real-backend fixture for the desktop smoke test. Never uses a business DB."""

import json
import os
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("POSTGRES_DB", "sentinelops_desktop_test")
os.environ["LLM_PROBE_SCHEDULER_ENABLED"] = "false"
os.environ["SERVICE_PROBE_SCHEDULER_ENABLED"] = "false"

from alembic import command
from alembic.config import Config
from app.core.config import get_settings
from app.core.permissions import ROLE_PERMISSIONS
from app.core.security import password_hasher
from app.db.session import SessionLocal
from app.models import (
    AnalysisConversation,
    AnalysisEvent,
    Incident,
    Role,
    RolePermission,
    User,
)
from sqlalchemy import create_engine, select, text

settings = get_settings()
if not settings.postgres_db.endswith("_test"):
    raise SystemExit("Desktop smoke tests require a POSTGRES_DB ending in _test")

admin_engine = create_engine(
    settings.database_url.set(database="postgres"), isolation_level="AUTOCOMMIT"
)
with admin_engine.connect() as connection:
    if not connection.scalar(
        text("SELECT 1 FROM pg_database WHERE datname=:name"),
        {"name": settings.postgres_db},
    ):
        quoted = admin_engine.dialect.identifier_preparer.quote(settings.postgres_db)
        connection.execute(text(f"CREATE DATABASE {quoted}"))
admin_engine.dispose()
command.upgrade(Config(str(ROOT / "backend/alembic.ini")), "head")

password = secrets.token_urlsafe(24)
users = {}
with SessionLocal() as db:
    for code in ("admin", "operator", "viewer"):
        role = db.scalar(select(Role).where(Role.code == code))
        for permission in ROLE_PERMISSIONS[code]:
            if not db.scalar(
                select(RolePermission).where(
                    RolePermission.role_id == role.id,
                    RolePermission.permission == permission,
                )
            ):
                db.add(RolePermission(role_id=role.id, permission=permission))
        user = db.scalar(select(User).where(User.username == f"desktop_{code}"))
        if user is None:
            user = User(
                username=f"desktop_{code}",
                role_id=role.id,
                password_hash=password_hasher.hash(password),
                is_active=True,
            )
            db.add(user)
        else:
            user.password_hash = password_hasher.hash(password)
        db.flush()
        users[code] = {"username": user.username, "id": str(user.id)}
    now = datetime.now(UTC)
    incident = Incident(
        title="桌面验收：隔离事故",
        severity="warning",
        started_at=now,
        last_alert_started_at=now,
    )
    db.add(incident)
    db.flush()
    conversation = AnalysisConversation(
        incident_id=incident.id,
        owner_id=users["admin"]["id"],
        title="桌面流式验收",
        last_seq=2,
    )
    db.add(conversation)
    db.flush()
    for seq, content in enumerate(("第一段中文证据", "第二段恢复结果"), 1):
        db.add(
            AnalysisEvent(
                conversation_id=conversation.id,
                seq=seq,
                kind="assistant_delta",
                data={"text": content},
            )
        )
    db.commit()
    fixture = {
        "url": "http://127.0.0.1:18092",
        "password": password,
        "users": users,
        "incident": str(incident.id),
        "conversation": str(conversation.id),
    }

target = ROOT / ".local/desktop-acceptance/backend.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
print(
    f"Desktop fixture ready in dedicated database {settings.postgres_db}; starting loopback API on port 18092",
    flush=True,
)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=18092, log_level="warning")
