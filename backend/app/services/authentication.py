from datetime import UTC, datetime
from uuid import UUID

import app.repositories.authentication as _repository
from app.core.security import ROLE_PERMISSIONS, decode_token, unauthorized


def get_current_session(db, credentials):
    if credentials is None:
        raise unauthorized()
    claims = decode_token(credentials.credentials, "access")
    session = _repository.get_current_session_query(claims, db)
    if (
        session is None
        or session.user_id != UUID(claims["sub"])
        or session.deleted_at is not None
        or session.revoked_at is not None
        or session.expires_at <= datetime.now(UTC)
    ):
        raise unauthorized()
    return session


def get_current_user(db, session):
    user = _repository.get_current_user_query(db, session)
    if user is None or user.role.code not in ROLE_PERMISSIONS:
        raise unauthorized()
    from app.services.access_control import bind

    bind(db, user)
    return user
