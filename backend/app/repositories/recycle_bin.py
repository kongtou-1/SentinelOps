"""Explicit catalogue of recoverable business resources; never credentials in responses."""

from dataclasses import dataclass

from sqlalchemy import String, cast, func, literal, or_, select, union_all, update

from app import models as m


@dataclass(frozen=True)
class Resource:
    model: type
    label: str
    name: str = "name"
    note: str = "恢复记录，保留原有历史数据"


PAUSED = "恢复后保持停用，请检查配置后手动启用"
RESOURCES = {
    "servers": Resource(m.Server, "服务器", note="恢复资产记录，监控保持停用，不操作远程服务器"),
    "containers": Resource(m.Container, "容器", note="仅恢复资产记录，不启动或修改实际容器"),
    "applications": Resource(
        m.Application, "应用与服务", note="保持停用；已清除的源码凭据需重新配置"
    ),
    "ssh-credentials": Resource(m.SSHCredential, "SSH 凭据"),
    "llm-endpoints": Resource(m.LLMEndpoint, "LLM 服务", note=PAUSED),
    "service-monitors": Resource(m.ServiceMonitor, "服务监测", note=PAUSED),
    "service-databases": Resource(m.ServiceDatabase, "数据库连接", note=PAUSED),
    "prediction-tasks": Resource(m.PredictionTask, "预测任务", note=PAUSED),
    "alert-rules": Resource(m.AlertRule, "告警规则", "code", PAUSED),
    "knowledge": Resource(m.KnowledgeDocument, "知识文档", "title", PAUSED),
    "skills": Resource(m.KnowledgeSkill, "Skill", "title", PAUSED),
    "inspection-plans": Resource(
        m.InspectionPlan, "巡检任务", note="恢复后保持暂停，原有巡检记录保留"
    ),
    "inspection-runs": Resource(
        m.InspectionRun,
        "巡检记录",
        "snapshot",
        "恢复本次记录及随本次删除的对象结果，不重新执行巡检",
    ),
    "inspection-items": Resource(m.InspectionItem, "巡检对象结果", "record_id"),
    "inspection-data-sources": Resource(m.InspectionDataSource, "巡检数据源", note=PAUSED),
    "inspection-http-connections": Resource(
        m.InspectionHTTPConnection, "巡检 API 连接", note=PAUSED
    ),
    "reports": Resource(
        m.PostmortemReport, "复盘报告", "snapshot", "恢复原报告与历史版本，不重新生成或发送"
    ),
    "analysis-conversations": Resource(m.AnalysisConversation, "诊断会话", "title"),
    "notifications": Resource(
        m.Notification, "个人通知", "title", "恢复为已读通知，不重新发送邮件"
    ),
    "service-objectives": Resource(m.ServiceObjective, "服务目标", note=PAUSED),
    "maintenance-windows": Resource(
        m.MaintenanceWindow, "维护窗口", "reason", "恢复后仍按原有效期生效"
    ),
    "alert-inhibitions": Resource(
        m.AlertInhibition, "告警抑制关系", "id", "恢复原有抑制关系，校验关联规则与循环依赖"
    ),
    "llm-bindings": Resource(m.LLMServiceBinding, "LLM 服务关联", "id"),
    "market-categories": Resource(m.MarketCategory, "资源中心分类", note=PAUSED),
    "agent-channels": Resource(m.AgentChannel, "Agent 模型通道", note=PAUSED),
    "users": Resource(m.User, "用户账号", "username", "恢复后保持禁用，原登录会话不恢复"),
}


def owner_clause(kind, user_id):
    if kind == "analysis-conversations":
        return m.AnalysisConversation.owner_id == user_id
    if kind == "notifications":
        return m.Notification.user_id == user_id
    return literal(True)


def deleted_query(user_id, access, kind=None, search=""):
    queries = []
    for key, spec in RESOURCES.items():
        if key not in access or (kind and key != kind):
            continue
        model = spec.model
        if key == "inspection-runs":
            name = func.coalesce(model.snapshot["plan"]["name"].as_string(), "巡检记录")
        elif key == "reports":
            name = func.coalesce(model.snapshot["incident"]["title"].as_string(), "复盘报告")
        elif key == "llm-bindings":
            app_name = (
                select(m.Application.name)
                .where(m.Application.id == model.application_id)
                .scalar_subquery()
            )
            endpoint_name = (
                select(m.LLMEndpoint.name)
                .where(m.LLMEndpoint.id == model.llm_endpoint_id)
                .scalar_subquery()
            )
            name = func.concat(app_name, " / ", endpoint_name)
        elif key == "alert-inhibitions":
            parent = (
                select(m.AlertRule.code)
                .where(m.AlertRule.id == model.parent_rule_id)
                .scalar_subquery()
            )
            child = (
                select(m.AlertRule.code)
                .where(m.AlertRule.id == model.child_rule_id)
                .scalar_subquery()
            )
            name = func.concat(parent, " → ", child)
        else:
            name = cast(getattr(model, spec.name), String)
        query = select(
            literal(key).label("kind"),
            model.id,
            name.label("name"),
            model.created_at,
            model.updated_at,
            model.deleted_at,
        ).where(model.deleted_at.is_not(None), owner_clause(key, user_id), access[key])
        if key == "inspection-plans":
            query = query.where(model.trial_only.is_(False))
        queries.append(query)
    combined = union_all(*queries).subquery()
    query = select(combined)
    if search:
        query = query.where(combined.c.name.icontains(search, autoescape=True))
    return query.order_by(combined.c.deleted_at.desc(), combined.c.kind, combined.c.id)


def listing(db, user_id, access, kind, search, offset, limit):
    query = deleted_query(user_id, access, kind, search)
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    return db.execute(query.offset(offset).limit(limit)).mappings().all(), total


def counts(db, user_id, access):
    query = deleted_query(user_id, access).order_by(None).subquery()
    return dict(db.execute(select(query.c.kind, func.count()).group_by(query.c.kind)).all())


def get(db, model, identifier, lock=False, shared=False):
    primary = next(iter(model.__table__.primary_key.columns))
    query = select(model).where(primary == identifier)
    if lock:
        query = query.with_for_update(read=shared, of=model)
    return db.scalar(query.execution_options(populate_existing=True))


def restore_run_items(db, run, deleted_at, now):
    return db.execute(
        update(m.InspectionItem)
        .where(
            m.InspectionItem.run_id == run.id,
            m.InspectionItem.deleted_at == deleted_at,
        )
        .values(deleted_at=None, updated_at=now)
    ).rowcount


def inhibition_edges(db):
    return db.execute(
        select(m.AlertInhibition.parent_rule_id, m.AlertInhibition.child_rule_id).where(
            m.AlertInhibition.deleted_at.is_(None)
        )
    ).all()


def lock_agent_config(db, module):
    return db.scalar(select(m.AgentConfig).where(m.AgentConfig.module == module).with_for_update())


def last_channel_position(db, module):
    return db.scalar(
        select(func.max(m.AgentChannel.position)).where(
            m.AgentChannel.module == module, m.AgentChannel.deleted_at.is_(None)
        )
    )


def deletion_targets(kind, identifier):
    """Only these owned details may be removed with a recycled resource."""
    root = RESOURCES[kind].model.__table__
    targets = {root: root.c.id == identifier}
    if kind == "knowledge":
        table = m.KnowledgeChunk.__table__
        targets[table] = table.c.document_id == identifier
    elif kind == "analysis-conversations":
        for model in (m.AnalysisEvent, m.AnalysisMessage, m.AnalysisRun):
            table = model.__table__
            targets[table] = table.c.conversation_id == identifier
    elif kind in {"inspection-runs", "inspection-items"}:
        checks = m.InspectionCheckResult.__table__
        traces = m.InspectionTrace.__table__
        if kind == "inspection-runs":
            items = m.InspectionItem.__table__
            targets[items] = items.c.run_id == identifier
            targets[checks] = checks.c.run_id == identifier
            targets[traces] = traces.c.run_id == identifier
        else:
            targets[checks] = checks.c.item_id == identifier
            targets[traces] = traces.c.check_result_id.in_(
                select(checks.c.id).where(targets[checks])
            )
    return targets


def deletion_conflict(db, kind, identifier, targets):
    # Core queries deliberately inspect all references, including soft-deleted or private rows.
    # The caller has already authorized the root; never return referencing records or secrets.
    if kind == "inspection-runs":
        items = m.InspectionItem.__table__
        if db.scalar(
            select(items.c.id).where(targets[items], items.c.deleted_at.is_(None)).limit(1)
        ):
            return "请先将本轮仍可见的巡检对象结果移入回收站"
    if kind == "analysis-conversations":
        runs = m.AnalysisRun.__table__
        if db.scalar(
            select(runs.c.id)
            .where(targets[runs], runs.c.status.in_(("queued", "running", "cancelling")))
            .limit(1)
        ):
            return "请先停止诊断并等待运行结束"
    for table in m.Base.metadata.sorted_tables:
        for fk in table.foreign_keys:
            parent = fk.column.table
            if parent not in targets:
                continue
            query = (
                select(literal(True))
                .select_from(table)
                .where(fk.parent.in_(select(fk.column).where(targets[parent])))
            )
            if table in targets:
                query = query.where(~targets[table])
            if db.scalar(query.limit(1)):
                label = next(
                    (s.label for s in RESOURCES.values() if s.model.__table__ is table),
                    "历史或关联记录",
                )
                return (
                    f"仍有{label}引用，无法永久删除；请先处理关联记录，保留的历史记录不可级联删除"
                )
    # Inspection connection/Skill references also live in JSON, without database FKs.
    if kind in {
        "service-databases",
        "inspection-http-connections",
        "skills",
        "inspection-data-sources",
    }:
        for model, fields in (
            (
                m.InspectionPlan,
                ("source_config", "related_sources", "agent_source_ids", "check_items"),
            ),
            (m.InspectionDataSource, ("source_config",)),
        ):
            table = model.__table__
            if db.scalar(
                select(table.c.id)
                .where(or_(*(cast(table.c[f], String).contains(str(identifier)) for f in fields)))
                .limit(1)
            ):
                return "仍有巡检配置引用，请先移除关联配置"
    scope = {"servers": "server", "applications": "service", "alert-rules": "rule"}.get(kind)
    if scope:
        table = m.MaintenanceWindow.__table__
        if db.scalar(
            select(table.c.id)
            .where(table.c.scope_kind == scope, table.c.scope_id == identifier)
            .limit(1)
        ):
            return "仍有维护窗口引用，请先处理关联维护窗口"
    return None


def delete_targets(db, targets):
    for table in reversed(m.Base.metadata.sorted_tables):
        if table in targets:
            db.execute(table.delete().where(targets[table]))
