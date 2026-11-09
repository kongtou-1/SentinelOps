from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import literal
from sqlalchemy.exc import IntegrityError

from app import models as m
from app.repositories import recycle_bin as repository
from app.repositories.notifications import audit


def resource(kind):
    if kind not in repository.RESOURCES:
        raise HTTPException(422, "该资源类型不支持回收站操作")
    return repository.RESOURCES[kind]


def allowed_types(db, user):
    from app.services.access_control import context

    current = context(db, user)
    if user.role.code == "admin":
        return {kind: literal(True) for kind in repository.RESOURCES}
    result = {"notifications": current.clause(m.Notification)}
    if current.has("analyses:run"):
        # Match conversation deletion, including ORM queries for scoped dependencies.
        current.required = "analyses:run"
        result["analysis-conversations"] = current.clause(m.AnalysisConversation, "analyses:run")
    return result


def dependencies(row):
    # Actor/account FKs are historical attribution, not resources to re-enable.
    models = {mapper.local_table.name: mapper.class_ for mapper in m.Server.registry.mappers}
    refs = []
    for column in row.__table__.columns:
        for fk in column.foreign_keys:
            value = getattr(row, column.key)
            if value and fk.column.table.name not in {"users", "roles", "agent_configs"}:
                refs.append((models[fk.column.table.name], value))
    if isinstance(row, (m.InspectionPlan, m.InspectionDataSource)):
        connection = (row.source_config or {}).get("connection_id")
        if connection:
            refs.append((m.ServiceDatabase, UUID(str(connection))))
        http = (row.source_config or {}).get("http_connection_id")
        if http:
            refs.append((m.InspectionHTTPConnection, UUID(str(http))))
    if isinstance(row, m.MaintenanceWindow):
        model = {
            "server": m.Server,
            "container": m.Container,
            "application": m.Application,
            "llm_endpoint": m.LLMEndpoint,
            "service_monitor": m.ServiceMonitor,
        }.get(row.scope_kind)
        if model:
            refs.append((model, row.scope_id))
    return sorted(set(refs), key=lambda pair: (pair[0].__tablename__, str(pair[1])))


def blocked(db, row, lock=False):
    for model, identifier in dependencies(row):
        parent = repository.get(db, model, identifier, lock=lock, shared=True)
        if parent is None or parent.deleted_at is not None:
            label = next(
                (s.label for s in repository.RESOURCES.values() if s.model is model), "关联资源"
            )
            return f"请先恢复关联的{label}（{identifier}）"
    return None


def listing(db, response, user, kind, search, offset, limit):
    access = allowed_types(db, user)
    if kind:
        resource(kind)
        if kind not in access:
            raise HTTPException(403, "无权管理该类资源的回收站")
    rows, total = repository.listing(db, user.id, access, kind, search, offset, limit)
    response.headers["X-Total-Count"] = str(total)
    result = []
    for values in rows:
        spec = resource(values["kind"])
        row = repository.get(db, spec.model, values["id"])
        result.append(
            {
                **values,
                "label": spec.label,
                "restore_note": spec.note,
                "blocked_reason": blocked(db, row),
            }
        )
    return result


def types(db, user):
    access = allowed_types(db, user)
    counts = repository.counts(db, user.id, access)
    return [
        {"kind": kind, "label": spec.label, "count": counts.get(kind, 0)}
        for kind, spec in repository.RESOURCES.items()
        if kind in access
    ]


def validate_inhibition(db, row):
    from app.services.incidents.logic import lock_incidents

    lock_incidents(db)
    edges = {}
    for parent, child in repository.inhibition_edges(db):
        edges.setdefault(parent, []).append(child)
    pending, visited = [row.child_rule_id], set()
    while pending:
        current = pending.pop()
        if current == row.parent_rule_id:
            raise HTTPException(409, "恢复该抑制关系会形成循环，请先调整现有关系")
        if current not in visited:
            visited.add(current)
            pending.extend(edges.get(current, []))


def restore(db, kind, identifier, body, user):
    spec = resource(kind)
    if kind not in allowed_types(db, user):
        raise HTTPException(403, "无权恢复该类资源")
    row = repository.get(db, spec.model, identifier)
    if row is None or row.deleted_at is None:
        raise HTTPException(404, "资源不在回收站中")
    if (kind == "analysis-conversations" and row.owner_id != user.id) or (
        kind == "notifications" and row.user_id != user.id
    ):
        raise HTTPException(404, "资源不在回收站中")
    if kind == "inspection-plans" and row.trial_only:
        raise HTTPException(409, "试运行快照不能作为正式任务恢复")
    if kind in {"alert-rules", "alert-inhibitions", "maintenance-windows"}:
        from app.services.incidents.logic import lock_incidents

        lock_incidents(db)
    # Lock parents before children, matching the business deletion order.
    reason = blocked(db, row, lock=True)
    if reason:
        raise HTTPException(409, reason)
    config = repository.lock_agent_config(db, row.module) if kind == "agent-channels" else None
    if kind == "agent-channels" and (config is None or config.deleted_at is not None):
        raise HTTPException(409, "模型模块配置不可用")
    if kind == "alert-inhibitions":
        validate_inhibition(db, row)
    row = repository.get(db, spec.model, identifier, lock=True)
    if row.deleted_at is None or row.deleted_at != body.expected_deleted_at:
        raise HTTPException(409, "资源状态已变化，请刷新回收站")
    previous, now = row.deleted_at, datetime.now(UTC)
    if hasattr(row, "enabled"):
        row.enabled = False
    if hasattr(row, "version"):
        row.version += 1
    if isinstance(row, m.InspectionPlan):
        row.next_run_at = None
    if isinstance(row, m.Server):
        row.monitoring_enabled = False
    if isinstance(row, m.LLMEndpoint):
        row.probe_enabled = False
    if isinstance(row, m.ServiceDatabase):
        row.query_enabled = False
        row.config_version += 1
    if isinstance(row, m.KnowledgeDocument):
        row.index_status = "pending"
    if isinstance(row, m.User):
        row.is_active = False
    if isinstance(row, m.Notification):
        row.read_at = row.read_at or now
        if row.mail_status in {"queued", "sending"}:
            row.mail_status = "disabled"
        row.finished_at = row.finished_at or now
    if isinstance(row, m.AgentChannel):
        row.position = (repository.last_channel_position(db, row.module) or 0) + 1
        config.version += 1
    children = (
        repository.restore_run_items(db, row, previous, now) if kind == "inspection-runs" else 0
    )
    row.deleted_at, row.updated_at = None, now
    audit(
        db,
        user,
        "resource_restored",
        "recycle_bin",
        row.id,
        {"resource_type": kind, "resource_label": spec.label, "restored_children": children},
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "恢复与当前资源冲突，请先处理重名或重复关联") from None
    return {"kind": kind, "id": row.id, "restored_children": children}


def delete(db, kind, identifier, body, user):
    spec = resource(kind)
    if kind not in allowed_types(db, user):
        raise HTTPException(403, "无权永久删除该类资源")
    row = repository.get(db, spec.model, identifier)
    if row is None or row.deleted_at is None:
        raise HTTPException(404, "资源不在回收站中")
    if (kind == "analysis-conversations" and row.owner_id != user.id) or (
        kind == "notifications" and row.user_id != user.id
    ):
        raise HTTPException(404, "资源不在回收站中")
    if kind == "inspection-plans" and row.trial_only:
        raise HTTPException(409, "试运行快照不支持此操作")
    # Match the plan -> run -> item lock order used by inspection workers and restoration.
    if isinstance(row, (m.InspectionRun, m.InspectionItem)):
        repository.get(db, m.InspectionPlan, row.plan_id, lock=True)
    if isinstance(row, m.InspectionItem):
        run = repository.get(db, m.InspectionRun, row.run_id, lock=True)
        if run and run.status in {"queued", "collecting", "evaluating"}:
            raise HTTPException(409, "请先停止本轮巡检")
    row = repository.get(db, spec.model, identifier, lock=True)
    if row is None or row.deleted_at is None or row.deleted_at != body.expected_deleted_at:
        raise HTTPException(409, "资源状态已变化，请刷新回收站")
    if (
        (isinstance(row, m.InspectionRun) and row.status in {"queued", "collecting", "evaluating"})
        or (isinstance(row, m.Notification) and row.mail_status == "sending")
        or (isinstance(row, m.PostmortemReport) and row.status in {"queued", "running"})
        or (isinstance(row, m.KnowledgeDocument) and row.index_status == "running")
    ):
        raise HTTPException(409, "资源仍在处理中，请等待处理结束")
    targets = repository.deletion_targets(kind, identifier)
    reason = repository.deletion_conflict(db, kind, identifier, targets)
    if reason:
        raise HTTPException(409, reason)
    try:
        repository.delete_targets(db, targets)
        audit(
            db,
            user,
            "resource_purged",
            "recycle_bin",
            identifier,
            {"resource_type": kind, "resource_label": spec.label},
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "资源仍有关联引用，无法永久删除，请刷新后处理关联记录") from None
