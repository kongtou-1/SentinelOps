"""Database access; transaction ownership stays with the caller."""

from uuid import UUID

from sqlalchemy import select, text

from app.models import Approval, Server, SkillExecution, SSHCredential


def stage_query(db, row):
    approval = db.get(Approval, row.approval_id)
    return approval


def authorize_query(db, row):
    approval = db.get(Approval, row.approval_id, with_for_update=True)
    return approval


def interrupt_runs_query(ACTIVE, db):
    return db.scalars(
        select(SkillExecution)
        .where(
            SkillExecution.status.in_(ACTIVE[1:]),
            SkillExecution.deleted_at.is_(None),
        )
        .with_for_update()
    )


def finish_query(db, identifier):
    row = db.get(SkillExecution, identifier, with_for_update=True)
    return row


def execute_once_query(db):
    row = db.scalar(
        select(SkillExecution)
        .where(
            SkillExecution.status == "queued",
            SkillExecution.deleted_at.is_(None),
        )
        .order_by(SkillExecution.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    return row


def execute_once_query_2(db, row):
    approval = db.get(Approval, row.approval_id)
    server = db.get(Server, UUID(approval.binding["server_id"]))
    credential = db.get(SSHCredential, UUID(approval.binding["credential_id"]))
    return approval, credential, server


def execute_once_query_3(owner):
    owner.execute(text("SELECT 1"))


def execute_once_query_4(db, identifier):
    row = db.get(SkillExecution, identifier, with_for_update=True)
    return row


def execute_once_query_5(db, identifier):
    row = db.get(SkillExecution, identifier, with_for_update=True)
    return row


def execute_once_query_6(db, identifier):
    row = db.get(SkillExecution, identifier)
    return row


def main_query(owner):
    return owner.scalar(text("SELECT pg_try_advisory_lock(90009)"))


def main_query_2(owner):
    owner.execute(text("SELECT 1"))
