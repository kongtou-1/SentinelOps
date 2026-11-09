from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.api.dependencies import Database, require_permission
from app.models import EvaluationCase, EvaluationRun, User
from app.schemas.evaluation import (
    CaseInput,
    CasePreview,
    CaseResponse,
    OutcomeLink,
    OutcomeResponse,
    ReplayInput,
    ReplayResponse,
    ReplayReview,
    ReviewInput,
    ReviewResponse,
)
from app.services.operations import evaluation, prediction_outcomes
from app.services.records import active_record

router = APIRouter(prefix="/api/v1", tags=["evaluation"])
ReadUser = Annotated[User, Depends(require_permission("analyses:read"))]
WriteUser = Annotated[User, Depends(require_permission("analyses:run"))]
AdminUser = Annotated[User, Depends(require_permission("knowledge:manage"))]
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=100)]


@router.get("/evaluations/summary")
def summary(db: Database, user: ReadUser):
    return evaluation.summary(db)


@router.get("/analyses/{identifier}/reviews", response_model=list[ReviewResponse])
def reviews(
    identifier: UUID,
    response: Response,
    db: Database,
    user: ReadUser,
    offset: Offset = 0,
    limit: Limit = 50,
):
    return evaluation.listing(db, response, "reviews", identifier, offset, limit)


@router.post("/analyses/{identifier}/reviews", response_model=ReviewResponse, status_code=201)
def review(identifier: UUID, body: ReviewInput, db: Database, user: WriteUser):
    return evaluation.review(db, identifier, body, user)


@router.get("/evaluation-cases", response_model=list[CaseResponse])
def cases(response: Response, db: Database, user: ReadUser, offset: Offset = 0, limit: Limit = 50):
    return evaluation.listing(db, response, "cases", offset=offset, limit=limit)


@router.post("/evaluation-cases", response_model=CaseResponse, status_code=201)
def create_case(body: CaseInput, db: Database, user: AdminUser):
    return evaluation.create_case(db, body, user)


@router.get("/analyses/{identifier}/evaluation-preview", response_model=CasePreview)
def preview_case(identifier: UUID, db: Database, user: AdminUser):
    return evaluation.preview_case(db, identifier)


@router.get("/evaluation-cases/{identifier}", response_model=CaseResponse)
def case_detail(identifier: UUID, db: Database, user: ReadUser):
    return active_record(db, EvaluationCase, identifier)


@router.post(
    "/evaluation-cases/{identifier}/replays", response_model=ReplayResponse, status_code=202
)
def replay(identifier: UUID, body: ReplayInput, db: Database, user: WriteUser):
    return evaluation.enqueue(db, identifier, body, user)


@router.get("/evaluation-runs", response_model=list[ReplayResponse])
def runs(
    response: Response,
    db: Database,
    user: ReadUser,
    case_id: UUID | None = None,
    offset: Offset = 0,
    limit: Limit = 50,
):
    return evaluation.listing(db, response, "runs", case_id, offset, limit)


@router.get("/evaluation-runs/compare")
def compare(left: UUID, right: UUID, db: Database, user: ReadUser):
    result = evaluation.compare(db, left, right)
    return {
        "comparable_provider": result["comparable_provider"],
        "left": ReplayResponse.model_validate(result["left"]),
        "right": ReplayResponse.model_validate(result["right"]),
    }


@router.get("/evaluation-runs/{identifier}", response_model=ReplayResponse)
def run_detail(identifier: UUID, db: Database, user: ReadUser):
    return active_record(db, EvaluationRun, identifier)


@router.post("/evaluation-runs/{identifier}/review", response_model=ReplayResponse)
def review_run(identifier: UUID, body: ReplayReview, db: Database, user: WriteUser):
    return evaluation.review_replay(db, identifier, body, user)


@router.get("/predictions/{identifier}/outcomes", response_model=list[OutcomeResponse])
def outcomes(
    identifier: UUID,
    response: Response,
    db: Database,
    user: ReadUser,
    offset: Offset = 0,
    limit: Limit = 50,
):
    return prediction_outcomes.listing(db, response, identifier, offset, limit)


@router.post("/prediction-outcomes/{identifier}/incidents", response_model=OutcomeResponse)
def link(identifier: UUID, body: OutcomeLink, db: Database, user: WriteUser):
    return prediction_outcomes.link(db, identifier, body, user)
