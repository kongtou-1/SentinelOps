"""Database access; transaction ownership stays with the caller."""

from uuid import uuid4

from sqlalchemy import select

from app.models import (
    Approval,
    AuditLog,
    Incident,
    Role,
    SkillExecution,
    User,
)


def audit_query(action, approval, content, db, execution, request_ip, user):
    db.add(
        AuditLog(
            user_id=user.id if user else None,
            incident_id=approval.incident_id,
            approval_id=approval.id,
            execution_id=execution.id if execution else None,
            action=action,
            request_id=str(uuid4()),
            ip=request_ip,
            detail=content,
        )
    )
    incident = db.get(Incident, approval.incident_id)
    return incident


def user_valid_query(admin, db, identifier):
    from app.repositories.access import capability_condition

    return db.scalar(
        select(User.id)
        .join(Role)
        .where(
            User.id == identifier,
            User.is_active.is_(True),
            User.deleted_at.is_(None),
            Role.deleted_at.is_(None),
            Role.code == "admin" if admin else capability_condition("executions:run"),
        )
    )


def invalid_reason_query(approval, db):
    incident = db.get(Incident, approval.incident_id)
    return incident


def create_approval_query(analysis_id, db):
    previous = db.scalar(
        select(Approval)
        .where(
            Approval.analysis_id == analysis_id,
            Approval.status.in_(("pending", "approved")),
            Approval.deleted_at.is_(None),
        )
        .with_for_update()
    )
    return previous


def create_approval_query_2(db):
    db.flush()


def create_approval_query_3(db, row):
    db.add(row)
    db.flush()


def create_approval_query_4(db, row):
    db.refresh(row)


def locked_approval_query(db, identifier):
    return db.scalar(
        select(Approval)
        .where(
            Approval.id == identifier,
            Approval.deleted_at.is_(None),
        )
        .with_for_update()
    )


def decide_query(db, row):
    db.refresh(row)


def enqueue_query(db, row):
    existing = db.scalar(select(SkillExecution).where(SkillExecution.approval_id == row.id))
    return existing


def enqueue_query_2(ACTIVE, db, row):
    return db.scalar(
        select(SkillExecution.id).where(
            SkillExecution.container_id == row.container_id,
            SkillExecution.status.in_(ACTIVE),
            SkillExecution.deleted_at.is_(None),
        )
    )


def enqueue_query_3(db, execution):
    db.add(execution)
    db.flush()


def enqueue_query_4(db, execution):
    db.refresh(execution)
