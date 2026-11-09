"""Database access; transaction ownership stays with the caller."""

from sqlalchemy import select

from app.core.security import password_hasher
from app.models import Role, User


def main_query(db):
    role = db.scalar(select(Role).where(Role.code == "admin").with_for_update())
    return role


def main_query_2(db, role):
    return db.scalar(select(User.id).where(User.role_id == role.id, User.deleted_at.is_(None)))


def main_query_3(body, db):
    return db.scalar(select(User.id).where(User.username == body.username))


def main_query_4(body, db, role):
    db.add(
        User(
            username=body.username,
            password_hash=password_hasher.hash(body.password.get_secret_value()),
            role_id=role.id,
        )
    )
