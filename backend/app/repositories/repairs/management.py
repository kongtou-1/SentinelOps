"""Database access; transaction ownership stays with the caller."""

from sqlalchemy import select

from app.models import RepairHandoff


def history_query(incident_id):
    return (
        select(RepairHandoff)
        .where(RepairHandoff.incident_id == incident_id, RepairHandoff.deleted_at.is_(None))
        .order_by(RepairHandoff.created_at.desc(), RepairHandoff.id.desc())
    )
