"""Database access; transaction ownership stays with the caller."""

from sqlalchemy import and_, case, func, select

from app.models import (
    Approval,
    Container,
    Incident,
    LLMEndpoint,
    User,
)


def listing_query(model):
    query = select(model).where(model.deleted_at.is_(None))
    return query


def listing_query_2(now):
    state = case(
        (
            and_(Approval.status.in_(("pending", "approved")), Approval.expires_at <= now),
            "expired",
        ),
        else_=Approval.status,
    )
    return state


def listing_query_3(query, state, status):
    query = query.where(state == status)
    return query


def listing_query_4(incident_id, model, query):
    query = query.where(model.incident_id == incident_id)
    return query


def listing_query_5(actor_field, actor_id, model, query):
    query = query.where(getattr(model, actor_field) == actor_id)
    return query


def listing_query_6(model, query):
    return query.order_by(model.created_at.desc(), model.id.desc())


def listing_query_7(actor_field, db, rows):
    incidents = {
        r.id: r
        for r in db.scalars(select(Incident).where(Incident.id.in_({r.incident_id for r in rows})))
    }
    users = {
        r.id: r.username
        for r in db.scalars(
            select(User).where(User.id.in_({getattr(r, actor_field) for r in rows}))
        )
    }
    containers = {
        r.id: r.name
        for r in db.scalars(
            select(Container).where(
                Container.id.in_({getattr(r, "container_id", None) for r in rows})
            )
        )
    }
    return containers, incidents, users


def counts_query(db, model):
    return db.execute(
        select(model.status, func.count()).where(model.deleted_at.is_(None)).group_by(model.status)
    )


def summary_query(db):
    endpoints = db.scalars(select(LLMEndpoint).where(LLMEndpoint.deleted_at.is_(None))).all()
    return endpoints


def summary_count_query(conditions, db, model):
    return db.scalar(
        select(func.count()).select_from(model).where(model.deleted_at.is_(None), *conditions)
    )


def summary_query_2(db):
    return db.scalar(select(func.max(Container.last_seen_at)).where(Container.deleted_at.is_(None)))
