"""Automatic diagnosis configuration and authorization records."""

from sqlalchemy import select

from app.models import AgentAnalysis, AlertRule, AnalysisRun, DiagnosisPolicy, Role, User


def policy(db):
    return db.scalar(select(DiagnosisPolicy).where(DiagnosisPolicy.deleted_at.is_(None)))


def add(db, row):
    db.add(row)


def rule(db, rule_id):
    return db.scalar(
        select(AlertRule).where(AlertRule.id == rule_id, AlertRule.deleted_at.is_(None))
    )


def admin(db, user_id):
    return db.scalar(
        select(User)
        .join(Role)
        .where(
            User.id == user_id,
            User.is_active.is_(True),
            User.deleted_at.is_(None),
            Role.code == "admin",
            Role.deleted_at.is_(None),
        )
    )


def already_analyzed(db, incident_id):
    # Even a failed or retired analysis consumes the automatic attempt.
    if (
        db.scalar(select(AgentAnalysis.id).where(AgentAnalysis.incident_id == incident_id).limit(1))
        is not None
    ):
        return True
    return (
        db.scalar(
            select(AnalysisRun.id)
            .where(
                AnalysisRun.incident_id == incident_id,
                AnalysisRun.status.in_(("queued", "running", "cancelling")),
                AnalysisRun.deleted_at.is_(None),
            )
            .limit(1)
        )
        is not None
    )
