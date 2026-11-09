from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from uuid import uuid4

import jwt
import pytest
from alembic import command
from conftest import PASSWORD
from sqlalchemy import DateTime, inspect, select, text

from app.core.config import Settings, get_settings
from app.core.security import hash_jti
from app.db.session import SessionLocal, engine, get_db
from app.main import app
from app.models import AuthSession, Role, User


def headers(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def test_health_and_openapi(client):
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").json() == {"status": "ok", "database": "ok"}
    assert client.get("/openapi.json").status_code == 200


def test_unavailable_database_is_not_ready(client):
    class BrokenDatabase:
        def execute(self, statement):
            from sqlalchemy.exc import OperationalError

            raise OperationalError("SELECT 1", {}, Exception("unavailable"))

    app.dependency_overrides[get_db] = lambda: BrokenDatabase()
    try:
        assert client.get("/health/ready").status_code == 503
        assert client.get("/health/live").status_code == 200
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
def test_role_access_matrix(client, make_user, login, role):
    make_user(role, role)
    auth = headers(login(role))
    me = client.get("/api/v1/me", headers=auth)
    assert me.status_code == 200
    assert me.json()["role"] == role
    assert "password_hash" not in me.json()
    system = client.get("/api/v1/system/status", headers=auth)
    assert system.status_code == (403 if role == "viewer" else 200)
    for path in ("/api/v1/users", "/api/v1/roles"):
        assert client.get(path, headers=auth).status_code == (200 if role == "admin" else 403)
    create = client.post(
        "/api/v1/users",
        headers=auth,
        json={"username": "created-user", "password": "Another-password-2026", "role": "viewer"},
    )
    assert create.status_code == (201 if role == "admin" else 403)
    if role != "admin":
        assert (
            client.patch(
                f"/api/v1/users/{uuid4()}", headers=auth, json={"role": "admin"}
            ).status_code
            == 403
        )
        assert client.delete(f"/api/v1/users/{uuid4()}", headers=auth).status_code == 403


@pytest.mark.parametrize("path", ["/api/v1/me", "/api/v1/users", "/api/v1/system/status"])
def test_unauthenticated_access(client, path):
    response = client.get(path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "username,password", [("missing", "Bad-password"), ("admin", "Bad-password")]
)
def test_bad_login(client, make_user, username, password):
    make_user()
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid credentials or token"


def test_disabled_user_cannot_login(client, make_user):
    make_user(active=False)
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "Acceptance-test-2026"}
        ).status_code
        == 401
    )


def test_password_hash_and_login_timestamps(client, make_user, login):
    user_id = make_user()
    with SessionLocal() as db:
        before = db.get(User, user_id).updated_at
    tokens = login()
    with SessionLocal() as db:
        user = db.get(User, user_id)
        assert user.password_hash.startswith("$argon2id$")
        assert user.password_hash != "Acceptance-test-2026"
        assert user.updated_at > before
        assert user.last_login_at is not None
        claims = jwt.decode(tokens["refresh_token"], options={"verify_signature": False})
        session = db.scalar(select(AuthSession).where(AuthSession.user_id == user_id))
        assert session.refresh_jti_hash == hash_jti(claims["jti"])
        assert session.refresh_jti_hash != claims["jti"]


def test_refresh_rotation_and_logout(client, make_user, login):
    make_user()
    original = login()
    refreshed = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": original["refresh_token"]}
    )
    assert refreshed.status_code == 200
    assert refreshed.headers["cache-control"] == "no-store"
    current = refreshed.json()
    assert current["refresh_token"] != original["refresh_token"]
    assert (
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": original["refresh_token"]}
        ).status_code
        == 401
    )
    assert client.get("/api/v1/me", headers=headers(current)).status_code == 200
    assert client.post("/api/v1/auth/logout", headers=headers(current)).status_code == 204
    for token in (original, current):
        assert client.get("/api/v1/me", headers=headers(token)).status_code == 401
        assert (
            client.post(
                "/api/v1/auth/refresh", json={"refresh_token": token["refresh_token"]}
            ).status_code
            == 401
        )


def test_access_and_refresh_cannot_be_interchanged(client, make_user, login):
    make_user()
    tokens = login()
    assert (
        client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {tokens['refresh_token']}"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["access_token"]}
        ).status_code
        == 401
    )


@pytest.mark.parametrize(
    "change", ["expiry", "signature", "audience", "issuer", "subject", "session"]
)
def test_invalid_tokens(client, make_user, login, change):
    make_user()
    token = login()["access_token"]
    claims = jwt.decode(token, options={"verify_signature": False})
    secret = get_settings().jwt_secret.get_secret_value()
    if change == "expiry":
        claims["exp"] = datetime.now(UTC) - timedelta(seconds=1)
    elif change == "signature":
        secret = "A-different-signing-key-that-is-long-enough"
    elif change == "audience":
        claims["aud"] = "another-service"
    elif change == "issuer":
        claims["iss"] = "another-issuer"
    elif change == "subject":
        claims["sub"] = "invalid-uuid"
    else:
        claims["sid"] = str(uuid4())
    bad_token = jwt.encode(claims, secret, algorithm="HS256")
    assert (
        client.get("/api/v1/me", headers={"Authorization": f"Bearer {bad_token}"}).status_code
        == 401
    )


def test_user_management_soft_delete(client, admin_headers, login):
    body = {"username": "test-viewer", "password": "Another-password-2026", "role": "viewer"}
    response = client.post("/api/v1/users", headers=admin_headers, json=body)
    assert response.status_code == 201
    user_id = response.json()["id"]
    assert "password" not in response.text
    assert client.post("/api/v1/users", headers=admin_headers, json=body).status_code == 409
    token = login("test-viewer", body["password"])
    assert (
        client.patch(
            f"/api/v1/users/{user_id}", headers=admin_headers, json={"role": "operator"}
        ).json()["role"]
        == "operator"
    )
    assert client.get("/api/v1/system/status", headers=headers(token)).status_code == 200
    assert (
        client.patch(
            f"/api/v1/users/{user_id}", headers=admin_headers, json={"role": "viewer"}
        ).status_code
        == 200
    )
    assert client.get("/api/v1/system/status", headers=headers(token)).status_code == 403
    assert client.delete(f"/api/v1/users/{user_id}", headers=admin_headers).status_code == 204
    assert client.get("/api/v1/me", headers=headers(token)).status_code == 401
    assert (
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": token["refresh_token"]}
        ).status_code
        == 401
    )
    assert all(
        u["id"] != user_id for u in client.get("/api/v1/users", headers=admin_headers).json()
    )
    assert client.delete(f"/api/v1/users/{user_id}", headers=admin_headers).status_code == 404
    assert client.post("/api/v1/users", headers=admin_headers, json=body).status_code == 409
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT deleted_at, updated_at FROM users WHERE id = :id"), {"id": user_id}
        ).one()
        assert row.deleted_at is not None
        assert row.updated_at >= row.deleted_at


@pytest.mark.parametrize("change", [{"is_active": False}, {"password": "New-password-2026"}])
def test_account_changes_revoke_existing_sessions(client, admin_headers, make_user, login, change):
    user_id = make_user("operator", "operator")
    tokens = login("operator")
    assert (
        client.patch(f"/api/v1/users/{user_id}", headers=admin_headers, json=change).status_code
        == 200
    )
    assert client.get("/api/v1/me", headers=headers(tokens)).status_code == 401
    assert (
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        ).status_code
        == 401
    )
    if "is_active" in change:
        client.patch(f"/api/v1/users/{user_id}", headers=admin_headers, json={"is_active": True})
        assert client.get("/api/v1/me", headers=headers(tokens)).status_code == 401
    else:
        assert login("operator", change["password"])["access_token"]


def test_last_admin_is_preserved(client, admin_headers):
    user_id = client.get("/api/v1/me", headers=admin_headers).json()["id"]
    for change in ({"role": "viewer"}, {"is_active": False}):
        assert (
            client.patch(f"/api/v1/users/{user_id}", headers=admin_headers, json=change).status_code
            == 409
        )
    assert client.delete(f"/api/v1/users/{user_id}", headers=admin_headers).status_code == 409


@pytest.mark.parametrize("body", [{}, {"role": None}, {"is_active": None}, {"role": "superadmin"}])
def test_invalid_user_updates(client, admin_headers, body):
    assert (
        client.patch(f"/api/v1/users/{uuid4()}", headers=admin_headers, json=body).status_code
        == 422
    )


def test_validation_responses_never_echo_secrets(client, admin_headers):
    secret = "Do-not-expose-password-2026"
    response = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={"username": "bad username", "password": secret, "role": "viewer"},
    )
    assert response.status_code == 422
    assert secret not in response.text
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": {"secret": secret}})
    assert response.status_code == 422
    assert secret not in response.text
    assert "input" not in response.text


def test_all_tables_have_utc_timestamps_and_update_triggers():
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) == {
        "access_grants",
        "access_requests",
        "access_policies",
        "role_permissions",
        "inspection_plans",
        "inspection_runs",
        "inspection_items",
        "inspection_reviews",
        "inspection_traces",
        "inspection_data_sources",
        "inspection_http_connections",
        "inspection_check_results",
        "model_usage",
        "usage_cursors",
        "model_prices",
        "usage_budgets",
        "archive_records",
        "retention_plans",
        "backup_records",
        "improvement_tasks",
        "diagnosis_reviews",
        "evaluation_cases",
        "evaluation_runs",
        "prediction_outcomes",
        "service_objectives",
        "worker_heartbeats",
        "incident_relations",
        "service_operations_policies",
        "notifications",
        "notification_policy",
        "incident_responses",
        "maintenance_windows",
        "alert_inhibitions",
        "operations_audit_logs",
        "market_categories",
        "market_entries",
        "market_entry_revisions",
        "market_audit_logs",
        "repair_handoffs",
        "applications",
        "source_snapshots",
        "application_releases",
        "application_deployments",
        "source_audit_logs",
        "alembic_version",
        "roles",
        "users",
        "auth_sessions",
        "ssh_credentials",
        "servers",
        "containers",
        "alert_rules",
        "alert_events",
        "llm_endpoints",
        "llm_service_bindings",
        "prediction_tasks",
        "prediction_results",
        "incidents",
        "incident_alerts",
        "incident_timeline",
        "knowledge_documents",
        "knowledge_chunks",
        "knowledge_skills",
        "agent_analyses",
        "agent_channels",
        "agent_configs",
        "diagnosis_policy",
        "service_monitors",
        "service_databases",
        "analysis_conversations",
        "analysis_messages",
        "analysis_runs",
        "analysis_events",
        "approvals",
        "skill_executions",
        "audit_logs",
        "postmortem_reports",
        "postmortem_revisions",
        "postmortem_deliveries",
    }
    for table in inspector.get_table_names():
        columns = {c["name"]: c for c in inspector.get_columns(table)}
        for name in ("created_at", "updated_at", "deleted_at"):
            assert isinstance(columns[name]["type"], DateTime)
            assert columns[name]["type"].timezone is True
        for name in ("created_at", "updated_at"):
            assert columns[name]["nullable"] is False
            assert columns[name]["default"] is not None
        assert columns["deleted_at"]["nullable"] is True
        with engine.connect() as connection:
            assert connection.scalar(text("SHOW timezone")) == "UTC"
            trigger_count = connection.scalar(
                text("""
                    SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
                    WHERE c.relname = :table AND t.tgname = :trigger AND NOT t.tgisinternal
                """),
                {"table": table, "trigger": f"trg_{table}_updated_at"},
            )
            assert trigger_count == 1, table


def test_direct_sql_also_updates_timestamp():
    with engine.begin() as connection:
        before = connection.scalar(text("SELECT updated_at FROM roles WHERE code='viewer'"))
        after = connection.scalar(
            text("UPDATE roles SET name=name WHERE code='viewer' RETURNING updated_at")
        )
        assert after > before


def test_migrations_rollback_reapply_and_no_model_drift(migrated_database):
    config = migrated_database
    with engine.connect() as connection:
        before = connection.scalar(text("SELECT updated_at FROM alembic_version"))
    command.downgrade(config, "0001_foundation")
    assert inspect(engine).get_table_names() == ["alembic_version"]
    with engine.connect() as connection:
        after = connection.scalar(text("SELECT updated_at FROM alembic_version"))
        assert after > before
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    command.check(config)
    with SessionLocal() as db:
        assert set(db.scalars(select(Role.code))) == {"viewer", "operator", "admin"}


def test_settings_reject_empty_secrets():
    with pytest.raises(ValueError):
        Settings(postgres_password="", jwt_secret="", _env_file=None)


def test_concurrent_refresh_consumes_token_once(client, make_user, login):
    make_user()
    tokens = login()
    barrier = Barrier(2)

    def refresh_once():
        barrier.wait(timeout=5)
        return client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: refresh_once(), range(2)))
    assert sorted(results) == [200, 401]


def test_concurrent_demotions_preserve_one_admin(client, make_user, login):
    accounts = [(make_user(name), headers(login(name))) for name in ("admin-one", "admin-two")]
    barrier = Barrier(2)

    def demote(account):
        user_id, auth = account
        barrier.wait(timeout=5)
        return client.patch(
            f"/api/v1/users/{user_id}", headers=auth, json={"role": "viewer"}
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(demote, accounts))
    assert sorted(results) == [200, 409]


@pytest.mark.parametrize("change", ["password", "disable", "delete"])
def test_login_cannot_escape_concurrent_account_revocation(
    client, admin_headers, make_user, monkeypatch, change
):
    from app.core.security import password_hasher

    user_id = make_user("racing-user", "operator")
    entered, release = Event(), Event()
    original = password_hasher.verify

    def verify(password, password_hash):
        entered.set()
        assert release.wait(timeout=4)
        return original(password, password_hash)

    monkeypatch.setattr(password_hasher, "verify", verify)

    def modify():
        if change == "delete":
            return client.delete(f"/api/v1/users/{user_id}", headers=admin_headers)
        body = {"password": "New-password-2026"} if change == "password" else {"is_active": False}
        return client.patch(f"/api/v1/users/{user_id}", headers=admin_headers, json=body)

    with ThreadPoolExecutor(max_workers=2) as executor:
        signing_in = executor.submit(
            client.post,
            "/api/v1/auth/login",
            json={"username": "racing-user", "password": PASSWORD},
        )
        assert entered.wait(timeout=3)
        changing = executor.submit(modify)
        try:
            # Observe the real PostgreSQL lock wait, not an assumed thread schedule.
            from time import monotonic, sleep

            deadline = monotonic() + 2
            waiting = False
            while monotonic() < deadline:
                with engine.connect() as connection:
                    waiting = connection.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                            "WHERE datname=current_database() AND wait_event_type='Lock' "
                            "AND query LIKE '%users%')"
                        )
                    )
                if waiting:
                    break
                sleep(0.02)
            assert waiting and not changing.done()
        finally:
            release.set()
        response = signing_in.result(timeout=5)
        changed = changing.result(timeout=5)
    assert response.status_code == 200
    assert changed.status_code == (204 if change == "delete" else 200)
    tokens = response.json()
    assert client.get("/api/v1/me", headers=headers(tokens)).status_code == 401
    assert (
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": "racing-user", "password": PASSWORD}
        ).status_code
        == 401
    )
