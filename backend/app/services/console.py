"""Read-only projections for the web console; authorization remains in existing actions."""

from datetime import UTC, datetime

import app.repositories.console as _repository
from app.models import (
    AlertEvent,
    Approval,
    Container,
    Incident,
    PostmortemReport,
    Server,
    SkillExecution,
)
from app.schemas.console import ConsoleItem, DashboardSummary
from app.services.llm.status import endpoint_status
from app.services.pagination import page, time_range
from app.services.reports.resources import resource_context


def listing(
    db, response, model, actor_field, status, incident_id, actor_id, start, end, offset, limit
):
    query = _repository.listing_query(model)
    now = datetime.now(UTC)
    state = model.status
    if model is Approval:
        state = _repository.listing_query_2(now)
    if status:
        query = _repository.listing_query_3(query, state, status)
    if incident_id:
        query = _repository.listing_query_4(incident_id, model, query)
    if actor_id:
        query = _repository.listing_query_5(actor_field, actor_id, model, query)
    rows = page(
        db,
        _repository.listing_query_6(model, time_range(query, model.created_at, start, end)),
        response,
        offset,
        limit,
    )
    containers, incidents, users = _repository.listing_query_7(actor_field, db, rows)
    result = []
    for row in rows:
        container_id = getattr(row, "container_id", None)
        result.append(
            ConsoleItem(
                id=row.id,
                incident_id=row.incident_id,
                incident_title=incidents[row.incident_id].title,
                severity=incidents[row.incident_id].severity,
                status=(
                    "expired"
                    if model is Approval
                    and row.status in ("pending", "approved")
                    and row.expires_at <= now
                    else row.status
                ),
                actor_id=getattr(row, actor_field),
                actor_username=users.get(getattr(row, actor_field), "已删除用户"),
                created_at=row.created_at,
                finished_at=getattr(row, "finished_at", None),
                expires_at=getattr(row, "expires_at", None),
                container_id=container_id,
                container_name=containers.get(container_id),
                action="restart_container" if model in (Approval, SkillExecution) else None,
                revision=getattr(row, "revision", None),
                error_code=row.error_code,
                resource_context=resource_context(db, row.snapshot)
                if model is PostmortemReport
                else None,
            )
        )
    return result


def approvals(
    db,
    response,
    user,
    status=None,
    incident_id=None,
    actor_id=None,
    start=None,
    end=None,
    offset=0,
    limit=50,
):
    return listing(
        db,
        response,
        Approval,
        "requested_by",
        status,
        incident_id,
        actor_id,
        start,
        end,
        offset,
        limit,
    )


def executions(
    db,
    response,
    user,
    status=None,
    incident_id=None,
    actor_id=None,
    start=None,
    end=None,
    offset=0,
    limit=50,
):
    return listing(
        db,
        response,
        SkillExecution,
        "initiated_by",
        status,
        incident_id,
        actor_id,
        start,
        end,
        offset,
        limit,
    )


def reports(
    db,
    response,
    user,
    status=None,
    incident_id=None,
    actor_id=None,
    start=None,
    end=None,
    offset=0,
    limit=50,
):
    return listing(
        db,
        response,
        PostmortemReport,
        "requested_by",
        status,
        incident_id,
        actor_id,
        start,
        end,
        offset,
        limit,
    )


def counts(db, model):
    return dict(_repository.counts_query(db, model).all())


def summary(db, user):
    endpoints = _repository.summary_query(db)
    llm_counts = {}
    for endpoint in endpoints:
        state = endpoint_status(endpoint)
        llm_counts[state] = llm_counts.get(state, 0) + 1

    def count(model, *conditions):
        return _repository.summary_count_query(conditions, db, model)

    return DashboardSummary(
        servers_total=count(Server),
        servers_monitored=count(Server, Server.monitoring_enabled.is_(True)),
        containers_total=count(Container),
        containers_by_status=counts(db, Container),
        containers_last_seen_at=_repository.summary_query_2(db),
        llm_total=len(endpoints),
        llm_by_status=llm_counts,
        approvals_pending=count(
            Approval, Approval.status == "pending", Approval.expires_at > datetime.now(UTC)
        ),
        alerts_by_status=counts(db, AlertEvent),
        alerts_unacknowledged=count(
            AlertEvent, AlertEvent.status == "firing", AlertEvent.acknowledged_at.is_(None)
        ),
        incidents_by_status=counts(db, Incident),
    )
