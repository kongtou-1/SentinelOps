"""Access persistence. Transactions belong to the access service."""

from datetime import UTC, datetime

from sqlalchemy import or_, select

from app.models import AccessGrant, AccessPolicy, AccessRequest, Role, RolePermission, User


def raw(db, statement):
    return db.execute(statement.execution_options(access_internal=True))


def role(db, code, lock=False):
    statement = select(Role).where(Role.code == code, Role.deleted_at.is_(None))
    return raw(db, statement.with_for_update() if lock else statement).scalar_one_or_none()


def role_permissions(db, role_id):
    return set(
        raw(
            db,
            select(RolePermission.permission).where(
                RolePermission.role_id == role_id, RolePermission.deleted_at.is_(None)
            ),
        ).scalars()
    )


def active_user(db, user_id, lock=False):
    statement = (
        select(User)
        .join(Role)
        .where(
            User.id == user_id,
            User.is_active.is_(True),
            User.deleted_at.is_(None),
            Role.deleted_at.is_(None),
        )
        .execution_options(populate_existing=True)
    )
    return raw(db, statement.with_for_update(of=User) if lock else statement).scalar_one_or_none()


def grants(db, user_id):
    return (
        raw(
            db,
            select(AccessGrant).where(
                AccessGrant.user_id == user_id,
                AccessGrant.deleted_at.is_(None),
                AccessGrant.revoked_at.is_(None),
                or_(AccessGrant.expires_at.is_(None), AccessGrant.expires_at > datetime.now(UTC)),
            ),
        )
        .scalars()
        .all()
    )


def policies(db, user_id=None):
    return (
        raw(
            db,
            select(AccessPolicy).where(
                AccessPolicy.deleted_at.is_(None),
                AccessPolicy.restricted.is_(True),
                AccessPolicy.user_id == user_id,
            ),
        )
        .scalars()
        .all()
    )


def policy(db, kind, identifier, lock=False, user_id=None):
    statement = select(AccessPolicy).where(
        AccessPolicy.resource_kind == kind,
        AccessPolicy.resource_id == identifier,
        AccessPolicy.user_id == user_id,
        AccessPolicy.deleted_at.is_(None),
    )
    return raw(db, statement.with_for_update() if lock else statement).scalar_one_or_none()


def request(db, identifier, lock=False):
    statement = select(AccessRequest).where(
        AccessRequest.id == identifier, AccessRequest.deleted_at.is_(None)
    )
    return raw(db, statement.with_for_update() if lock else statement).scalar_one_or_none()


def invalidate_user(db, identifier, actor_id):
    now = datetime.now(UTC)
    for grant in raw(
        db,
        select(AccessGrant).where(
            AccessGrant.user_id == identifier,
            AccessGrant.revoked_at.is_(None),
            AccessGrant.deleted_at.is_(None),
        ),
    ).scalars():
        grant.revoked_at, grant.revoked_by = now, actor_id
    for row in raw(
        db,
        select(AccessRequest)
        .where(
            AccessRequest.user_id == identifier,
            AccessRequest.status == "pending",
            AccessRequest.deleted_at.is_(None),
        )
        .with_for_update(),
    ).scalars():
        row.status, row.decided_at, row.decided_by = "invalidated", now, actor_id


def add(db, record):
    db.add(record)


def capability_condition(permission):
    now = datetime.now(UTC)
    return or_(
        Role.code == "admin",
        select(RolePermission.id)
        .where(
            RolePermission.role_id == User.role_id,
            RolePermission.permission == permission,
            RolePermission.deleted_at.is_(None),
        )
        .exists(),
        select(AccessGrant.id)
        .where(
            AccessGrant.user_id == User.id,
            AccessGrant.permission == permission,
            AccessGrant.deleted_at.is_(None),
            AccessGrant.revoked_at.is_(None),
            or_(AccessGrant.expires_at.is_(None), AccessGrant.expires_at > now),
        )
        .exists(),
    )
