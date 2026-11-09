"""Business grouping changes resource metadata only, never deployment or server state."""

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException

import app.repositories.resources.business_services as _repository
from app.core.security import check_permission
from app.models import Application, Server
from app.schemas.resources import (
    BusinessServiceGroup,
    BusinessServiceResponse,
    ContainerResponse,
)
from app.services.pagination import page
from app.services.records import active_record, save
from app.services.sources.catalog import service_owner, set_application


def attach_services(db, result):
    containers = result["containers"]
    applications = _repository.attach_services_query(containers, db)
    active_ids = {a.id for a in applications}
    # A recreated Compose instance has a new Docker ID. Resolve its business
    # grouping from the exact host/project/service/name slot, without changing
    # stored membership, deployment bindings or historical container evidence.
    current_ids = {
        o["container_id"]
        for o in result["observations"]
        if o.get("state") and o["state"] != "unknown"
    }
    slots = {}
    for container in containers:
        identity = container.identity or {}
        project, service = identity.get("compose_project"), identity.get("compose_service")
        if project and service:
            slot = (container.server_id, project, service, container.name)
            slots.setdefault(slot, []).append(container)
    effective = {c.id: c for c in containers}
    for instances in slots.values():
        current = [c for c in instances if c.id in current_ids]
        owners = {c.application_id for c in instances if c.application_id in active_ids}
        if len(current) != 1 or len(owners) > 1:
            continue
        instance = current[0]
        if owners and instance.application_id not in active_ids:
            effective[instance.id] = ContainerResponse.model_validate(instance).model_copy(
                update={"application_id": next(iter(owners))}
            )
        for previous in instances:
            if previous.id != instance.id:
                effective.pop(previous.id)
    containers = list(effective.values())
    result["containers"] = containers
    result["observations"] = [o for o in result["observations"] if o["container_id"] in effective]
    result["services"] = [
        BusinessServiceGroup(
            **BusinessServiceResponse.model_validate(a).model_dump(),
            container_ids=[c.id for c in containers if c.application_id == a.id],
        )
        for a in applications
    ]
    result["unassigned_container_ids"] = [
        c.id for c in containers if c.application_id not in active_ids
    ]
    return result


def services(response, db, user, offset=0, limit=50):
    return page(
        db,
        _repository.services_query(),
        response,
        offset,
        limit,
    )


def lock_server(db, server_id):
    server = active_record(db, Server, server_id)
    _repository.lock_server_query(db, server)
    if server.deleted_at:
        raise HTTPException(404, "Resource not found")
    return server


def members(db, server_id, application, container_ids):
    desired = set(container_ids)
    inventory = _repository.members_query(db, server_id)
    if not desired <= {c.id for c in inventory}:
        raise HTTPException(422, "请选择当前服务器尚未退役的容器")
    owners = _repository.members_query_2(db)
    for container in inventory:
        if container.id in desired:
            if container.application_id in owners and container.application_id != application.id:
                raise HTTPException(409, "容器已归属其他服务，请先从原服务移出")
            container.application_id = application.id
        elif container.application_id == application.id:
            container.application_id = None


def profile(db, row, body, user):
    owner = service_owner(db, body.owner, row.owner)
    if body.source_connection is not None:
        check_permission(user, "sources:manage")
        set_application(row, body.source_connection, db)
    dependencies = list(dict.fromkeys(body.depends_on))
    if row.id in dependencies:
        raise HTTPException(422, "服务不能依赖自身")
    for dependency in dependencies:
        active_record(db, Application, dependency)
    row.name, row.purpose, row.owner = body.name.strip(), body.purpose.strip(), owner
    row.depends_on = [str(v) for v in dependencies]


def audit(db, row, user, server_id, action, container_ids):
    _repository.audit_query(action, container_ids, db, row, server_id, user)


def create_service(server_id, body, db, user):
    lock_server(db, server_id)
    row = Application(id=uuid4(), code=body.code)
    profile(db, row, body, user)
    _repository.create_service_query(db, row)
    # Existing members must be validated before saving the new service.
    with db.no_autoflush:
        members(db, server_id, row, body.container_ids)
    audit(db, row, user, server_id, "business_service_created", body.container_ids)
    return save(db, row)


def update_service(server_id, application_id, body, db, user):
    lock_server(db, server_id)
    row = active_record(db, Application, application_id)
    _repository.update_service_query(db, row)
    profile(db, row, body, user)
    with db.no_autoflush:
        members(db, server_id, row, body.container_ids)
    audit(db, row, user, server_id, "business_service_updated", body.container_ids)
    return save(db, row)


def assign_service(server_id, application_id, body, db, user):
    lock_server(db, server_id)
    row = active_record(db, Application, application_id)
    _repository.assign_service_query(db, row)
    members(db, server_id, row, body.container_ids)
    row.updated_at = datetime.now(UTC)
    audit(db, row, user, server_id, "business_service_members_updated", body.container_ids)
    return save(db, row)
