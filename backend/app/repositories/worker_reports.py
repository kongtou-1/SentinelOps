"""Database access; transaction ownership stays with the caller."""

from sqlalchemy import select, text

from app.models import PostmortemDelivery, PostmortemReport


def generate_once_query(db):
    row = db.scalar(
        select(PostmortemReport)
        .where(PostmortemReport.status == "queued", PostmortemReport.deleted_at.is_(None))
        .order_by(PostmortemReport.created_at, PostmortemReport.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    return row


def finish_delivery_query(db, identifier):
    delivery = db.get(PostmortemDelivery, identifier, with_for_update=True)
    return delivery


def finish_delivery_query_2(db, delivery):
    report = db.get(PostmortemReport, delivery.report_id)
    return report


def deliver_once_query(db):
    delivery = db.scalar(
        select(PostmortemDelivery)
        .where(PostmortemDelivery.status == "queued", PostmortemDelivery.deleted_at.is_(None))
        .order_by(PostmortemDelivery.created_at, PostmortemDelivery.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    return delivery


def deliver_once_query_2(db, delivery):
    report = db.get(PostmortemReport, delivery.report_id)
    return report


def deliver_once_query_3(db, identifier):
    current = db.get(PostmortemDelivery, identifier, with_for_update=True)
    report = db.get(PostmortemReport, current.report_id)
    return current, report


def interrupt_tasks_query(db):
    return db.scalars(select(PostmortemReport).where(PostmortemReport.status == "running"))


def interrupt_tasks_query_2(db):
    return db.scalars(select(PostmortemDelivery).where(PostmortemDelivery.status == "sending"))


def interrupt_tasks_query_3(db, row):
    return db.get(PostmortemReport, row.report_id)


def main_query(owner):
    return owner.scalar(text("SELECT pg_try_advisory_lock(100010)"))


def main_query_2(owner):
    owner.execute(text("SELECT 1"))
