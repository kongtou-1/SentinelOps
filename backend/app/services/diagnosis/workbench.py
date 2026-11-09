import asyncio

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

import app.repositories.diagnosis.workbench as _repository
from app.db.session import SessionLocal
from app.models import (
    AnalysisConversation,
    Incident,
)
from app.schemas.conversations import (
    EventResponse,
)
from app.services.access_control import has_permission
from app.services.authentication import get_current_user
from app.services.diagnosis.conversations import (
    access,
    adopt,
    cancel,
    remove,
    run_access,
    send,
    snapshot,
)
from app.services.incidents.logic import writable
from app.services.pagination import page
from app.services.records import active_record


def create(incident_id, body, db, user):
    writable(active_record(db, Incident, incident_id))
    row = AnalysisConversation(
        incident_id=incident_id,
        owner_id=user.id,
        title=body.title.strip() or "事故诊断",
        last_seq=0,
    )
    _repository.create_query(db, row)
    db.commit()
    _repository.create_query_2(db, row)
    return snapshot(db, row)


def listing(incident_id, response, db, user, owner_id=None, offset=0, limit=50):
    active_record(db, Incident, incident_id)
    statement = _repository.listing_query(incident_id)
    if not has_permission(user, "analyses:run", db):
        statement = _repository.listing_query_2(statement)
    elif not has_permission(user, "analysis_conversations:read_all", db):
        statement = _repository.listing_query_3(statement, user)
    elif owner_id:
        statement = _repository.listing_query_4(owner_id, statement)
    from app.services.access_control import context

    statement = statement.where(context(db, user).clause(AnalysisConversation, "analyses:run"))
    rows = page(
        db,
        _repository.listing_query_5(statement),
        response,
        offset,
        limit,
    )
    return [snapshot(db, row) for row in rows]


def detail(identifier, db, user):
    return snapshot(db, access(db, identifier, user))


def delete(identifier, db, user):
    remove(db, identifier, user)


def message(identifier, body, db, user):
    return send(db, identifier, user, body)


def messages(identifier, response, db, user, offset=0, limit=50):
    access(db, identifier, user)
    return page(
        db,
        _repository.messages_query(identifier),
        response,
        offset,
        limit,
    )


def runs(identifier, response, db, user, offset=0, limit=50):
    access(db, identifier, user)
    return page(
        db,
        _repository.runs_query(identifier),
        response,
        offset,
        limit,
    )


def events(identifier, db, user, after_seq=0, limit=100):
    access(db, identifier, user)
    return _repository.events_query(after_seq, db, identifier, limit).all()


def stream(identifier, request, db, user, after_seq=0):
    access(db, identifier, user)
    # No dependency-scoped connection is retained while streaming; every pass rechecks auth.
    authorization = request.headers.get("authorization", "")
    db.close()

    async def generate():
        from fastapi.security import HTTPAuthorizationCredentials

        from app.services.authentication import get_current_session

        cursor = after_seq
        ticks = 0
        while not await request.is_disconnected():
            try:
                with SessionLocal() as session:
                    credentials = HTTPAuthorizationCredentials(
                        scheme="Bearer", credentials=authorization.removeprefix("Bearer ")
                    )
                    auth_session = get_current_session(session, credentials)
                    current = get_current_user(session, auth_session)
                    access(session, identifier, current)
                    batch = _repository.stream_generate_query(cursor, identifier, session)
                    frames = [
                        (item.seq, EventResponse.model_validate(item).model_dump_json())
                        for item in batch
                    ]
            except HTTPException as exc:
                kind = "auth_required" if exc.status_code == 401 else "permission_lost"
                yield f"event: {kind}\ndata: {{}}\n\n"
                return
            except Exception:
                yield "event: connection_error\ndata: {}\n\n"
                return
            for seq, payload in frames:
                yield f"id: {seq}\nevent: event\ndata: {payload}\n\n"
                cursor = seq
            ticks += 1
            if ticks % 20 == 0:
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def run_detail(identifier, db, user):
    return run_access(db, identifier, user)


def stop(identifier, db, user):
    return cancel(db, identifier, user)


def publish(identifier, db, user):
    return adopt(db, identifier, user)
