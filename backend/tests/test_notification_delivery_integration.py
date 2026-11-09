"""Opt-in local Alertmanager/Mailpit acceptance; never uses external SMTP."""

import os
from pathlib import Path
from uuid import UUID, uuid4

import httpx2
import pytest

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.integrations.notification_config import active_configuration
from app.services.operations.notifications import deliver_once, enqueue


@pytest.mark.skipif(
    os.environ.get("NOTIFICATIONS_LOCAL_ACCEPTANCE") != "1",
    reason="Start the isolated local Alertmanager/Mailpit acceptance containers",
)
def test_real_alertmanager_switch_and_mailpit_delivery(client, admin_headers, monkeypatch):
    settings = get_settings()
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setattr(
        settings, "notification_config_dir", root / ".local/design-completion/notification-config"
    )
    monkeypatch.setattr(settings, "alertmanager_url", "http://127.0.0.1:19097")
    monkeypatch.setattr(settings, "smtp_address", "127.0.0.1:2527")
    monkeypatch.setattr(settings, "smtp_from", "alerts@example.test")
    monkeypatch.setattr(settings, "smtp_username", "")
    monkeypatch.setattr(settings, "smtp_require_tls", False)
    identifier = client.get("/api/v1/me", headers=admin_headers).json()["id"]
    assert (
        client.patch(
            f"/api/v1/users/{identifier}",
            headers=admin_headers,
            json={"notification_email": "operator@example.test"},
        ).status_code
        == 200
    )
    configured = client.put(
        "/api/v1/system/notification-policy",
        headers=admin_headers,
        json={
            "expected_version": 0,
            "managed": True,
            "email_enabled": True,
            "confirm_legacy_switch": True,
        },
    )
    assert configured.status_code == 200, configured.text
    receiver = next(r for r in active_configuration()["receivers"] if r["name"] == "platform")
    assert not receiver.get("email_configs")
    title = "Local notification acceptance " + uuid4().hex
    with SessionLocal() as db:
        enqueue(db, [UUID(identifier)], "acceptance", "test", uuid4(), title)
        db.commit()
    assert deliver_once() and not deliver_once()
    rows = client.get("/api/v1/notifications", headers=admin_headers).json()
    assert rows[0]["mail_status"] == "sent"
    messages = httpx2.get("http://127.0.0.1:18027/api/v1/messages", timeout=5).json()["messages"]
    received = [message for message in messages if message["Subject"] == title]
    assert len(received) == 1 and received[0]["To"][0]["Address"] == "operator@example.test"
    restored = client.put(
        "/api/v1/system/notification-policy",
        headers=admin_headers,
        json={
            "expected_version": 1,
            "managed": False,
            "email_enabled": False,
            "confirm_legacy_switch": True,
        },
    )
    assert restored.status_code == 200, restored.text
    receiver = next(r for r in active_configuration()["receivers"] if r["name"] == "platform")
    assert receiver["email_configs"]
