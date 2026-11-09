from sqlalchemy import select

from app.models import Role, User


def active_users():
    return select(User).join(Role).where(User.deleted_at.is_(None), Role.deleted_at.is_(None))


def active_by_username(db, username):
    return db.scalar(active_users().where(User.username == username, User.is_active.is_(True)))
