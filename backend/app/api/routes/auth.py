from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.api.dependencies import CurrentSession, CurrentUser, Database, require_permission
from app.models import User
from app.schemas.auth import (
    LoginRequest,
    MeResponse,
    RefreshRequest,
    RoleResponse,
    TokenResponse,
    UserCreate,
    UserResponse,
    UserUpdate,
)
from app.services import auth as _service

router = APIRouter(prefix="/api/v1")

AdminUser = Annotated[User, Depends(require_permission("users:manage"))]


@router.post("/auth/login", response_model=TokenResponse, tags=["auth"])
def login(body: LoginRequest, db: Database, response: Response):
    return _service.login(body, db, response)


@router.post("/auth/refresh", response_model=TokenResponse, tags=["auth"])
def refresh(body: RefreshRequest, db: Database, response: Response):
    return _service.refresh(body, db, response)


@router.post("/auth/logout", status_code=204, tags=["auth"])
def logout(db: Database, session: CurrentSession, user: CurrentUser):
    return _service.logout(db, session, user)


@router.get("/me", response_model=MeResponse, tags=["auth"])
def me(user: CurrentUser):
    return _service.me(user)


@router.get("/roles", response_model=list[RoleResponse], tags=["users"])
def roles(db: Database, user: Annotated[User, Depends(require_permission("roles:read"))]):
    return _service.roles(db, user)


@router.get("/users", response_model=list[UserResponse], tags=["users"])
def users(
    response: Response,
    db: Database,
    admin: AdminUser,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return _service.users(response, db, admin, offset, limit)


@router.post("/users", response_model=UserResponse, status_code=201, tags=["users"])
def create_user(body: UserCreate, db: Database, admin: AdminUser):
    return _service.create_user(body, db, admin)


@router.patch("/users/{user_id}", response_model=UserResponse, tags=["users"])
def update_user(user_id: UUID, body: UserUpdate, db: Database, admin: AdminUser):
    # Consistent role -> user ordering also preserves concurrent last-admin checks.
    return _service.update_user(user_id, body, db, admin)


@router.delete("/users/{user_id}", status_code=204, tags=["users"])
def delete_user(user_id: UUID, db: Database, admin: AdminUser):
    return _service.delete_user(user_id, db, admin)
