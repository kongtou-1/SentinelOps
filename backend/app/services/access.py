"""Grouped access requests, atomic approval and immediate revocation."""

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from app.core.permissions import (
    ADMIN_ONLY,
    BUNDLE_MAP,
    BUNDLES,
    CATALOGUE,
    DASHBOARDS,
    MODULES,
    RESOURCE_READS,
)
from app.models import (
    AccessGrant,
    AccessPolicy,
    AccessRequest,
    Notification,
    OperationsAuditLog,
    RolePermission,
)
from app.repositories import access as repository
from app.services.access_control import RESOURCE_MODELS, Authorization, context, record_allowed
from app.services.pagination import page


def admin(user):
    if user.role.code != "admin":
        raise HTTPException(403, "需要管理员权限")


def lock_admin(db, user):
    repository.role(db, "admin", lock=True)
    current = repository.active_user(db, user.id, lock=True)
    if current is None:
        raise HTTPException(403, "管理员账号已失效")
    admin(current)


def audit(db, user, action, kind, identifier=None, detail=None):
    repository.add(
        db,
        OperationsAuditLog(
            user_id=user.id,
            action=action,
            resource_kind=kind,
            resource_id=identifier,
            detail=detail or {},
        ),
    )


def notify(db, user_id, request_id, kind, title):
    repository.add(
        db,
        Notification(
            user_id=user_id,
            kind=kind,
            resource_kind="access_request",
            resource_id=request_id,
            dedupe_key=f"{kind}:{request_id}:{user_id}",
            title=title,
            detail={},
            email="",
            mail_status="disabled",
        ),
    )


def catalogue(user):
    return {
        "modules": MODULES,
        "permissions": [
            {
                **asdict(p),
                "resource_kinds": sorted(
                    {
                        scope
                        for bundle in BUNDLES
                        if p.code in bundle["permissions"]
                        for scope in bundle["scopes"]
                    }
                )
                or ["global"],
            }
            for p in CATALOGUE.values()
        ],
        "bundles": BUNDLES,
        "dashboards": DASHBOARDS,
    }


def version(current):
    payload = [
        sorted(current.defaults),
        [(str(g.id), str(g.updated_at), str(g.expires_at)) for g in current.grants],
        [(str(p.id), p.version) for p in current.policies],
        [(str(p.id), p.version) for p in current.denials],
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]


def update_role(db, user, code, body):
    admin(user)
    if code not in ("viewer", "operator"):
        raise HTTPException(422, "管理员权限不可修改")
    lock_admin(db, user)
    role = repository.role(db, code, lock=True)
    if role is None:
        raise HTTPException(404, "角色不存在")
    if role.permissions_version != body.expected_version:
        raise HTTPException(409, "角色权限版本已变化")
    desired = set(body.permissions)
    if not desired <= CATALOGUE.keys() or desired & ADMIN_ONLY:
        raise HTTPException(422, "包含未知权限或管理员专属权限")
    # Personal access and session pages cannot be removed by editing business defaults.
    desired.add("profile:read")
    existing = (
        repository.raw(db, select(RolePermission).where(RolePermission.role_id == role.id))
        .scalars()
        .all()
    )
    now = datetime.now(UTC)
    found = set()
    for row in existing:
        found.add(row.permission)
        row.deleted_at = None if row.permission in desired else now
    for permission in desired - found:
        repository.add(db, RolePermission(role_id=role.id, permission=permission))
    role.permissions_version += 1
    audit(
        db,
        user,
        "role_permissions_updated",
        "role",
        role.id,
        {"code": code, "version": role.permissions_version},
    )
    db.commit()
    return {
        "code": code,
        "name": role.name,
        "permissions": sorted(desired),
        "permissions_version": role.permissions_version,
    }


def target(db, kind, identifier):
    if kind == "global":
        if identifier != "*":
            raise HTTPException(422, "全局范围标识无效")
        return "普通资源"
    if kind == "dashboard":
        if identifier not in DASHBOARDS:
            raise HTTPException(404, "看板不存在")
        return DASHBOARDS[identifier]
    model = RESOURCE_MODELS.get(kind)
    try:
        key = UUID(identifier)
    except (ValueError, TypeError):
        raise HTTPException(422, "资源标识无效") from None
    if model is None:
        raise HTTPException(422, "资源类型无效")
    t = model.__table__
    name = repository.raw(
        db, select(t.c.name).where(t.c.id == key, t.c.deleted_at.is_(None))
    ).scalar()
    if name is None:
        raise HTTPException(404, "资源不存在")
    return name


def normalized_id(kind, identifier):
    if kind in ("global", "dashboard"):
        return identifier
    try:
        return str(UUID(identifier))
    except (ValueError, TypeError):
        raise HTTPException(422, "资源标识无效") from None


def targets(db, user, kind, q, offset, limit, response):
    if kind == "dashboard":
        result = [
            {"id": key, "name": value, "kind": kind}
            for key, value in DASHBOARDS.items()
            if not q or q in value
        ]
        response.headers["X-Total-Count"] = str(len(result))
        return result[offset : offset + limit]
    if kind == "global":
        response.headers["X-Total-Count"] = "1"
        return [{"id": "*", "name": "普通资源", "kind": "global"}]
    model = RESOURCE_MODELS.get(kind)
    if model is None:
        raise HTTPException(422, "资源类型无效")
    table = model.__table__
    query = select(table.c.id, table.c.name).where(table.c.deleted_at.is_(None))
    if q:
        query = query.where(table.c.name.ilike(f"%{q}%"))
    from sqlalchemy import func

    response.headers["X-Total-Count"] = str(
        repository.raw(db, select(func.count()).select_from(query.subquery())).scalar()
    )
    return [
        {"id": str(row.id), "name": row.name, "kind": kind}
        for row in repository.raw(
            db, query.order_by(table.c.name, table.c.id).offset(offset).limit(limit)
        )
    ]


def canonical_items(db, items):
    result = []
    for item in items:
        name = target(db, item.resource_kind, item.resource_id)
        codes = set()
        for key in item.bundle_ids:
            if key not in BUNDLE_MAP:
                raise HTTPException(422, "权限组不存在")
            if item.resource_kind not in BUNDLE_MAP[key]["scopes"]:
                raise HTTPException(422, "该权限组不适用于所选资源范围")
            codes.update(BUNDLE_MAP[key]["permissions"])
        if codes & ADMIN_ONLY or any(not CATALOGUE[code].requestable for code in codes):
            raise HTTPException(422, "该权限不可申请")
        if item.resource_kind == "dashboard" and codes != {"dashboards:read"}:
            raise HTTPException(422, "看板范围仅支持看板查看权限")
        if (
            item.resource_kind != "dashboard"
            and "dashboards:read" in codes
            and item.resource_kind != "global"
        ):
            raise HTTPException(422, "请为看板选择看板范围")
        result.append(
            {
                **item.model_dump(),
                "resource_id": normalized_id(item.resource_kind, item.resource_id),
                "bundle_ids": sorted(set(item.bundle_ids)),
                "permissions": sorted(codes),
                "resource_name": name,
            }
        )
    return sorted(
        result, key=lambda row: (row["resource_kind"], row["resource_id"], row["bundle_ids"])
    )


def request_permission_owned(current, permission, kind, identifier, include_descendants):
    if not current.allows(permission, kind, identifier):
        return False
    if current.admin or not include_descendants or kind not in ("application", "server"):
        return True
    covers_children = any(
        grant.permission == permission
        and grant.resource_kind == kind
        and grant.resource_id == identifier
        and grant.include_descendants
        for grant in current.grants
    )
    # An exact-object grant does not cover future children. Defaults and global
    # grants cover ordinary children; restricted children must be checked too.
    if (
        not covers_children
        and permission not in current.defaults
        and not any(
            grant.permission == permission and grant.resource_kind == "global"
            for grant in current.grants
        )
    ):
        return False
    if not covers_children and any(
        p.resource_kind == kind and p.resource_id == identifier for p in current.policies
    ):
        return False
    children = (
        ("container", "service_monitor", "service_database")
        if kind == "application"
        else ("container",)
    )
    for child_kind in children:
        table = RESOURCE_MODELS[child_kind].__table__
        parent = table.c.application_id if kind == "application" else table.c.server_id
        denied = select(table.c.id).where(
            parent == UUID(identifier),
            table.c.deleted_at.is_(None),
            ~current.resource_clause(child_kind, table.c.id, permission),
        )
        if repository.raw(current.db, denied.limit(1)).first():
            return False
    return True


def create_request(db, user, body):
    repository.role(db, user.role.code, lock=True)
    if repository.active_user(db, user.id, lock=True) is None:
        raise HTTPException(401, "账号已失效")
    items = canonical_items(db, body.items)
    rows = (
        repository.raw(
            db,
            select(AccessRequest).where(
                AccessRequest.user_id == user.id,
                AccessRequest.deleted_at.is_(None),
                (AccessRequest.idempotency_key == body.idempotency_key)
                | (AccessRequest.status == "pending"),
            ),
        )
        .scalars()
        .all()
    )
    for row in rows:
        same = (
            row.items == items
            and row.reason == body.reason
            and row.duration_days == body.duration_days
        )
        if row.idempotency_key == body.idempotency_key and not same:
            raise HTTPException(409, "申请标识已用于其他内容")
        if row.idempotency_key == body.idempotency_key or same:
            return row
    current = Authorization(db, user)
    for item in items:
        for bundle_id in item["bundle_ids"]:
            if all(
                request_permission_owned(
                    current,
                    permission,
                    item["resource_kind"],
                    item["resource_id"],
                    item["include_descendants"],
                )
                for permission in BUNDLE_MAP[bundle_id]["permissions"]
            ):
                raise HTTPException(409, "已拥有所选资源的权限，请刷新后仅申请缺少的权限")
    row = AccessRequest(
        user_id=user.id,
        idempotency_key=body.idempotency_key,
        items=items,
        reason=body.reason,
        duration_days=body.duration_days,
    )
    repository.add(db, row)
    db.flush()
    audit(db, user, "access_requested", "access_request", row.id)
    from app.repositories.notifications import admins

    for administrator in admins(db):
        if administrator.id != user.id:
            notify(db, administrator.id, row.id, "access_pending", "权限申请待审批")
    db.commit()
    return row


def request_detail(db, user, identifier, lock=False):
    row = repository.request(db, identifier, lock)
    if row is None or (user.role.code != "admin" and row.user_id != user.id):
        raise HTTPException(404, "申请不存在")
    return row


def requests(db, user, response, status, offset, limit):
    query = select(AccessRequest).where(AccessRequest.deleted_at.is_(None))
    if user.role.code != "admin":
        query = query.where(AccessRequest.user_id == user.id)
    if status:
        query = query.where(AccessRequest.status == status)
    return page(
        db,
        query.order_by(AccessRequest.created_at.desc(), AccessRequest.id.desc()),
        response,
        offset,
        limit,
    )


def decide(db, user, identifier, body, approved):
    admin(user)
    # Same role -> user -> request order as account changes, serializing demotion and approval.
    for code in ("admin", "operator", "viewer"):
        repository.role(db, code, lock=True)
    lock_admin(db, user)
    row = request_detail(db, user, identifier)
    applicant = repository.active_user(db, row.user_id, lock=True)
    row = request_detail(db, user, identifier, lock=True)
    if row.user_id == user.id:
        raise HTTPException(403, "不能审批自己的申请")
    if row.status != "pending":
        raise HTTPException(409, "申请已处理")
    if applicant is None:
        raise HTTPException(409, "申请人已失效")
    days = body.duration_days if "duration_days" in body.model_fields_set else row.duration_days
    if row.duration_days is not None and (days is None or days > row.duration_days):
        raise HTTPException(422, "不能延长申请期限")
    now = datetime.now(UTC)
    if approved:
        for item in row.items:
            target(db, item["resource_kind"], item["resource_id"])
            for code in item["permissions"]:
                if code not in CATALOGUE or not CATALOGUE[code].requestable:
                    raise HTTPException(409, "权限目录已变化，请重新申请")
                exists = repository.raw(
                    db,
                    select(AccessGrant.id).where(
                        AccessGrant.request_id == row.id,
                        AccessGrant.permission == code,
                        AccessGrant.resource_kind == item["resource_kind"],
                        AccessGrant.resource_id == item["resource_id"],
                    ),
                ).first()
                if not exists:
                    repository.add(
                        db,
                        AccessGrant(
                            request_id=row.id,
                            user_id=row.user_id,
                            permission=code,
                            resource_kind=item["resource_kind"],
                            resource_id=item["resource_id"],
                            include_descendants=item["include_descendants"],
                            expires_at=now + timedelta(days=days) if days else None,
                        ),
                    )
                    db.flush()
    row.status, row.decided_by, row.decided_at = (
        "approved" if approved else "rejected",
        user.id,
        now,
    )
    row.decision_comment = body.comment
    audit(db, user, "access_" + row.status, "access_request", row.id)
    notify(
        db,
        row.user_id,
        row.id,
        "access_" + row.status,
        "权限申请已批准" if approved else "权限申请已拒绝",
    )
    db.commit()
    return row


def withdraw(db, user, identifier):
    row = request_detail(db, user, identifier, lock=True)
    if row.user_id != user.id:
        raise HTTPException(403, "仅申请人可撤回")
    if row.status != "pending":
        raise HTTPException(409, "申请已处理")
    row.status, row.decided_at = "withdrawn", datetime.now(UTC)
    audit(db, user, "access_withdrawn", "access_request", row.id)
    db.commit()
    return row


def grants(db, user, response, offset, limit):
    query = select(AccessGrant).where(AccessGrant.deleted_at.is_(None))
    if user.role.code != "admin":
        query = query.where(AccessGrant.user_id == user.id)
    return page(
        db, query.order_by(AccessGrant.created_at.desc(), AccessGrant.id), response, offset, limit
    )


def revoke(db, user, identifier):
    admin(user)
    lock_admin(db, user)
    row = repository.raw(
        db,
        select(AccessGrant)
        .where(AccessGrant.id == identifier, AccessGrant.deleted_at.is_(None))
        .with_for_update(),
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "授权不存在")
    # Revoke the selected permission group together, keeping the UI operationally simple.
    for grant in repository.raw(
        db,
        select(AccessGrant)
        .where(
            AccessGrant.request_id == row.request_id,
            AccessGrant.resource_kind == row.resource_kind,
            AccessGrant.resource_id == row.resource_id,
            AccessGrant.revoked_at.is_(None),
        )
        .with_for_update(),
    ).scalars():
        grant.revoked_at, grant.revoked_by = datetime.now(UTC), user.id
    audit(db, user, "access_revoked", "access_request", row.request_id)
    db.commit()


def policy(db, user, kind, identifier, body=None, user_id=None):
    admin(user)
    if kind == "global":
        raise HTTPException(422, "请对具体资源设置访问策略")
    identifier = normalized_id(kind, identifier)
    target(db, kind, identifier)
    if body is not None:
        # Same lock order as role changes and access approvals.
        for code in ("admin", "operator", "viewer"):
            repository.role(db, code, lock=True)
        lock_admin(db, user)
    subject = None
    if user_id is not None:
        subject = repository.active_user(db, user_id, lock=body is not None)
        if subject is None:
            raise HTTPException(404, "用户不存在或已停用")
        if subject.role.code == "admin":
            raise HTTPException(422, "管理员账号不参与资源访问限制")
    row = repository.policy(db, kind, identifier, lock=body is not None, user_id=user_id)
    if body is not None:
        if body.expected_version != (row.version if row else 0):
            raise HTTPException(409, "资源策略已变化")
        if row is None:
            row = AccessPolicy(
                resource_kind=kind,
                resource_id=identifier,
                user_id=user_id,
                updated_by=user.id,
                version=0,
            )
            repository.add(db, row)
        row.restricted, row.updated_by, row.version = body.restricted, user.id, row.version + 1
        audit(
            db,
            user,
            "access_policy_updated",
            kind,
            None,
            {
                "resource_id": identifier,
                "restricted": body.restricted,
                "subject_user_id": str(user_id) if user_id else None,
            },
        )
        db.commit()
    return {
        "resource_kind": kind,
        "resource_id": identifier,
        "user_id": user_id,
        "restricted": row.restricted if row else False,
        "version": row.version if row else 0,
        "allowed": (
            Authorization(db, subject).allows("dashboards:read", kind, identifier)
            if kind == "dashboard"
            else record_allowed(
                db, subject, RESOURCE_MODELS[kind], UUID(identifier), RESOURCE_READS[kind]
            )
        )
        if subject
        else None,
    }


def checks(db, user, body):
    current = context(db, user)
    result = []
    for item in body.items:
        if bool(item.resource_kind) != bool(item.resource_id):
            raise HTTPException(422, "资源类型和标识需同时提供")
        if item.resource_kind:
            target(db, item.resource_kind, item.resource_id)
        result.append(
            {
                **item.model_dump(),
                "allowed": request_permission_owned(
                    current,
                    item.permission,
                    item.resource_kind,
                    item.resource_id,
                    item.include_descendants,
                ),
                "requestable": item.permission in CATALOGUE
                and CATALOGUE[item.permission].requestable,
            }
        )
    return result
