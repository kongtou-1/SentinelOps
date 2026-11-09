"""Stage-4 code evidence, rule revision, conflict handling and snapshot choice."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import Database, require_permission
from app.models import User
from app.schemas.askdata_code_rules import (
    AskDataCodeRuleConflictInput,
    AskDataCodeRuleDetail,
    AskDataCodeRuleInput,
    AskDataCodeRulePage,
    AskDataCodeRuleResponse,
    AskDataCodeRuleSummary,
    AskDataSourceSnapshotPage,
)
from app.services import askdata_code_rules as service

router = APIRouter()
ReadUser = Annotated[User, Depends(require_permission("askdata:read"))]
ManageUser = Annotated[User, Depends(require_permission("askdata:manage"))]


@router.get(
    "/connections/{identifier}/source-snapshots",
    response_model=AskDataSourceSnapshotPage,
)
def source_snapshots(identifier: UUID, db: Database, user: ReadUser):
    return service.snapshot_options(db, user, identifier)


@router.get(
    "/versions/{identifier}/code-rules/summary",
    response_model=AskDataCodeRuleSummary,
)
def code_rule_summary(identifier: UUID, db: Database, user: ReadUser):
    return service.summary(db, user, identifier)


@router.get("/versions/{identifier}/code-rules", response_model=AskDataCodeRulePage)
def code_rules(
    identifier: UUID,
    db: Database,
    user: ReadUser,
    language: Literal["python", "java", "other"] | None = Query(default=None),
    kind: str | None = Query(default=None, max_length=30),
    parse_state: Literal["static", "dynamic"] | None = Query(default=None),
    review_required: bool | None = Query(default=None),
    conflict_state: Literal["none", "pending", "resolved"] | None = Query(default=None),
    include_superseded: bool = Query(default=False),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    return service.listing(
        db,
        user,
        identifier,
        language=language,
        kind=kind,
        parse_state=parse_state,
        review_required=review_required,
        conflict_state=conflict_state,
        include_superseded=include_superseded,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/versions/{identifier}/code-rules/{rule_id}",
    response_model=AskDataCodeRuleDetail,
)
def code_rule(identifier: UUID, rule_id: UUID, db: Database, user: ReadUser):
    return service.detail(db, user, identifier, rule_id)


@router.put(
    "/versions/{identifier}/code-rules/{rule_id}",
    response_model=AskDataCodeRuleResponse,
)
def revise(
    identifier: UUID,
    rule_id: UUID,
    body: AskDataCodeRuleInput,
    db: Database,
    user: ManageUser,
):
    return service.revise(db, user, identifier, rule_id, body)


@router.post(
    "/versions/{identifier}/code-rules/{rule_id}/resolve",
    response_model=AskDataCodeRuleResponse,
)
def resolve(identifier: UUID, rule_id: UUID, db: Database, user: ManageUser):
    return service.resolve(db, user, identifier, rule_id)


@router.post(
    "/versions/{identifier}/code-rules/{rule_id}/conflict",
    response_model=AskDataCodeRuleResponse,
)
def resolve_conflict(
    identifier: UUID,
    rule_id: UUID,
    body: AskDataCodeRuleConflictInput,
    db: Database,
    user: ManageUser,
):
    return service.resolve_conflict(db, user, identifier, rule_id, body)
