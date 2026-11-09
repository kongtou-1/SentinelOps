from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.core.config import get_settings
from app.core.security import password_hasher
from app.db.session import SessionLocal, engine
from app.main import app
from app.models import Base, Role, User

PASSWORD = "Acceptance-test-2026"
PASSWORD_HASH = password_hasher.hash(PASSWORD)


@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    if not get_settings().postgres_db.endswith("_test"):
        pytest.fail("Use a dedicated POSTGRES_DB ending in _test; tests clear its user data")
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    yield config
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_users(migrated_database):
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE "
                + ", ".join(
                    f'askdata."{t.name}"'
                    for t in Base.metadata.sorted_tables
                    if t.schema == "askdata"
                )
                + ", access_grants, access_requests, access_policies, "
                "inspection_traces, inspection_check_results, "
                "inspection_reviews, inspection_items, inspection_runs, inspection_plans, "
                "inspection_data_sources, inspection_http_connections, "
                "model_usage, usage_cursors, model_prices, usage_budgets, agent_channels, "
                "archive_records, retention_plans, backup_records, improvement_tasks, "
                "diagnosis_reviews, evaluation_runs, evaluation_cases, prediction_outcomes, "
                "notifications, incident_responses, maintenance_windows, "
                "alert_inhibitions, notification_policy, operations_audit_logs, "
                "incident_relations, service_operations_policies, "
                "service_objectives, worker_heartbeats, "
                "market_audit_logs, market_entry_revisions, market_entries, "
                "repair_handoffs, source_audit_logs, llm_service_bindings, "
                "application_deployments, service_databases, "
                "application_releases, source_snapshots, applications, audit_logs, "
                "postmortem_deliveries, postmortem_revisions, "
                "postmortem_reports, skill_executions, approvals, "
                "analysis_events, analysis_messages, analysis_runs, analysis_conversations, "
                "agent_analyses, diagnosis_policy, knowledge_chunks, "
                "knowledge_documents, knowledge_skills, "
                "incident_timeline, incident_alerts, incidents, "
                "alert_events, alert_rules, service_monitors, "
                "prediction_results, prediction_tasks, "
                "containers, servers, ssh_credentials, "
                "llm_endpoints, auth_sessions, users"
            )
        )
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE agent_configs SET version=1, enabled=true, deleted_at=NULL")
        )
    from app.core.permissions import ROLE_PERMISSIONS
    from app.models import RolePermission

    with SessionLocal() as db:
        db.query(RolePermission).delete()
        for role in db.scalars(select(Role)):
            role.permissions_version = 1
            db.add_all(
                RolePermission(role_id=role.id, permission=p) for p in ROLE_PERMISSIONS[role.code]
            )
        db.commit()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(get_settings(), "service_probe_scheduler_enabled", False)
    monkeypatch.setattr(get_settings(), "llm_probe_scheduler_enabled", False)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def make_user():
    def create(username="admin", role="admin", active=True):
        with SessionLocal() as db:
            role_record = db.scalar(select(Role).where(Role.code == role))
            user = User(
                username=username,
                role_id=role_record.id,
                password_hash=PASSWORD_HASH,
                is_active=active,
            )
            db.add(user)
            db.commit()
            return user.id

    return create


@pytest.fixture
def login(client):
    def sign_in(username="admin", password=PASSWORD):
        response = client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        assert response.status_code == 200, response.text
        return response.json()

    return sign_in


@pytest.fixture
def admin_headers(make_user, login):
    make_user()
    return {"Authorization": f"Bearer {login()['access_token']}"}
