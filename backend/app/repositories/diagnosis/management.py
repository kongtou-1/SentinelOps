"""Database access; transaction ownership stays with the caller."""

from sqlalchemy import select

from app.models import AgentAnalysis


def history_query(incident_id):
    return (
        select(AgentAnalysis)
        .where(AgentAnalysis.incident_id == incident_id, AgentAnalysis.deleted_at.is_(None))
        .order_by(AgentAnalysis.created_at.desc(), AgentAnalysis.id.desc())
    )
