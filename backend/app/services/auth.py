import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

import app.repositories.auth as _repository
from app.core.config import get_settings
from app.core.security import (
    DUMMY_HASH,
    ROLE_PERMISSIONS,
    decode_token,
    hash_jti,
    password_hasher,
    token_pair,
    unauthorized,
)
from app.models import AuthSession, User
from app.services.pagination import page


def user_response(user):
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role.code,
        "is_active": user.is_active,
        "last_login_at": user.last_login_at,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
        "notification_email": user.notification_email,
    }


def no_store(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def get_role(db, code):
    role = _repository.get_role_query(code, db)
    if role is None:
        raise HTTPException(503, "Roles are not initialized; run migrations")
    return role


def commit_user(db, user):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Username is already reserved") from None
    _repository.commit_user_query(db, user)


def revoke_sessions(db, user_id):
    _repository.revoke_sessions_query(db, user_id)


def protect_admin(db, user):
    if user.role.code != "admin" or not user.is_active:
        return
    # Serialize removals/demotions of administrators to preserve an active administrator.
    admins = _repository.protect_admin_query(db, user)
    if len(admins.all()) <= 1:
        raise HTTPException(409, "Cannot remove the last active administrator")


def login(body, db, response):
    no_store(response)
    # Hold the user lock through verification and session creation. Account changes
    # cannot revoke sessions before a login using the old password commits.
    user = _repository.login_query(body, db)
    valid = password_hasher.verify(
        body.password.get_secret_value(), user.password_hash if user else DUMMY_HASH
    )
    if not valid or user is None or not user.is_active:
        raise unauthorized()
    now = _repository.login_query_2(db)
    refresh_jti = secrets.token_urlsafe(32)
    session = AuthSession(
        id=uuid4(),
        user_id=user.id,
        refresh_jti_hash=hash_jti(refresh_jti),
        expires_at=now + timedelta(days=get_settings().refresh_token_days),
    )
    user.last_login_at = now
    _repository.login_query_3(db, session)
    db.commit()
    return token_pair(user, session, refresh_jti)


def refresh(body, db, response):
    no_store(response)
    claims = decode_token(body.refresh_token.get_secret_value(), "refresh")
    session = _repository.refresh_query(claims, db)
    if (
        session is None
        or session.user_id != UUID(claims["sub"])
        or session.deleted_at is not None
        or session.revoked_at is not None
        or session.expires_at <= datetime.now(UTC)
        or not secrets.compare_digest(session.refresh_jti_hash, hash_jti(claims["jti"]))
    ):
        raise unauthorized()
    user = _repository.refresh_query_2(db, session)
    if user is None:
        raise unauthorized()
    refresh_jti = secrets.token_urlsafe(32)
    session.refresh_jti_hash = hash_jti(refresh_jti)
    db.commit()
    return token_pair(user, session, refresh_jti)


def logout(db, session, user):
    session.revoked_at = _repository.logout_query()
    db.commit()


def me(user):
    from sqlalchemy.orm import object_session

    from app.services.access import version
    from app.services.access_control import context

    current = context(object_session(user), user)
    from app.core.permissions import DASHBOARDS

    return {
        **user_response(user),
        "permissions": sorted(current.capabilities),
        "default_permissions": sorted(current.defaults),
        "permissions_version": version(current),
        "dashboard_access": {
            key: current.allows("dashboards:read", "dashboard", key)
            if key == "overview"
            or any(
                p.resource_kind == "dashboard" and p.resource_id == key
                for p in [*current.policies, *current.denials]
            )
            else True
            for key in DASHBOARDS
        },
    }


def roles(db, user):
    from app.repositories.access import role_permissions

    return [
        {
            "code": role.code,
            "name": role.name,
            "permissions": sorted(
                ROLE_PERMISSIONS[role.code]
                if role.code == "admin"
                else role_permissions(db, role.id)
            ),
            "permissions_version": role.permissions_version,
        }
        for role in _repository.roles_query(db)
    ]


def users(response, db, admin, offset=0, limit=50):
    return [
        user_response(user) for user in page(db, _repository.users_query(), response, offset, limit)
    ]


def create_user(body, db, admin):
    role = get_role(db, body.role)
    user = User(
        username=body.username,
        password_hash=password_hasher.hash(body.password.get_secret_value()),
        role_id=role.id,
        notification_email=body.notification_email,
    )
    _repository.create_user_query(db, user)
    commit_user(db, user)
    return user_response(user)


def update_user(user_id, body, db, admin):
    # Consistent role -> user ordering also preserves concurrent last-admin checks.
    user = _repository.update_user_query(db, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    if body.is_active is False or (body.role is not None and body.role != "admin"):
        protect_admin(db, user)
    if body.role is not None:
        if body.role != user.role.code:
            from app.repositories.access import invalidate_user

            invalidate_user(db, user.id, admin.id)
        user.role_id = get_role(db, body.role).id
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.password is not None:
        user.password_hash = password_hasher.hash(body.password.get_secret_value())
    if body.notification_email is not None:
        user.notification_email = body.notification_email
    if body.password is not None or body.is_active is False:
        revoke_sessions(db, user.id)
    commit_user(db, user)
    return user_response(user)


def delete_user(user_id, db, admin):
    user = _repository.delete_user_query(db, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    protect_admin(db, user)
    user.deleted_at = _repository.delete_user_query_2()
    revoke_sessions(db, user.id)
    db.commit()
