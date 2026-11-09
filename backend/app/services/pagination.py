"""Array responses remain compatible; totals describe the filtered query."""

from fastapi import HTTPException, Response

import app.repositories.pagination as _repository


def page(db, query, response: Response, offset=0, limit=50):
    response.headers["X-Total-Count"] = str(_repository.page_query(db, query))
    return _repository.page_query_2(db, limit, offset, query).all()


def time_range(query, column, start, end):
    if start and end and end < start:
        raise HTTPException(422, "End must not precede start")
    if start:
        query = _repository.time_range_query(column, query, start)
    if end:
        query = _repository.time_range_query_2(column, end, query)
    return query
