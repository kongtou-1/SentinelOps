"""Database access; transaction ownership stays with the caller."""

from sqlalchemy import select

from app.models import PostmortemDelivery, PostmortemRevision


def revisions_query(identifier):
    return (
        select(PostmortemRevision)
        .where(PostmortemRevision.report_id == identifier, PostmortemRevision.deleted_at.is_(None))
        .order_by(PostmortemRevision.revision)
    )


def deliveries_query(identifier):
    return (
        select(PostmortemDelivery)
        .where(PostmortemDelivery.report_id == identifier, PostmortemDelivery.deleted_at.is_(None))
        .order_by(PostmortemDelivery.created_at.desc(), PostmortemDelivery.id.desc())
    )
