from sqlalchemy import case, func, select, text
from sqlalchemy.dialects.postgresql import insert

from app.models import (
    AgentAnalysis,
    AlertEvent,
    AlertInhibition,
    AlertRule,
    Application,
    EvaluationCase,
    EvaluationRun,
    ImprovementTask,
    Incident,
    IncidentResponseRecord,
    InspectionDataSource,
    InspectionItem,
    InspectionPlan,
    InspectionRun,
    KnowledgeDocument,
    KnowledgeSkill,
    MaintenanceWindow,
    Notification,
    NotificationPolicy,
    OperationsAuditLog,
    Role,
    Server,
    User,
)
from app.repositories.access import capability_condition


def active_user(db, identifier, handler=False):
    query = (
        select(User)
        .join(Role)
        .where(
            User.id == identifier,
            User.is_active.is_(True),
            User.deleted_at.is_(None),
            Role.deleted_at.is_(None),
        )
    )
    if handler:
        query = query.where(capability_condition("incidents:handle"))
    return db.scalar(query)


def admins(db):
    return db.scalars(
        select(User)
        .join(Role)
        .where(
            Role.code == "admin",
            User.deleted_at.is_(None),
            User.is_active.is_(True),
            Role.deleted_at.is_(None),
        )
        .order_by(User.id)
    ).all()


def notification_policy(db, lock=False):
    if lock:
        db.execute(text("SELECT pg_advisory_xact_lock(100027)"))
    return db.get(NotificationPolicy, 1, with_for_update=lock)


def has_email_contacts(db):
    return (
        db.scalar(
            select(User.id)
            .where(
                User.deleted_at.is_(None), User.is_active.is_(True), User.notification_email != ""
            )
            .limit(1)
        )
        is not None
    )


def response(db, incident_id):
    return db.scalar(
        select(IncidentResponseRecord)
        .where(
            IncidentResponseRecord.incident_id == incident_id,
            IncidentResponseRecord.deleted_at.is_(None),
        )
        .with_for_update()
    )


def due_responses(db, now):
    initial = IncidentResponseRecord.snapshot["initial_notified"].as_boolean()
    return db.scalars(
        select(IncidentResponseRecord.incident_id)
        .join(Incident)
        .where(
            Incident.status.in_(("open", "in_progress")),
            Incident.deleted_at.is_(None),
            IncidentResponseRecord.deleted_at.is_(None),
            (initial.is_(False))
            | (
                IncidentResponseRecord.accepted_at.is_(None)
                & IncidentResponseRecord.escalated_at.is_(None)
                & (IncidentResponseRecord.due_at <= now)
            ),
        )
        .order_by(case((initial.is_(False), 0), else_=1), IncidentResponseRecord.due_at)
        .limit(50)
    ).all()


def add_notification(db, values):
    statement = insert(Notification).values(**values)
    return db.scalar(
        statement.on_conflict_do_nothing(index_elements=[Notification.dedupe_key]).returning(
            Notification.id
        )
    )


def feed(user_id, unread_only=False):
    query = select(Notification).where(
        Notification.user_id == user_id, Notification.deleted_at.is_(None)
    )
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    return query.order_by(Notification.created_at.desc(), Notification.id)


def unread_count(db, user_id):
    return db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.read_at.is_(None),
            Notification.deleted_at.is_(None),
        )
    )


def queued_mail(db):
    return db.scalar(
        select(Notification)
        .where(Notification.mail_status == "queued", Notification.deleted_at.is_(None))
        .order_by(Notification.created_at, Notification.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )


def unfinished_mail(db):
    return db.scalars(
        select(Notification).where(
            Notification.mail_status == "sending", Notification.deleted_at.is_(None)
        )
    ).all()


def active_windows(db, now):
    return db.scalars(
        select(MaintenanceWindow).where(
            MaintenanceWindow.deleted_at.is_(None),
            MaintenanceWindow.starts_at <= now,
            MaintenanceWindow.ends_at > now,
        )
    ).all()


def windows_query():
    return select(MaintenanceWindow).where(MaintenanceWindow.deleted_at.is_(None))


def inhibitions(db):
    return db.scalars(select(AlertInhibition).where(AlertInhibition.deleted_at.is_(None))).all()


def inhibition_query():
    return (
        select(AlertInhibition)
        .where(AlertInhibition.deleted_at.is_(None))
        .order_by(AlertInhibition.created_at.desc())
    )


def existing_inhibition(db, parent_id, child_id):
    return db.scalar(
        select(AlertInhibition)
        .where(
            AlertInhibition.parent_rule_id == parent_id,
            AlertInhibition.child_rule_id == child_id,
        )
        .with_for_update()
    )


def firing_parent_events(db, rule_id):
    return db.scalars(
        select(AlertEvent).where(
            AlertEvent.rule_id == rule_id,
            AlertEvent.status == "firing",
            AlertEvent.deleted_at.is_(None),
        )
    ).all()


def audit(db, user, action, kind, identifier=None, detail=None):
    db.add(
        OperationsAuditLog(
            user_id=user.id if user else None,
            action=action,
            resource_kind=kind,
            resource_id=identifier,
            detail=detail or {},
        )
    )


def audit_query(scope=None):
    query = (
        select(OperationsAuditLog)
        .where(OperationsAuditLog.deleted_at.is_(None))
        .order_by(OperationsAuditLog.created_at.desc(), OperationsAuditLog.id)
    )
    if scope == "notifications":
        query = query.where(
            OperationsAuditLog.resource_kind.in_(
                (
                    "notification_policy",
                    "platform_mail",
                    "maintenance",
                    "inhibition",
                    "notification",
                )
            )
        )
    return query


def audit_context(db, rows):
    # Historical audit entries keep names even when the linked record was soft-deleted.
    users = dict(
        db.execute(
            select(User.id, User.username).where(
                User.id.in_({row.user_id for row in rows if row.user_id})
            )
        ).all()
    )
    names = {}
    for kind, model, column in (
        ("notification", Notification, Notification.title),
        ("improvement_task", ImprovementTask, ImprovementTask.title),
        ("evaluation_case", EvaluationCase, EvaluationCase.title),
        ("knowledge_document", KnowledgeDocument, KnowledgeDocument.title),
        ("knowledge_skill", KnowledgeSkill, KnowledgeSkill.title),
        ("inspection_plan", InspectionPlan, InspectionPlan.name),
        ("inspection_data_source", InspectionDataSource, InspectionDataSource.name),
    ):
        identifiers = {row.resource_id for row in rows if row.resource_kind == kind}
        if identifiers:
            names.update(
                {
                    (kind, identifier): name
                    for identifier, name in db.execute(
                        select(model.id, column).where(model.id.in_(identifiers))
                    )
                }
            )
    for kind, query in (
        (
            "inspection_run",
            select(InspectionRun.id, InspectionPlan.name).join(
                InspectionPlan, InspectionPlan.id == InspectionRun.plan_id
            ),
        ),
        (
            "inspection_item",
            select(InspectionItem.id, InspectionPlan.name).join(
                InspectionPlan, InspectionPlan.id == InspectionItem.plan_id
            ),
        ),
        (
            "maintenance",
            select(
                MaintenanceWindow.id, func.coalesce(Application.name, Server.name, AlertRule.code)
            )
            .outerjoin(
                Application,
                (MaintenanceWindow.scope_kind == "service")
                & (Application.id == MaintenanceWindow.scope_id),
            )
            .outerjoin(
                Server,
                (MaintenanceWindow.scope_kind == "server")
                & (Server.id == MaintenanceWindow.scope_id),
            )
            .outerjoin(
                AlertRule,
                (MaintenanceWindow.scope_kind == "rule")
                & (AlertRule.id == MaintenanceWindow.scope_id),
            ),
        ),
        (
            "inhibition",
            select(AlertInhibition.id, AlertRule.code).join(
                AlertRule, AlertRule.id == AlertInhibition.child_rule_id
            ),
        ),
        (
            "analysis",
            select(AgentAnalysis.id, Incident.title).join(
                Incident, Incident.id == AgentAnalysis.incident_id
            ),
        ),
        (
            "evaluation_run",
            select(EvaluationRun.id, EvaluationCase.title).join(
                EvaluationCase, EvaluationCase.id == EvaluationRun.case_id
            ),
        ),
    ):
        identifiers = {row.resource_id for row in rows if row.resource_kind == kind}
        if identifiers:
            names.update(
                {
                    (kind, identifier): name
                    for identifier, name in db.execute(
                        query.where(query.selected_columns[0].in_(identifiers))
                    )
                }
            )
    return users, names
