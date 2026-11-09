import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage

from fastapi import HTTPException

from app.db.session import SessionLocal
from app.integrations import mail, notification_config
from app.models import Notification, NotificationPolicy
from app.repositories import notifications as repository
from app.schemas.notifications import OperationsAuditResponse
from app.services.operations import mail_settings
from app.services.pagination import page
from app.services.records import active_record
from app.utils.redaction import redact


def read_policy(db):
    row = repository.notification_policy(db)
    return {
        "version": row.version if row else 0,
        "managed": bool(row and row.managed),
        "email_enabled": bool(row and row.email_enabled),
        "configured_by": row.configured_by if row else None,
    }


def configure_policy(db, body, user):
    row = repository.notification_policy(db, lock=True)
    if body.expected_version != (row.version if row else 0):
        raise HTTPException(409, "Notification policy version changed")
    previous_managed = bool(row and row.managed)
    if body.managed != previous_managed and not body.confirm_legacy_switch:
        raise HTTPException(422, "Explicitly confirm the Alertmanager email switch")
    previous = (
        notification_config.switch(body.managed) if body.managed != previous_managed else None
    )
    try:
        if not row:
            row = NotificationPolicy(id=1, version=0)
            db.add(row)
        row.managed, row.email_enabled, row.configured_by = (
            body.managed,
            body.email_enabled,
            user.id,
        )
        row.version += 1
        repository.audit(
            db,
            user,
            "notification_policy_saved",
            "notification_policy",
            detail={
                "version": row.version,
                "managed": row.managed,
                "email_enabled": row.email_enabled,
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        if previous:
            notification_config.restore(previous)
        raise
    return read_policy(db)


def enqueue(db, user_ids, kind, resource_kind, resource_id, title, detail=None, incident_id=None):
    policy = repository.notification_policy(db)
    emails = set()
    created = 0
    for identifier in sorted(set(user_ids), key=str):
        user = repository.active_user(db, identifier)
        if not user:
            continue
        email = user.notification_email
        enabled = bool(
            policy and policy.managed and policy.email_enabled and email and email not in emails
        )
        if enabled:
            emails.add(email)
        created += bool(
            repository.add_notification(
                db,
                {
                    "user_id": user.id,
                    "incident_id": incident_id,
                    "kind": kind,
                    "resource_kind": resource_kind,
                    "resource_id": resource_id,
                    "dedupe_key": f"{resource_kind}:{resource_id}:{kind}:{user.id}",
                    "title": " ".join(redact(title).split())[:200],
                    "detail": redact(detail or {}),
                    "email": email if enabled else "",
                    "policy_version": policy.version if enabled else None,
                    "mail_status": "queued" if enabled else "disabled",
                },
            )
        )
    return created


def listing(db, user, response, unread_only=False, offset=0, limit=50):
    return page(db, repository.feed(user.id, unread_only), response, offset, limit)


def unread_count(db, user):
    return {"unread": repository.unread_count(db, user.id)}


def audit_listing(db, response, offset=0, limit=50, scope=None):
    rows = page(db, repository.audit_query(scope), response, offset, limit)
    users, names = repository.audit_context(db, rows)
    return [
        OperationsAuditResponse.model_validate(row).model_copy(
            update={
                "actor_username": users.get(row.user_id),
                "resource_name": names.get((row.resource_kind, row.resource_id)),
            }
        )
        for row in rows
    ]


def mark_read(db, identifier, user):
    row = active_record(db, Notification, identifier)
    if row.user_id != user.id:
        raise HTTPException(404, "Notification not found")
    if row.read_at is None:
        row.read_at = datetime.now(UTC)
        db.commit()
    return row


def remove(db, identifier, user):
    row = active_record(db, Notification, identifier)
    if row.user_id != user.id:
        raise HTTPException(404, "Notification not found")
    row.deleted_at = row.updated_at = datetime.now(UTC)
    repository.audit(db, user, "notification_deleted", "notification", row.id)
    db.commit()


def delivery_authorized(db, row):
    policy = repository.notification_policy(db)
    user = repository.active_user(db, row.user_id)
    author = repository.active_user(db, policy.configured_by) if policy else None
    if user and row.incident_id:
        from app.models import Incident
        from app.services.access_control import record_allowed

        if not record_allowed(db, user, Incident, row.incident_id, "incidents:read"):
            return False
    return bool(
        policy
        and policy.managed
        and policy.email_enabled
        and policy.version == row.policy_version
        and author
        and author.role.code == "admin"
        and user
        and user.notification_email
        and user.notification_email == row.email
    )


def interrupt_deliveries(db):
    for row in repository.unfinished_mail(db):
        row.mail_status = "unknown" if row.dispatched_at else "failed"
        row.error_code, row.finished_at = "worker_interrupted", datetime.now(UTC)
    db.commit()


def finish(identifier, status, error=None):
    with SessionLocal() as db:
        row = db.get(Notification, identifier, with_for_update=True)
        if row and row.mail_status == "sending":
            row.mail_status, row.error_code, row.finished_at = status, error, datetime.now(UTC)
            repository.audit(
                db,
                None,
                "notification_" + status,
                "notification",
                identifier,
                {"error_code": error},
            )
            db.commit()


def deliver_once():
    with SessionLocal() as db:
        row = repository.queued_mail(db)
        if not row:
            return False
        if not delivery_authorized(db, row):
            row.mail_status, row.error_code = "failed", "authorization_or_configuration_changed"
            row.finished_at = datetime.now(UTC)
            db.commit()
            return True
        identifier, email, title, kind, resource_kind, resource_id = (
            row.id,
            row.email,
            row.title,
            row.kind,
            row.resource_kind,
            row.resource_id,
        )
        row.mail_status = "sending"
        db.commit()
    client, dispatched = None, False
    try:
        with SessionLocal() as db:
            smtp_config = mail_settings.runtime(db)
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = smtp_config["sender"], email, title
        message["Message-ID"] = f"<notification-{identifier}@sentinelops.local>"
        message.set_content(f"{title}\n事件：{kind}\n对象：{resource_kind}/{resource_id}\n")
        client = mail.connect(smtp_config)
        code, _ = client.mail(smtp_config["sender"])
        if code != 250:
            raise smtplib.SMTPSenderRefused(code, b"sender refused", smtp_config["sender"])
        code, _ = client.rcpt(email)
        if code not in (250, 251):
            raise smtplib.SMTPRecipientsRefused({email: (code, b"recipient refused")})
        with SessionLocal() as db:
            row = db.get(Notification, identifier, with_for_update=True)
            if (
                row.mail_status != "sending"
                or not delivery_authorized(db, row)
                or mail_settings.version(db) != smtp_config["version"]
            ):
                raise PermissionError
            row.dispatched_at = datetime.now(UTC)
            db.commit()
        dispatched = True
        code, _ = client.data(message.as_bytes())
        if code != 250:
            raise smtplib.SMTPDataError(code, b"message refused")
        finish(identifier, "sent")
    except (smtplib.SMTPResponseException, smtplib.SMTPRecipientsRefused):
        finish(identifier, "failed", "smtp_rejected")
    except PermissionError:
        finish(identifier, "failed", "authorization_changed")
    except Exception:
        finish(
            identifier,
            "unknown" if dispatched else "failed",
            "smtp_result_unknown" if dispatched else "smtp_unavailable",
        )
    finally:
        if client:
            client.close()
    return True
