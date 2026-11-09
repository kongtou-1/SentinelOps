"""Encrypted platform sender settings; recipient routing belongs to each service."""

import json
from email.message import EmailMessage

from cryptography.fernet import InvalidToken
from fastapi import HTTPException

from app.core.config import get_settings
from app.core.secrets import cipher, file_secret
from app.integrations import mail
from app.models import NotificationPolicy
from app.repositories import notifications as repository


def version(db):
    row = repository.notification_policy(db)
    return row.smtp_version if row else 0


def runtime(db, password=True):
    row = repository.notification_policy(db)
    if row and row.encrypted_smtp_config:
        try:
            config = json.loads(cipher().decrypt(row.encrypted_smtp_config.encode()))
        except (InvalidToken, ValueError, TypeError):
            raise HTTPException(503, "平台邮箱配置无法解密，请重新保存配置") from None
        return {**config, "version": row.smtp_version, "source": "platform"}
    settings = get_settings()
    host, _, port = settings.smtp_address.rpartition(":")
    return {
        "host": host,
        "port": int(port),
        "sender": settings.smtp_from,
        "security": "starttls" if settings.smtp_require_tls else "none",
        "username": settings.smtp_username,
        "password": file_secret(settings.smtp_password_file)
        if password and settings.smtp_username
        else "",
        "version": 0,
        "source": "deployment",
    }


def read(db):
    config = runtime(db, password=False)
    return {
        key: config[key]
        for key in ("version", "source", "host", "port", "security", "sender", "username")
    } | {
        "password_configured": bool(
            config["password"] or config["username"] and config["source"] == "deployment"
        ),
        "local_test_service": config["host"] == "mailpit",
    }


def configure(db, body, user):
    row = repository.notification_policy(db, lock=True)
    if body.expected_version != (row.smtp_version if row else 0):
        raise HTTPException(409, "平台邮箱配置已变化，请刷新后重试")
    previous = runtime(db, password=body.password is None)
    password = body.password.get_secret_value() if body.password else previous["password"]
    if body.username and not password:
        raise HTTPException(422, "请填写邮箱密码或 SMTP 授权码")
    if (
        body.username
        and not body.password
        and any(
            getattr(body, key) != previous[key] for key in ("host", "port", "security", "username")
        )
    ):
        raise HTTPException(422, "修改邮件服务器或账号时，请重新填写授权码")
    config = body.model_dump(exclude={"expected_version", "password"})
    config["password"] = password if body.username else ""
    encrypted = cipher().encrypt(json.dumps(config).encode()).decode()
    if not row:
        row = NotificationPolicy(
            id=1,
            managed=False,
            email_enabled=False,
            version=0,
            configured_by=user.id,
            smtp_version=0,
        )
        db.add(row)
    row.encrypted_smtp_config = encrypted
    row.smtp_version += 1
    repository.audit(
        db, user, "platform_mail_saved", "platform_mail", detail={"version": row.smtp_version}
    )
    db.commit()
    return read(db)


def send_test(db, body, user):
    config = runtime(db)
    if config["version"] != body.expected_version:
        raise HTTPException(409, "平台邮箱配置已变化，请刷新后重试")
    client = None
    try:
        message = EmailMessage()
        message["From"], message["To"] = config["sender"], body.recipient
        message["Subject"] = "SentinelOps 平台邮箱测试"
        message.set_content("这是一封由管理员手动发起的平台发件邮箱测试邮件。")
        client = mail.connect(config)
        client.send_message(message)
    except Exception:
        raise HTTPException(502, "测试邮件发送失败，请检查服务器、加密方式、账号和授权码") from None
    finally:
        if client:
            client.close()
    repository.audit(db, user, "platform_mail_tested", "platform_mail")
    db.commit()
    return {"success": True}
