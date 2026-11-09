"""Database access; transaction ownership stays with the caller."""

from uuid import UUID

from app.models import AuthSession, User
from app.repositories.users import active_users


def get_current_session_query(claims, db):
    session = db.get(AuthSession, UUID(claims["sid"]))
    return session


def get_current_user_query(db, session):
    user = db.scalar(active_users().where(User.id == session.user_id, User.is_active.is_(True)))
    return user
