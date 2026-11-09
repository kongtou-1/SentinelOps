"""One authorization evaluator shared by HTTP, queries and background work."""

from fastapi import HTTPException
from sqlalchemy import String, Uuid, and_, case, cast, event, false, func, not_, or_, select, true
from sqlalchemy.dialects.postgresql import JSONB, JSONPATH
from sqlalchemy.orm import Session, object_session, with_loader_criteria
from sqlalchemy.sql import Select, visitors

from app import models as m
from app.core.permissions import ADMIN_ONLY, CATALOGUE, RESOURCE_READS
from app.repositories import access as repository

RESOURCE_MODELS = {
    "server": m.Server,
    "container": m.Container,
    "application": m.Application,
    "llm_endpoint": m.LLMEndpoint,
    "service_monitor": m.ServiceMonitor,
    "service_database": m.ServiceDatabase,
}


class Authorization:
    def __init__(self, db, user):
        self.db, self.user = db, user
        self.admin = user.role.code == "admin"
        self.defaults = (
            set(CATALOGUE) if self.admin else repository.role_permissions(db, user.role_id)
        )
        self.grants = [] if self.admin else repository.grants(db, user.id)
        self.policies = [] if self.admin else repository.policies(db)
        self.denials = [] if self.admin else repository.policies(db, user.id)
        self.required = None
        self.cache = {}

    @property
    def capabilities(self):
        return self.defaults | {g.permission for g in self.grants if g.permission not in ADMIN_ONLY}

    def has(self, permission):
        if permission == "resources:read":
            return any(p in self.capabilities for p in RESOURCE_READS.values())
        return permission in self.capabilities and (self.admin or permission not in ADMIN_ONLY)

    def parents(self, kind, identifier):
        """Only ownership edges; never service dependencies or shared LLM bindings."""
        if kind == "container":
            t = m.Container.__table__
            row = repository.raw(
                self.db, select(t.c.server_id, t.c.application_id).where(t.c.id == identifier)
            ).first()
            return [(k, str(v)) for k, v in zip(("server", "application"), row or ()) if v]
        if kind in ("service_monitor", "service_database"):
            t = RESOURCE_MODELS[kind].__table__
            value = repository.raw(
                self.db, select(t.c.application_id).where(t.c.id == identifier)
            ).scalar()
            return [("application", str(value))] if value else []
        return []

    def allows(self, permission, kind=None, identifier=None):
        if permission == "resources:read" and kind in RESOURCE_READS:
            permission = RESOURCE_READS[kind]
        if not self.user.is_active or self.user.deleted_at:
            return False
        if self.admin:
            return permission in CATALOGUE
        if permission in ADMIN_ONLY or permission not in CATALOGUE:
            return False
        if kind is None:
            return self.has(permission)
        identifier = str(identifier)
        parents = self.parents(kind, identifier)
        scopes = {(kind, identifier), *parents}
        if any((p.resource_kind, p.resource_id) in scopes for p in self.denials):
            return False
        restricted = any((p.resource_kind, p.resource_id) in scopes for p in self.policies)
        for g in self.grants:
            if g.permission != permission:
                continue
            if (g.resource_kind, g.resource_id) == (kind, identifier):
                return True
            if g.include_descendants and (g.resource_kind, g.resource_id) in parents:
                return True
            if not restricted and g.resource_kind == "global":
                return True
        return permission in self.defaults and not restricted

    def resource_clause(self, kind, identifier, permission):
        if self.admin:
            return true()
        if permission in ADMIN_ONLY:
            return false()
        text_id = cast(identifier, String)
        parents = []
        if kind == "container":
            t = m.Container.__table__.alias("access_container_parent")
            parents = [
                (k, select(cast(t.c[col], String)).where(t.c.id == identifier).scalar_subquery())
                for k, col in (("server", "server_id"), ("application", "application_id"))
            ]
        elif kind in ("service_monitor", "service_database"):
            t = RESOURCE_MODELS[kind].__table__.alias("access_application_parent")
            parents = [
                (
                    "application",
                    select(cast(t.c.application_id, String))
                    .where(t.c.id == identifier)
                    .scalar_subquery(),
                )
            ]
        restrictions = []
        denials = []
        for parent_kind, column in [(kind, text_id), *parents]:
            ids = [p.resource_id for p in self.policies if p.resource_kind == parent_kind]
            if ids:
                restrictions.append(func.coalesce(column.in_(ids), false()))
            denied_ids = [p.resource_id for p in self.denials if p.resource_kind == parent_kind]
            if denied_ids:
                denials.append(func.coalesce(column.in_(denied_ids), false()))
        restricted = or_(*restrictions) if restrictions else false()
        allowed = []
        if permission in self.defaults:
            allowed.append(not_(restricted))
        for g in self.grants:
            if g.permission != permission:
                continue
            if g.resource_kind == "global":
                allowed.append(not_(restricted))
            elif g.resource_kind == kind:
                allowed.append(text_id == g.resource_id)
            elif g.include_descendants:
                allowed.extend(
                    column == g.resource_id
                    for parent_kind, column in parents
                    if parent_kind == g.resource_kind
                )
        result = func.coalesce(or_(*allowed), false()) if allowed else false()
        return and_(not_(or_(*denials)), result) if denials else result

    def clause(self, model, permission=None):
        key = (model, permission)
        if key not in self.cache:
            self.cache[key] = self._clause(model, permission)
        return self.cache[key]

    def json_clause(self, column, permission):
        if not self.policies and not self.denials and permission in self.defaults:
            return true()
        checks = []
        for kind, field in (
            ("server", "server_id"),
            ("container", "container_id"),
            ("application", "application_id"),
            ("llm_endpoint", "llm_endpoint_id"),
            ("service_monitor", "service_monitor_id"),
            ("service_database", "connection_id"),
            ("service_database", "database_id"),
        ):
            values = (
                func.jsonb_path_query(
                    cast(column, JSONB), cast(f"$.** ? (exists(@.{field}))", JSONPATH)
                )
                .table_valued("value")
                .render_derived()
            )
            obj = cast(values.c.value, JSONB)
            value = obj[field].astext
            identifier = case(
                (
                    value.op("~")(
                        "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
                    ),
                    cast(value, Uuid),
                ),
                else_=None,
            )
            allowed = self.resource_clause(kind, identifier, permission)
            if kind == "server":
                # A container's parent UUID is a link, not disclosure of host-wide data.
                ct = m.Container.__table__.alias("access_frozen_container")
                child_id = func.coalesce(obj["container_id"].astext, obj["id"].astext)
                container_link = (
                    select(ct.c.id)
                    .where(
                        cast(ct.c.id, String) == child_id,
                        ct.c.server_id == identifier,
                        self.resource_clause("container", ct.c.id, permission),
                    )
                    .exists()
                )
                allowed = or_(allowed, container_link)
            denied = select(values.c.value).where(identifier.is_not(None), not_(allowed))
            checks.append(not_(denied.exists()))
        for kind, model in RESOURCE_MODELS.items():
            values = (
                func.jsonb_path_query(cast(column, JSONB), cast("$.**.id", JSONPATH))
                .table_valued("value")
                .render_derived()
            )
            value = func.btrim(cast(values.c.value, String), '"')
            resource = model.__table__.alias("access_frozen_" + kind)
            denied = select(values.c.value).where(
                select(resource.c.id)
                .where(
                    cast(resource.c.id, String) == value,
                    not_(self.resource_clause(kind, resource.c.id, permission)),
                )
                .exists()
            )
            checks.append(not_(denied.exists()))
        return and_(*checks)

    def _clause(self, model, permission=None):
        t = model.__table__
        for kind, resource in RESOURCE_MODELS.items():
            if model is resource:
                action = permission or RESOURCE_READS[kind]
                result = self.resource_clause(kind, t.c.id, action)
                if model is m.ServiceMonitor:
                    result = and_(
                        result,
                        or_(
                            t.c.container_id.is_(None),
                            self.resource_clause("container", t.c.container_id, action),
                        ),
                    )
                return result
        p = permission

        if model in (
            m.AskDataConfiguration,
            m.AskDataModelVersion,
            m.AskDataBuild,
            m.AskDataConversation,
            m.AskDataDefinition,
        ):
            allowed = self.resource_clause(
                "service_database", t.c.connection_id, p or "askdata:read"
            )
            if model is m.AskDataConversation:
                allowed = and_(allowed, t.c.owner_id == self.user.id)
            return and_(allowed, t.c.deleted_at.is_(None))
        if model is m.AskDataRun:
            parent = m.AskDataConversation.__table__
            return and_(
                t.c.deleted_at.is_(None),
                t.c.conversation_id.in_(
                    select(parent.c.id).where(
                        self.clause(m.AskDataConversation, p or "askdata:read")
                    )
                ),
            )
        if model is m.AskDataEvent:
            parent = m.AskDataRun.__table__
            return and_(
                t.c.deleted_at.is_(None),
                t.c.run_id.in_(
                    select(parent.c.id).where(self.clause(m.AskDataRun, p or "askdata:read"))
                ),
            )

        def belongs(parent, column, action=None):
            pt = parent.__table__
            return column.in_(select(pt.c.id).where(self.clause(parent, action or p)))

        if model is m.PredictionTask:
            action = p or "predictions:read"
            checks = [
                or_(t.c[col].is_(None), self.resource_clause(kind, t.c[col], action))
                for kind, col in (
                    ("server", "server_id"),
                    ("container", "container_id"),
                    ("llm_endpoint", "llm_endpoint_id"),
                )
            ]
            return and_(*checks)
        if model in (m.AlertEvent, m.AlertRule):
            action = p or "alerts:read"
            checks = [
                or_(t.c[col].is_(None), self.resource_clause(kind, t.c[col], action))
                for kind, col in (("server", "server_id"), ("llm_endpoint", "llm_endpoint_id"))
            ]
            checks.append(
                or_(
                    t.c.prediction_task_id.is_(None),
                    belongs(m.PredictionTask, t.c.prediction_task_id, action),
                )
            )
            if model is m.AlertEvent:
                ct = m.Container.__table__.alias("access_alert_container")
                match = and_(
                    ct.c.server_id == t.c.server_id,
                    or_(
                        cast(ct.c.id, String) == t.c.labels["container_id"].as_string(),
                        ct.c.docker_id == t.c.labels["docker_id"].as_string(),
                        ct.c.docker_id == t.c.labels["id"].as_string(),
                        ("/docker/" + ct.c.docker_id) == t.c.labels["id"].as_string(),
                    ),
                )
                matches = select(ct.c.id).where(match).exists()
                accessible = (
                    select(ct.c.id)
                    .where(match, self.resource_clause("container", ct.c.id, action))
                    .exists()
                )
                server_access = case(
                    (matches, accessible),
                    else_=self.resource_clause("server", t.c.server_id, action),
                )
                checks[0] = or_(
                    t.c.server_id.is_(None),
                    t.c.service_monitor_id.is_not(None),
                    t.c.prediction_task_id.is_not(None),
                    server_access,
                )
                checks.append(
                    or_(
                        t.c.service_monitor_id.is_(None),
                        belongs(m.ServiceMonitor, t.c.service_monitor_id, action),
                    )
                )
                checks.append(self.json_clause(t.c.rule_snapshot, action))
            checks.append(
                or_(
                    t.c.server_id.is_not(None),
                    t.c.llm_endpoint_id.is_not(None),
                    t.c.prediction_task_id.is_not(None),
                    t.c.service_monitor_id.is_not(None) if model is m.AlertEvent else false(),
                    true() if action in self.defaults else false(),
                )
            )
            return and_(*checks)
        if model is m.Incident:
            link, alert = m.IncidentAlert.__table__, m.AlertEvent.__table__
            associated = (
                select(link.c.alert_event_id).where(link.c.incident_id == t.c.id).correlate(t)
            )
            denied = select(alert.c.id).where(
                alert.c.id.in_(associated), not_(self.clause(m.AlertEvent, p or "incidents:read"))
            )
            # Raw table subqueries keep denied rows visible to the authorization test.
            return and_(
                not_(denied.exists()),
                or_(
                    associated.exists(),
                    true() if (p or "incidents:read") in self.defaults else false(),
                ),
            )
        incident_models = {
            m.AgentAnalysis: "analyses:read",
            m.AnalysisConversation: "analyses:read",
            m.AnalysisRun: "analyses:read",
            m.Approval: "executions:read",
            m.SkillExecution: "executions:read",
            m.PostmortemReport: "reports:read",
            m.RepairHandoff: "repairs:read",
            m.IncidentTimeline: "incidents:read",
            m.IncidentResponseRecord: "incidents:read",
            m.ImprovementTask: "reports:read",
        }
        if model in incident_models:
            action = p or incident_models[model]
            result = belongs(m.Incident, t.c.incident_id, action)
            if model in (m.Approval, m.SkillExecution):
                result = and_(result, self.resource_clause("container", t.c.container_id, action))
            if model in (m.AgentAnalysis, m.AnalysisRun):
                result = and_(
                    result,
                    self.json_clause(t.c.source_context, action),
                    self.json_clause(t.c.database_context, action),
                    self.json_clause(t.c.evidence, action),
                )
            if model is m.AnalysisConversation:
                runs = m.AnalysisRun.__table__
                result = and_(
                    result,
                    not_(
                        select(runs.c.id)
                        .where(
                            runs.c.conversation_id == t.c.id,
                            not_(self.clause(m.AnalysisRun, action)),
                        )
                        .exists()
                    ),
                )
            if model is m.PostmortemReport:
                result = and_(result, self.json_clause(t.c.snapshot, action))
            if model is m.RepairHandoff:
                result = and_(
                    result,
                    or_(
                        t.c.application_id.is_(None),
                        self.resource_clause("application", t.c.application_id, action),
                    ),
                    self.json_clause(t.c.packet, action),
                )
            return result
        if model is m.IncidentAlert:
            return belongs(m.Incident, t.c.incident_id, p or "incidents:read")
        if model in (m.AnalysisMessage, m.AnalysisEvent):
            return belongs(m.AnalysisConversation, t.c.conversation_id, p or "analyses:read")
        if model is m.PredictionResult:
            return belongs(m.PredictionTask, t.c.task_id, p or "predictions:read")
        if model is m.PredictionOutcome:
            return belongs(m.PredictionResult, t.c.result_id, p or "predictions:read")
        if model in (
            m.SourceSnapshot,
            m.ApplicationRelease,
            m.ApplicationDeployment,
            m.LLMServiceBinding,
            m.ServiceObjective,
            m.ServiceOperationsPolicy,
            m.InspectionPlan,
            m.InspectionDataSource,
            m.InspectionHTTPConnection,
            m.SourceAuditLog,
            m.ModelUsage,
            m.UsageBudget,
        ):
            action = p or (
                "inspections:read"
                if model in (m.InspectionPlan, m.InspectionDataSource, m.InspectionHTTPConnection)
                else "sources:read"
            )
            if model in (m.ModelUsage, m.UsageBudget):
                action = p or "system:read"
            result = or_(
                t.c.application_id.is_(None) & (true() if action in self.defaults else false()),
                self.resource_clause("application", t.c.application_id, action),
            )
            if model in (m.LLMServiceBinding, m.ApplicationDeployment):
                result = and_(
                    result,
                    or_(
                        t.c.llm_endpoint_id.is_(None),
                        self.resource_clause("llm_endpoint", t.c.llm_endpoint_id, p or "llm:read"),
                    ),
                )
            if model is m.ApplicationDeployment:
                result = and_(
                    result,
                    or_(
                        t.c.container_id.is_(None),
                        self.resource_clause("container", t.c.container_id, action),
                    ),
                )
            if model in (m.InspectionPlan, m.InspectionDataSource):
                result = and_(
                    result,
                    self.json_clause(t.c.source_config, action),
                    self.json_clause(t.c.related_sources, action)
                    if model is m.InspectionPlan
                    else true(),
                )
            if model is m.InspectionPlan:
                references = (
                    func.jsonb_path_query(
                        cast(t.c.check_items, JSONB), cast("$[*].source_ids[*]", JSONPATH)
                    )
                    .table_valued("value")
                    .render_derived()
                )
                source_id = func.btrim(cast(references.c.value, String), '"')
                readable = select(cast(m.InspectionDataSource.id, String)).where(
                    self.clause(m.InspectionDataSource, action),
                    m.InspectionDataSource.deleted_at.is_(None),
                )
                result = and_(
                    result,
                    not_(select(references.c.value).where(source_id.not_in(readable)).exists()),
                )
            return result
        if model in (m.InspectionRun, m.InspectionItem, m.InspectionCheckResult, m.InspectionTrace):
            return belongs(m.InspectionPlan, t.c.plan_id, p or "inspections:read")
        if model is m.InspectionReview:
            return belongs(m.InspectionItem, t.c.item_id, p or "inspections:read")
        if model in (m.PostmortemRevision, m.PostmortemDelivery):
            return belongs(m.PostmortemReport, t.c.report_id, p or "reports:read")
        if model is m.IncidentRelation:
            return and_(
                belongs(m.Incident, t.c.incident_id), belongs(m.Incident, t.c.related_incident_id)
            )
        if model is m.DiagnosisReview:
            return belongs(m.AgentAnalysis, t.c.analysis_id, p or "analyses:read")
        if model is m.EvaluationCase:
            return and_(
                belongs(m.AgentAnalysis, t.c.analysis_id, p or "analyses:read"),
                self.json_clause(t.c.snapshot, p or "analyses:read"),
            )
        if model is m.EvaluationRun:
            return belongs(m.EvaluationCase, t.c.case_id, p or "analyses:read")
        if model is m.Notification:
            return and_(
                or_(
                    t.c.incident_id.is_(None),
                    belongs(m.Incident, t.c.incident_id, "incidents:read"),
                ),
                or_(
                    t.c.resource_kind != "improvement_task",
                    belongs(m.ImprovementTask, t.c.resource_id, "reports:read"),
                ),
            )
        if model is m.ArchiveRecord:
            return or_(
                *[
                    and_(t.c.resource_kind == kind, belongs(parent, t.c.resource_id, action))
                    for kind, parent, action in (
                        ("source_snapshot", m.SourceSnapshot, "sources:read"),
                        ("prediction", m.PredictionResult, "predictions:read"),
                        ("conversation", m.AnalysisConversation, "analyses:run"),
                    )
                ]
            )
        return true()


SCOPED_MODELS = [
    m.AskDataConfiguration,
    m.AskDataModelVersion,
    m.AskDataBuild,
    m.AskDataConversation,
    m.AskDataDefinition,
    m.AskDataRun,
    m.AskDataEvent,
    *RESOURCE_MODELS.values(),
    m.PredictionTask,
    m.PredictionResult,
    m.AlertEvent,
    m.AlertRule,
    m.Incident,
    m.IncidentAlert,
    m.IncidentTimeline,
    m.AgentAnalysis,
    m.AnalysisConversation,
    m.AnalysisRun,
    m.AnalysisMessage,
    m.AnalysisEvent,
    m.Approval,
    m.SkillExecution,
    m.PostmortemReport,
    m.PostmortemRevision,
    m.PostmortemDelivery,
    m.RepairHandoff,
    m.SourceSnapshot,
    m.ApplicationRelease,
    m.ApplicationDeployment,
    m.LLMServiceBinding,
    m.ServiceObjective,
    m.ServiceOperationsPolicy,
    m.IncidentRelation,
    m.IncidentResponseRecord,
    m.ImprovementTask,
    m.InspectionPlan,
    m.InspectionDataSource,
    m.InspectionHTTPConnection,
    m.InspectionRun,
    m.InspectionItem,
    m.InspectionReview,
    m.InspectionCheckResult,
    m.InspectionTrace,
    m.Notification,
    m.ArchiveRecord,
    m.PredictionOutcome,
    m.DiagnosisReview,
    m.EvaluationCase,
    m.EvaluationRun,
    m.SourceAuditLog,
    m.ModelUsage,
    m.UsageBudget,
]


def bind(db, user, permission=None):
    context = Authorization(db, user)
    context.required = permission
    db.info["authorization"] = context
    return context


def context(db, user):
    current = db.info.get("authorization")
    return current if current and current.user.id == user.id else bind(db, user)


def has_permission(user, permission, db=None):
    db = db or object_session(user)
    if db is None:
        return False
    return context(db, user).has(permission)


def require(db, user, permission, kind=None, identifier=None):
    current = context(db, user)
    if not current.allows(permission, kind, identifier):
        raise HTTPException(
            403,
            {
                "code": "permission_required",
                "permission": permission,
                "resource_kind": kind,
                "resource_id": str(identifier) if identifier else None,
                "requestable": permission in CATALOGUE and CATALOGUE[permission].requestable,
            },
        )
    return current


def record_allowed(db, user, model, identifier, permission=None):
    current = Authorization(db, user)
    if current.admin:
        return True
    return (
        repository.raw(
            db,
            select(model.__table__.c.id).where(
                model.__table__.c.id == identifier, current.clause(model, permission)
            ),
        ).first()
        is not None
    )


def container_server(db, container):
    """Connection metadata for an already authorized container, never a host grant."""
    row = repository.raw(
        db,
        select(m.Server).where(m.Server.id == container.server_id, m.Server.deleted_at.is_(None)),
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Resource not found")
    return row


def resource_guard(user_id, permission, kind, identifier):
    def check():
        from app.db.session import SessionLocal

        with SessionLocal() as db:
            user = repository.active_user(db, user_id)
            if user is None or not Authorization(db, user).allows(
                permission, kind, str(identifier)
            ):
                raise HTTPException(403, "权限已失效")

    return check


@event.listens_for(Session, "do_orm_execute")
def filter_resources(state):
    current = state.session.info.get("authorization")
    if (
        not current
        or current.admin
        or state.execution_options.get("access_internal")
        or not state.is_select
    ):
        return
    operation = current.required
    if operation and (operation.endswith(":read") or operation in ADMIN_ONLY):
        operation = None
    # A join used to locate a container must not require access to its whole host.
    # Filter returned entities (and count subqueries), rather than every joined table.
    returned = set()
    for node in visitors.iterate(state.statement):
        if isinstance(node, Select):
            returned.update(
                d["entity"] for d in node.column_descriptions if d.get("entity") is not None
            )
            for source in node._from_obj:
                mapper = source._annotations.get("parentmapper")
                if mapper:
                    returned.add(mapper.class_)
    state.statement = state.statement.options(
        *[
            with_loader_criteria(
                model,
                current.clause(model, operation),
                include_aliases=True,
                propagate_to_loaders=False,
            )
            for model in SCOPED_MODELS
            if model in returned
        ]
    )
