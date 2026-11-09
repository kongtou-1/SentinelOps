"""Database access; transaction ownership stays with the caller."""

from sqlalchemy import String, cast, func, or_, select

from app.models import MarketAuditLog, MarketCategory, MarketEntry, MarketRevision, User


def record_query(identifier, model):
    query = select(model).where(model.id == identifier, model.deleted_at.is_(None))
    return query


def record_query_2(query):
    query = query.with_for_update().execution_options(populate_existing=True)
    return query


def record_query_3(db, query):
    row = db.scalar(query)
    return row


def latest_query(db, entry):
    return db.scalar(
        select(MarketRevision)
        .where(MarketRevision.entry_id == entry.id, MarketRevision.deleted_at.is_(None))
        .order_by(MarketRevision.number.desc())
        .limit(1)
    )


def audit_query(action, category, db, detail, entry, revision, user):
    db.add(
        MarketAuditLog(
            user_id=user.id,
            entry_id=entry.id if entry else None,
            revision_id=revision.id if revision else None,
            category_id=category.id if category else None,
            action=action,
            detail=detail or {},
        )
    )


def categories_query():
    query = (
        select(MarketRevision.category_id, func.count())
        .join(MarketEntry, MarketEntry.published_revision_id == MarketRevision.id)
        .where(
            MarketEntry.deleted_at.is_(None),
            MarketEntry.listed.is_(True),
            MarketRevision.deleted_at.is_(None),
            MarketRevision.status == "approved",
        )
    )
    return query


def categories_query_2(kind, query):
    query = query.where(MarketEntry.kind == kind)
    return query


def categories_query_3(db, query):
    counts = dict(db.execute(query.group_by(MarketRevision.category_id)).all())
    return counts


def categories_query_4(db):
    return db.scalars(
        select(MarketCategory)
        .where(MarketCategory.deleted_at.is_(None))
        .order_by(MarketCategory.sort_order, MarketCategory.name, MarketCategory.id)
    )


def create_category_query(category, db):
    db.add(category)


def create_category_query_2(db):
    db.flush()


def delete_category_query(category, db):
    return db.scalar(
        select(MarketRevision.id).where(MarketRevision.category_id == category.id).limit(1)
    )


def entries_query():
    current = (
        select(MarketRevision.id)
        .where(MarketRevision.entry_id == MarketEntry.id, MarketRevision.deleted_at.is_(None))
        .order_by(MarketRevision.number.desc())
        .limit(1)
        .correlate(MarketEntry)
        .scalar_subquery()
    )
    return current


def entries_query_2(current):
    query = (
        select(MarketEntry, MarketRevision, MarketCategory, User)
        .join(MarketRevision, MarketRevision.id == current)
        .join(MarketCategory, MarketCategory.id == MarketRevision.category_id)
        .join(User, User.id == MarketEntry.owner_id)
        .where(
            MarketEntry.deleted_at.is_(None),
            MarketRevision.deleted_at.is_(None),
            MarketCategory.deleted_at.is_(None),
        )
    )
    return query


def entries_query_3(query):
    query = query.where(MarketEntry.listed.is_(True), MarketRevision.status == "approved")
    return query


def entries_query_4(query, user):
    query = query.where(MarketEntry.owner_id == user.id)
    return query


def entries_query_5(query, status):
    query = query.where(MarketRevision.status == status)
    return query


def entries_query_6(kind, query):
    query = query.where(MarketEntry.kind == kind)
    return query


def entries_query_7(category_id, query):
    query = query.where(MarketRevision.category_id == category_id)
    return query


def entries_query_8(pattern, query):
    query = query.where(
        or_(
            MarketRevision.title.ilike(pattern, escape="\\"),
            MarketRevision.summary.ilike(pattern, escape="\\"),
            cast(MarketRevision.tags, String).ilike(pattern, escape="\\"),
        )
    )
    return query


def entries_query_9(db, query):
    return db.scalar(select(func.count()).select_from(query.subquery()))


def entries_query_10(db, limit, offset, order, query):
    return db.execute(query.order_by(order.desc(), MarketEntry.id).offset(offset).limit(limit))


def create_entry_query(db, entry):
    db.add(entry)
    db.flush()


def create_entry_query_2(db, revision):
    db.add(revision)
    db.flush()


def public_entry_query(db, entry):
    return db.get(User, entry.owner_id)


def workbench_query(db, entry):
    revisions = db.scalars(
        select(MarketRevision)
        .where(MarketRevision.entry_id == entry.id, MarketRevision.deleted_at.is_(None))
        .order_by(MarketRevision.number.desc())
    ).all()
    return revisions


def workbench_query_2(db, entry):
    return db.get(User, entry.owner_id)


def workbench_query_3(db, entry):
    return db.execute(
        select(MarketAuditLog, User)
        .join(User, User.id == MarketAuditLog.user_id)
        .where(MarketAuditLog.entry_id == entry.id, MarketAuditLog.deleted_at.is_(None))
        .order_by(MarketAuditLog.created_at.desc(), MarketAuditLog.id)
        .limit(100)
    )


def save_draft_query(current, db):
    db.add(current)


def save_draft_query_2(db):
    db.flush()


def category_audit_query(db):
    return db.execute(
        select(MarketAuditLog, User)
        .join(User, User.id == MarketAuditLog.user_id)
        .where(MarketAuditLog.category_id.is_not(None), MarketAuditLog.deleted_at.is_(None))
        .order_by(MarketAuditLog.created_at.desc(), MarketAuditLog.id)
        .limit(100)
    )
