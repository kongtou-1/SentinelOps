from collections import deque
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException

from app.models import (
    Application,
    Incident,
    IncidentRelation,
    ServiceMonitor,
    ServiceOperationsPolicy,
)
from app.repositories import operations_impact as repository
from app.repositories.operations import service_audit
from app.services.incidents.logic import append_timeline, lock_incidents
from app.services.monitoring.service_probes import status as monitor_status
from app.services.pagination import page
from app.services.records import active_record


def read_policy(db, application_id):
    active_record(db, Application, application_id)
    row = repository.policy(db, application_id)
    return {
        "version": row.version if row else 0,
        "recovery_seconds": row.recovery_seconds if row else 180,
        "response": row.response_config if row else {},
    }


def configure_policy(db, application_id, body, actor=None):
    application = active_record(db, Application, application_id)
    # Serialize initial creation as well as subsequent version checks.
    lock_incidents(db)
    row = repository.policy(db, application.id, lock=True)
    if body.expected_version != (row.version if row else 0):
        raise HTTPException(409, "Policy version changed")
    if not row:
        row = ServiceOperationsPolicy(application_id=application.id, version=0)
        db.add(row)
    row.recovery_seconds, row.version = body.recovery_seconds, row.version + 1
    if body.response is not None:
        from app.repositories.notifications import active_user

        config = body.response.model_dump(mode="json")
        for field in ("primary_id", "backup_id"):
            if config[field] and not active_user(db, UUID(config[field]), handler=True):
                raise HTTPException(422, "Response owner must be an active Operator or Admin")
        for identifier in body.response.notify_user_ids:
            if not active_user(db, identifier):
                raise HTTPException(422, "Notification recipient is unavailable")
        row.response_config = config
    if actor:
        service_audit(
            db,
            application_id,
            actor,
            "service_operations_policy_saved",
            {"version": row.version, "recovery_seconds": row.recovery_seconds},
        )
    db.commit()
    return read_policy(db, application_id)


def recovery_seconds(db, rule):
    if rule.service_monitor_id:
        monitor = db.get(ServiceMonitor, rule.service_monitor_id)
        policy = repository.policy(db, monitor.application_id) if monitor else None
        if policy:
            return policy.recovery_seconds
    return 180


def graph(applications, containers=()):
    nodes = {str(row.id): row for row in applications}
    upstream = {key: set() for key in nodes}
    downstream = {key: set() for key in nodes}
    for key, row in nodes.items():
        for dependency in row.depends_on or []:
            if str(dependency) in nodes and str(dependency) != key:
                upstream[key].add(str(dependency))
                downstream[str(dependency)].add(key)
    inventory = {str(row.id): row for row in containers}
    for row in inventory.values():
        key = str(row.application_id)
        if key not in nodes:
            continue
        for dependency in row.depends_on or []:
            target = inventory.get(str(dependency))
            parent = str(target.application_id) if target else ""
            if parent in nodes and parent != key:
                upstream[key].add(parent)
                downstream[parent].add(key)
    return nodes, upstream, downstream


def walk(start, adjacency, remaining=100):
    visited, result, truncated = {start}, [], False
    pending = deque([(start, 0)])
    while pending:
        key, depth = pending.popleft()
        for child in sorted(adjacency.get(key, ())):
            if child in visited:
                continue
            if depth >= 3 or len(result) >= remaining:
                truncated = True
                continue
            visited.add(child)
            result.append((child, depth + 1))
            pending.append((child, depth + 1))
    return result, truncated


def impact(db, application_id):
    active_record(db, Application, application_id)
    inventory = repository.containers(db)
    nodes, upstream, downstream = graph(repository.applications(db), inventory)
    key = str(application_id)
    observed = {}
    now = datetime.now(UTC)
    bindings = repository.endpoint_bindings(db)
    endpoint_ids = {
        endpoint.id for binding, endpoint in bindings if binding.application_id == application_id
    }
    shared_ids = {
        str(binding.application_id)
        for binding, endpoint in bindings
        if endpoint.id in endpoint_ids
        and binding.application_id != application_id
        and str(binding.application_id) in nodes
    }
    resources = []
    from app.services.llm.status import endpoint_status

    for binding, endpoint in bindings:
        if binding.application_id == application_id:
            resources.append(
                {
                    "kind": "llm",
                    "id": str(endpoint.id),
                    "name": endpoint.name,
                    "observed_status": endpoint_status(endpoint),
                    "observed_at": endpoint.last_probe_at,
                }
            )
    direct = {str(row.id) for row in inventory if row.application_id == application_id}
    direct.update(
        str(value)
        for row in inventory
        if row.application_id == application_id
        for value in row.depends_on or []
    )
    for container in inventory:
        if str(container.id) in direct:
            fresh = (
                container.last_seen_at and 0 <= (now - container.last_seen_at).total_seconds() <= 60
            )
            resources.append(
                {
                    "kind": "container",
                    "id": str(container.id),
                    "name": container.name,
                    "observed_status": ("healthy" if container.status == "running" else "failed")
                    if fresh
                    else "unknown",
                    "observed_at": container.last_seen_at,
                }
            )
    for monitor in repository.monitors(db):
        if monitor.enabled and monitor.importance == "critical":
            observed.setdefault(str(monitor.application_id), []).append(
                monitor_status(monitor, now)
            )
    parents, cut_up = walk(key, upstream, 99)
    children, cut_down = walk(key, downstream, max(0, 99 - len(parents)))
    shared_ids -= {identifier for identifier, depth in [*parents, *children]}

    def item(identifier, depth, relation):
        values = observed.get(identifier, [])
        state = (
            "failed"
            if "failed" in values
            else ("healthy" if values and all(v == "healthy" for v in values) else "unknown")
        )
        return {
            "id": identifier,
            "name": nodes[identifier].name,
            "depth": depth,
            "relation": relation,
            "observed_status": state,
            "impact": "observed_failure" if state == "failed" else "potential",
        }

    return {
        "application_id": application_id,
        "upstream": [item(i, d, "upstream") for i, d in parents],
        "downstream": [item(i, d, "downstream") for i, d in children],
        "shared": [
            item(i, 1, "shared_llm")
            for i in sorted(shared_ids)[: max(0, 99 - len(parents) - len(children))]
        ],
        "resources": resources[:100],
        "truncated": cut_up
        or cut_down
        or len(resources) > 100
        or len(shared_ids) > max(0, 99 - len(parents) - len(children)),
        "checked_at": now,
    }


def service_ids(db, incident_id):
    return set().union(
        *(
            repository.event_applications(db, event)
            for event in repository.incident_events(db, incident_id)
        )
    )


def add_candidates(db, incident):
    ids = service_ids(db, incident.id)
    if not ids:
        return
    nodes, upstream, downstream = graph(repository.applications(db), repository.containers(db))
    connected = set()
    bindings = repository.endpoint_bindings(db)
    endpoint_ids = {endpoint.id for binding, endpoint in bindings if binding.application_id in ids}
    shared = {
        binding.application_id
        for binding, endpoint in bindings
        if endpoint.id in endpoint_ids and binding.application_id not in ids
    }
    for identifier in ids:
        for direction in (upstream, downstream):
            connected.update(UUID(i) for i, _ in walk(str(identifier), direction)[0])
    for recurrence in (True, False):
        for other in repository.recent_incidents(db, incident, recurrence):
            other_ids = service_ids(db, other.id)
            common = ids & other_ids
            if recurrence and not common:
                continue
            if not recurrence and not (common or connected & other_ids or shared & other_ids):
                continue
            kind = (
                "recurrence" if recurrence else "dependency" if connected & other_ids else "related"
            )
            if not repository.existing_relation(db, incident.id, other.id, kind):
                db.add(
                    IncidentRelation(
                        incident_id=incident.id,
                        related_incident_id=other.id,
                        kind=kind,
                        evidence={
                            "service_ids": sorted(str(i) for i in ids),
                            "related_service_ids": sorted(str(i) for i in other_ids),
                            "basis": "same_service_within_30m"
                            if recurrence
                            else "shared_llm_and_time"
                            if shared & other_ids and not connected & other_ids
                            else "explicit_relation_and_time",
                        },
                    )
                )
            if recurrence:
                break


def listing(db, incident_id, response, offset=0, limit=50):
    active_record(db, Incident, incident_id)
    return page(db, repository.relations(incident_id), response, offset, limit)


def decide(db, identifier, body, user):
    lock_incidents(db)
    row = repository.relation_for_update(db, identifier)
    if not row:
        raise HTTPException(404, "Relation not found")
    if row.version != body.expected_version:
        raise HTTPException(409, "Relation version changed")
    incident = active_record(db, Incident, row.incident_id)
    if incident.status in ("closed", "merged"):
        raise HTTPException(409, "Closed incident relations are read-only")
    row.status, row.decided_by, row.version = body.status, user.id, row.version + 1
    row.evidence = {**row.evidence, "comment": body.comment}
    append_timeline(
        db,
        incident,
        "relation_" + body.status,
        {"relation_id": str(row.id), "related_incident_id": str(row.related_incident_id)},
        actor=user,
    )
    db.commit()
    db.refresh(row)
    return row
