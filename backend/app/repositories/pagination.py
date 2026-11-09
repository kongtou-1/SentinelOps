"""Database access; transaction ownership stays with the caller."""

from sqlalchemy import func, select


def page_query(db, query):
    return db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))


def page_query_2(db, limit, offset, query):
    return db.scalars(query.offset(offset).limit(limit))


def time_range_query(column, query, start):
    query = query.where(column >= start)
    return query


def time_range_query_2(column, end, query):
    query = query.where(column <= end)
    return query
