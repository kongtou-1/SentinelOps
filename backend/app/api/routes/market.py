"""Human-reviewed discovery content; never registered as diagnostic or execution skills."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.api.dependencies import Database, require_permission
from app.models import User
from app.schemas.market import (
    CategoryInput,
    MarketCreate,
    MarketDraft,
    ReviewAction,
    RevisionAction,
    WithdrawEntry,
)
from app.services import market as _service

router = APIRouter(prefix="/api/v1/market", tags=["market"])

ReadUser = Annotated[User, Depends(require_permission("market:read"))]

SubmitUser = Annotated[User, Depends(require_permission("market:submit"))]

ReviewUser = Annotated[User, Depends(require_permission("market:review"))]

ManageUser = Annotated[User, Depends(require_permission("market:manage"))]

Kind = Literal["skill", "tool"]


@router.get("/categories")
def categories(db: Database, user: ReadUser, kind: Kind | None = None):
    return _service.categories(db, user, kind)


@router.post("/categories", status_code=201)
def create_category(body: CategoryInput, db: Database, user: ManageUser):
    return _service.create_category(body, db, user)


@router.put("/categories/{category_id}")
def update_category(category_id: UUID, body: CategoryInput, db: Database, user: ManageUser):
    return _service.update_category(category_id, body, db, user)


@router.delete("/categories/{category_id}", status_code=204)
def delete_category(category_id: UUID, db: Database, user: ManageUser):
    return _service.delete_category(category_id, db, user)


@router.get("/entries")
def entries(
    response: Response,
    db: Database,
    user: ReadUser,
    scope: Literal["published", "mine", "manage"] = "published",
    kind: Kind | None = None,
    category_id: UUID | None = None,
    status: Literal["draft", "pending", "approved", "rejected"] | None = None,
    q: Annotated[str, Query(max_length=200)] = "",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 24,
):
    return _service.entries(response, db, user, scope, kind, category_id, status, q, offset, limit)


@router.post("/entries", status_code=201)
def create_entry(body: MarketCreate, db: Database, user: SubmitUser):
    return _service.create_entry(body, db, user)


@router.get("/entries/{entry_id}")
def public_entry(entry_id: UUID, db: Database, user: ReadUser):
    return _service.public_entry(entry_id, db, user)


@router.get("/entries/{entry_id}/workbench")
def workbench(entry_id: UUID, db: Database, user: ReadUser):
    return _service.workbench(entry_id, db, user)


@router.put("/entries/{entry_id}/draft")
def save_draft(entry_id: UUID, body: MarketDraft, db: Database, user: SubmitUser):
    return _service.save_draft(entry_id, body, db, user)


@router.post("/revisions/{revision_id}/submit")
def submit_revision(revision_id: UUID, body: RevisionAction, db: Database, user: SubmitUser):
    return _service.submit_revision(revision_id, body, db, user)


@router.post("/revisions/{revision_id}/withdraw")
def withdraw_revision(revision_id: UUID, body: RevisionAction, db: Database, user: SubmitUser):
    return _service.withdraw_revision(revision_id, body, db, user)


@router.post("/revisions/{revision_id}/review")
def review_revision(revision_id: UUID, body: ReviewAction, db: Database, user: ReviewUser):
    return _service.review_revision(revision_id, body, db, user)


@router.post("/entries/{entry_id}/unpublish")
def unpublish_entry(entry_id: UUID, body: WithdrawEntry, db: Database, user: ReviewUser):
    return _service.unpublish_entry(entry_id, body, db, user)


@router.get("/category-audit")
def category_audit(db: Database, user: ManageUser):
    return _service.category_audit(db, user)
