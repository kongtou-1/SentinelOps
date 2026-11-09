import os
from uuid import UUID, uuid4

import httpx2
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.integrations import mail
from app.models import Notification, NotificationPolicy, OperationsAuditLog, User
from app.services.operations import mail_settings, notifications

PATH = "/api/v1/system/mail-settings"
SECRET = "synthetic-smtp-authorization-code"


@pytest.fixture(autouse=True)
def encrypted_mail_key(tmp_path, monkeypatch):
    key = tmp_path / "mail.key"
    key.write_bytes(Fernet.generate_key())
    monkeypatch.setattr(get_settings(), "credential_key_file", key)


def settings(**changes):
    return {
        "expected_version": 0,
        "host": "smtp.example.test",
        "port": 465,
        "security": "ssl",
        "sender": "platform@example.test",
        "username": "platform@example.test",
        "password": SECRET,
        **changes,
    }


def test_sender_configuration_is_encrypted_and_independent_from_recipient_policy(
    client, admin_headers
):
    result = client.put(PATH, headers=admin_headers, json=settings())
    assert result.status_code == 200, result.text
    assert result.json()["password_configured"] and "password" not in result.json()
    assert SECRET not in result.text
    with SessionLocal() as db:
        row = db.get(NotificationPolicy, 1)
        assert row.smtp_version == 1 and row.version == 0
        assert not row.email_enabled and not row.managed
        assert SECRET not in row.encrypted_smtp_config
        assert "platform@example.test" not in row.encrypted_smtp_config
        assert mail_settings.runtime(db)["password"] == SECRET
        assert SECRET not in str(db.scalar(select(OperationsAuditLog)).detail)
    kept = client.put(
        PATH,
        headers=admin_headers,
        json=settings(
            expected_version=1,
            password=None,
            sender="alerts@example.test",
        ),
    )
    assert kept.status_code == 200 and kept.json()["version"] == 2
    with SessionLocal() as db:
        assert mail_settings.runtime(db)["password"] == SECRET
    assert client.put(PATH, headers=admin_headers, json=settings()).status_code == 409
    changed_host = client.put(
        PATH,
        headers=admin_headers,
        json=settings(
            expected_version=2,
            host="other.example.test",
            password=None,
        ),
    )
    assert changed_host.status_code == 422
    assert client.get(PATH, headers=admin_headers).json()["host"] == "smtp.example.test"


@pytest.mark.parametrize(
    "changes",
    [
        {"sender": "invalid"},
        {"host": "smtp.example.test\r\ninvalid"},
        {"port": 0},
        {"port": 65536},
        {"security": "none"},
    ],
)
def test_sender_validates_mail_address_transport_and_port(client, admin_headers, changes):
    assert client.put(PATH, headers=admin_headers, json=settings(**changes)).status_code == 422


def test_only_admin_can_configure_or_send_test_mail(client, make_user, login):
    make_user("mail-operator", "operator")
    headers = {"Authorization": "Bearer " + login("mail-operator")["access_token"]}
    assert client.put(PATH, headers=headers, json=settings()).status_code == 403
    assert (
        client.post(
            PATH + "/test",
            headers=headers,
            json={
                "expected_version": 0,
                "recipient": "recipient@example.test",
            },
        ).status_code
        == 403
    )


def test_test_mail_is_explicit_and_uses_saved_credentials(client, admin_headers, monkeypatch):
    sent = []

    class SMTP:
        def send_message(self, message):
            sent.append(message)

        def close(self):
            pass

    def connect(config):
        assert config["password"] == SECRET and config["security"] == "ssl"
        return SMTP()

    monkeypatch.setattr(mail, "connect", connect)
    assert client.put(PATH, headers=admin_headers, json=settings()).status_code == 200
    assert not sent
    body = {"expected_version": 1, "recipient": "recipient@example.test"}
    assert (
        client.post(
            PATH + "/test", headers=admin_headers, json={**body, "expected_version": 0}
        ).status_code
        == 409
    )
    result = client.post(PATH + "/test", headers=admin_headers, json=body)
    assert result.status_code == 200 and len(sent) == 1
    assert sent[0]["From"] == "platform@example.test" and sent[0]["To"] == body["recipient"]


def test_smtp_failure_does_not_expose_credentials(client, admin_headers, monkeypatch):
    assert client.put(PATH, headers=admin_headers, json=settings()).status_code == 200

    def unavailable(config):
        raise ValueError(SECRET)

    monkeypatch.setattr(mail, "connect", unavailable)
    result = client.post(
        PATH + "/test",
        headers=admin_headers,
        json={
            "expected_version": 1,
            "recipient": "recipient@example.test",
        },
    )
    assert result.status_code == 502 and SECRET not in result.text


def test_notification_delivery_uses_saved_platform_sender(client, admin_headers, monkeypatch):
    assert client.put(PATH, headers=admin_headers, json=settings()).status_code == 200
    actor = UUID(client.get("/api/v1/me", headers=admin_headers).json()["id"])
    with SessionLocal() as db:
        user = db.get(User, actor)
        user.notification_email = "operator@example.test"
        row = db.get(NotificationPolicy, 1)
        row.managed = row.email_enabled = True
        row.version += 1
        db.flush()
        notifications.enqueue(db, [actor], "test", "test", uuid4(), "Sender integration")
        db.commit()
    sent = []

    class SMTP:
        def mail(self, sender):
            assert sender == "platform@example.test"
            return 250, b"ok"

        def rcpt(self, recipient):
            assert recipient == "operator@example.test"
            return 250, b"ok"

        def data(self, payload):
            sent.append(payload)
            return 250, b"ok"

        def close(self):
            pass

    def connect(config):
        assert config["host"] == "smtp.example.test" and config["password"] == SECRET
        return SMTP()

    monkeypatch.setattr(mail, "connect", connect)
    assert notifications.deliver_once() and len(sent) == 1
    with SessionLocal() as db:
        assert db.scalar(select(Notification)).mail_status == "sent"


@pytest.mark.parametrize("security", ["ssl", "starttls"])
def test_smtp_transport_negotiates_encryption_before_authentication(monkeypatch, security):
    calls = []

    class SMTP:
        def __init__(self, host, port, **kwargs):
            calls.append("connect")
            if security == "ssl":
                assert kwargs["context"].check_hostname

        def ehlo(self):
            calls.append("ehlo")

        def starttls(self, **kwargs):
            assert kwargs["context"].check_hostname
            calls.append("starttls")

        def login(self, user, password):
            assert password == SECRET
            calls.append("login")

    monkeypatch.setattr(mail.smtplib, "SMTP", SMTP)
    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", SMTP)
    mail.connect({**settings(), "security": security})
    assert calls == (
        ["connect", "ehlo", "login"]
        if security == "ssl"
        else ["connect", "ehlo", "starttls", "ehlo", "login"]
    )


@pytest.mark.skipif(
    os.environ.get("PLATFORM_MAIL_LOCAL_ACCEPTANCE") != "1", reason="Local Mailpit only"
)
def test_saved_sender_delivers_to_isolated_local_mailpit(client, admin_headers):
    recipient = f"mail-test-{uuid4().hex}@example.test"
    result = client.put(
        PATH,
        headers=admin_headers,
        json=settings(
            host="127.0.0.1",
            port=2528,
            security="none",
            username="",
            password=None,
        ),
    )
    assert result.status_code == 200, result.text
    result = client.post(
        PATH + "/test",
        headers=admin_headers,
        json={
            "expected_version": 1,
            "recipient": recipient,
        },
    )
    assert result.status_code == 200, result.text
    messages = httpx2.get("http://127.0.0.1:18028/api/v1/messages", timeout=5).json()["messages"]
    assert any(
        message["To"][0]["Address"] == recipient
        and message["From"]["Address"] == "platform@example.test"
        for message in messages
    )
