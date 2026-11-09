"""Database access; transaction ownership stays with the caller."""

from sqlalchemy import select, text

from app.models import AlertRule, Application, LLMEndpoint, PredictionTask, Server, ServiceMonitor


def service_rule_query(db, rule):
    monitor = db.get(ServiceMonitor, rule.service_monitor_id)
    application = db.get(Application, monitor.application_id) if monitor else None
    return monitor, application


def discovery_targets_query(db):
    return db.scalars(
        select(Server).where(Server.deleted_at.is_(None), Server.monitoring_enabled.is_(True))
    )


def apply_rules_query(db):
    return db.execute(text("SELECT pg_advisory_xact_lock(34004)"))


def apply_rules_query_2(db):
    return db.scalars(
        select(AlertRule)
        .where(AlertRule.deleted_at.is_(None), AlertRule.enabled.is_(True))
        .order_by(AlertRule.code)
    )


def apply_rules_query_3(db, rule):
    task = db.get(PredictionTask, rule.prediction_task_id)
    return task


def apply_rules_query_4(db, rule):
    server = db.get(Server, rule.server_id)
    return server


def apply_rules_query_5(db, rule):
    endpoint = db.get(LLMEndpoint, rule.llm_endpoint_id)
    return endpoint
