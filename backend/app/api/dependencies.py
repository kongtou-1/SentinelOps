from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import AuthSession, User
from app.services import authentication

bearer = HTTPBearer(auto_error=False)
Database = Annotated[Session, Depends(get_db)]


def get_current_session(
    db: Database,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> AuthSession:
    return authentication.get_current_session(db, credentials)


CurrentSession = Annotated[AuthSession, Depends(get_current_session)]


def get_current_user(db: Database, session: CurrentSession) -> User:
    return authentication.get_current_user(db, session)


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_permission(permission: str):
    def check(user: CurrentUser, db: Database, request: Request) -> User:
        from app.services.access_control import require

        actual = permission
        path = request.url.path
        if permission == "resources:read":
            if "/service-databases" in path or path.endswith("/databases"):
                actual = "service_databases:read"
            elif "/service-monitors" in path or "/observability" in path:
                actual = "service_monitors:read"
            elif "/servers" in path or path.endswith("servers-summary"):
                actual = "servers:read"
            elif "/containers" in path or "/container-" in path:
                actual = "containers:read"
            elif "/business-services" in path:
                actual = "applications:read"
            elif path.endswith("/dashboard/summary"):
                actual = "dashboards:read"
        if "/metrics" in path and permission in ("resources:read", "llm:read"):
            actual = "metrics:read"
        if path.endswith("/diagnostics") and request.query_params.get("kind") == "logs":
            actual = "logs:read"
        current = require(db, user, actual)
        if path.endswith("/dashboard/summary"):
            require(db, user, "dashboards:read", "dashboard", "overview")
        boards = {
            "/api/v1/incidents": "incidents",
            "/api/v1/predictions": "predictions",
            "/api/v1/prediction-tasks": "predictions",
            "/api/v1/inspections/summary": "inspections",
            "/api/v1/inspection-plans": "inspections",
            "/api/v1/inspection-items": "inspections",
            "/api/v1/evaluation-cases": "evaluations",
            "/api/v1/evaluation-runs": "evaluations",
            "/api/v1/system/storage": "data-governance",
            "/api/v1/system/usage": "data-governance",
        }
        board = boards.get(path)
        if (
            request.method == "GET"
            and board
            and any(
                p.resource_kind == "dashboard" and p.resource_id == board
                for p in [*current.policies, *current.denials]
            )
        ):
            require(db, user, "dashboards:read", "dashboard", board)
        for key, kind in (
            ("server_id", "server"),
            ("container_id", "container"),
            ("application_id", "application"),
            ("endpoint_id", "llm_endpoint"),
        ):
            if key in request.path_params:
                require(db, user, actual, kind, request.path_params[key])
        current.required = actual
        return user

    return check
