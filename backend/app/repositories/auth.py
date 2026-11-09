"""Database access; transaction ownership stays with the caller."""

from uuid import UUID

from sqlalchemy import func, select, update

from app.models import AuthSession, Role, User
from app.repositories.users import active_users


def get_role_query(code, db):
    role = db.scalar(select(Role).where(Role.code == code, Role.deleted_at.is_(None)))
    return role


def commit_user_query(db, user):
    db.refresh(user)


def revoke_sessions_query(db, user_id):
    db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=func.clock_timestamp())
    )


def protect_admin_query(db, user):
    db.scalar(select(Role).where(Role.code == "admin").with_for_update())
    admins = db.scalars(
        active_users().where(User.role_id == user.role_id, User.is_active.is_(True))
    )
    return admins


def login_query(body, db):
    user = db.scalar(active_users().where(User.username == body.username).with_for_update(of=User))
    return user


def login_query_2(db):
    now = db.scalar(select(func.clock_timestamp()))
    return now


def login_query_3(db, session):
    db.add(session)


def refresh_query(claims, db):
    session = db.scalar(
        select(AuthSession).where(AuthSession.id == UUID(claims["sid"])).with_for_update()
    )
    return session


def refresh_query_2(db, session):
    user = db.scalar(active_users().where(User.id == session.user_id, User.is_active.is_(True)))
    return user


def logout_query():
    return func.clock_timestamp()


def roles_query(db):
    return db.scalars(select(Role).where(Role.deleted_at.is_(None)).order_by(Role.code))


def users_query():
    return active_users().order_by(User.username)


def create_user_query(db, user):
    db.add(user)


def update_user_query(db, user_id):
    db.scalar(select(Role).where(Role.code == "admin").with_for_update())
    user = db.scalar(
        active_users()
        .where(User.id == user_id)
        .with_for_update(of=User)
        .execution_options(populate_existing=True)
    )
    return user


def delete_user_query(db, user_id):
    db.scalar(select(Role).where(Role.code == "admin").with_for_update())
    user = db.scalar(
        active_users()
        .where(User.id == user_id)
        .with_for_update(of=User)
        .execution_options(populate_existing=True)
    )
    return user


def delete_user_query_2():
    return func.clock_timestamp()
