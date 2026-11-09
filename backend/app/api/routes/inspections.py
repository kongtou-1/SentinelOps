from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.api.dependencies import Database, require_permission
from app.models import InspectionItem, InspectionRun, User
from app.repositories import inspections as repository
from app.schemas.inspections import (
    InspectionCheckResultResponse,
    InspectionConnectionTestResponse,
    InspectionDataSourceInput,
    InspectionDataSourceResponse,
    InspectionHTTPConnectionInput,
    InspectionHTTPConnectionResponse,
    InspectionItemResponse,
    InspectionPlanInput,
    InspectionPlanResponse,
    InspectionProgressResponse,
    InspectionQueryPreview,
    InspectionQueryPreviewResponse,
    InspectionReviewInput,
    InspectionReviewResponse,
    InspectionRunResponse,
    InspectionScheduleInput,
    InspectionScheduleResponse,
    InspectionStateInput,
    InspectionSummary,
    InspectionTraceResponse,
    InspectionTrialInput,
    InspectionTriggerInput,
)
from app.schemas.service_databases import DatabaseResponse
from app.services import service_databases
from app.services.inspections import connections as http_connections
from app.services.inspections import data_sources, outcomes, plans, progress, task_actions
from app.services.pagination import page
from app.services.records import active_record

router = APIRouter(prefix="/api/v1", tags=["inspections"])
ReadUser = Annotated[User, Depends(require_permission("inspections:read"))]
ManageUser = Annotated[User, Depends(require_permission("inspections:manage"))]
RunUser = Annotated[User, Depends(require_permission("inspections:run"))]
ReviewUser = Annotated[User, Depends(require_permission("inspections:review"))]
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=100)]


@router.post("/inspection-trials", response_model=InspectionRunResponse, status_code=202)
def trial(body: InspectionTrialInput, db: Database, user: ManageUser):
    return task_actions.trial(db, body, user)


@router.post("/inspection-schedule-preview", response_model=InspectionScheduleResponse)
def schedule_preview(body: InspectionScheduleInput, user: ManageUser):
    return task_actions.schedule_preview(body)


@router.post(
    "/inspection-plans/{identifier}/copy", response_model=InspectionPlanResponse, status_code=201
)
def copy_plan(identifier: UUID, db: Database, user: ManageUser):
    return task_actions.copy(db, identifier, user)


@router.get("/inspection-http-connections", response_model=list[InspectionHTTPConnectionResponse])
def http_connection_list(
    db: Database, user: ManageUser, response: Response, offset: Offset = 0, limit: Limit = 50
):
    return [
        http_connections.response(row)
        for row in page(db, http_connections.listing(), response, offset, limit)
    ]


@router.post(
    "/inspection-http-connections", response_model=InspectionHTTPConnectionResponse, status_code=201
)
def http_connection_create(body: InspectionHTTPConnectionInput, db: Database, user: ManageUser):
    return http_connections.configure(db, body, user)


@router.put(
    "/inspection-http-connections/{identifier}", response_model=InspectionHTTPConnectionResponse
)
def http_connection_update(
    identifier: UUID, body: InspectionHTTPConnectionInput, db: Database, user: ManageUser
):
    return http_connections.configure(db, body, user, identifier)


@router.delete("/inspection-http-connections/{identifier}", status_code=204)
def http_connection_delete(identifier: UUID, db: Database, user: ManageUser):
    http_connections.remove(db, identifier, user)


@router.post(
    "/inspection-data-sources/{identifier}/preview", response_model=InspectionQueryPreviewResponse
)
def query_preview(identifier: UUID, body: InspectionQueryPreview, db: Database, user: ManageUser):
    return data_sources.preview(db, identifier, body, user)


@router.get(
    "/inspection-runs/{identifier}/check-results",
    response_model=list[InspectionCheckResultResponse],
)
def check_results(
    identifier: UUID,
    db: Database,
    user: ReadUser,
    response: Response,
    offset: Offset = 0,
    limit: Limit = 50,
    item_id: UUID | None = None,
    scope: str | None = Query(default=None, pattern="^(object|collection)$"),
):
    active_record(db, InspectionRun, identifier)
    return page(db, repository.check_results(identifier, item_id, scope), response, offset, limit)


@router.get("/inspection-runs/{identifier}/traces", response_model=list[InspectionTraceResponse])
def traces(
    identifier: UUID,
    db: Database,
    user: ReadUser,
    response: Response,
    offset: Offset = 0,
    limit: Limit = 50,
    check_result_id: UUID | None = None,
):
    active_record(db, InspectionRun, identifier)
    return outcomes.trace_responses(
        db, page(db, repository.traces(identifier, check_result_id), response, offset, limit)
    )


@router.get("/inspections/summary", response_model=InspectionSummary)
def summary(db: Database, user: ReadUser):
    return repository.summary(db)


@router.get("/inspection-runs/{identifier}/progress", response_model=InspectionProgressResponse)
def run_progress(
    identifier: UUID,
    db: Database,
    user: ReadUser,
    before: str | None = Query(default=None, max_length=400),
    limit: Limit = 60,
):
    return progress.listing(db, active_record(db, InspectionRun, identifier), before, limit)


@router.get("/inspection-connections", response_model=list[DatabaseResponse])
def connections(
    db: Database, user: ManageUser, environment: str | None = Query(default=None, max_length=50)
):
    result = []
    for row in repository.connections(db, environment):
        value = service_databases.response(row)
        value.allowed_tables = {
            table: [
                c
                for c in columns
                if c
                in {
                    field["name"]
                    for field in row.schema_cache.get("tables", {})
                    .get(table, {})
                    .get("columns", [])
                }
            ]
            for table, columns in row.allowed_tables.items()
            if table in row.schema_cache.get("tables", {})
        }
        result.append(value)
    return result


@router.get("/inspection-items", response_model=list[InspectionItemResponse])
def results(
    db: Database,
    user: ReadUser,
    response: Response,
    plan_id: UUID | None = None,
    record_id: str | None = Query(default=None, max_length=200),
    verdict: str | None = Query(
        default=None, pattern="^(supported|contradicted|needs_review|unassessable)$"
    ),
    latest_only: bool = True,
    offset: Offset = 0,
    limit: Limit = 50,
):
    rows = page(
        db, repository.results(plan_id, record_id, verdict, latest_only), response, offset, limit
    )
    return [outcomes.item_response(row) for row in rows]


@router.get("/inspection-items/{identifier}", response_model=InspectionItemResponse)
def item_detail(identifier: UUID, db: Database, user: ReadUser):
    return outcomes.item_response(active_record(db, InspectionItem, identifier))


@router.delete("/inspection-items/{identifier}", status_code=204)
def remove_item(identifier: UUID, db: Database, user: ManageUser):
    plans.remove_item(db, identifier, user)


@router.get("/inspection-plans", response_model=list[InspectionPlanResponse])
def listing(
    db: Database,
    user: ReadUser,
    response: Response,
    application_id: UUID | None = None,
    search: str | None = Query(default=None, max_length=100),
    enabled: bool | None = None,
    environment: str | None = Query(default=None, max_length=50),
    offset: Offset = 0,
    limit: Limit = 50,
):
    return plans.listing(db, response, application_id, offset, limit, search, enabled, environment)


@router.get("/inspection-plans/{identifier}", response_model=InspectionPlanResponse)
def plan_detail(identifier: UUID, db: Database, user: ReadUser):
    row = repository.plan(db, identifier)
    if not row:
        from fastapi import HTTPException

        raise HTTPException(404, "Inspection plan not found")
    application = repository.application(db, row.application_id) if row.application_id else None
    return plans.response(row, application.name if application else None)


@router.post("/inspection-plans", response_model=InspectionPlanResponse, status_code=201)
def create(body: InspectionPlanInput, db: Database, user: ManageUser):
    return plans.configure(db, body, user)


@router.put("/inspection-plans/{identifier}", response_model=InspectionPlanResponse)
def update(identifier: UUID, body: InspectionPlanInput, db: Database, user: ManageUser):
    return plans.configure(db, body, user, identifier)


@router.put("/inspection-plans/{identifier}/state", response_model=InspectionPlanResponse)
def set_state(identifier: UUID, body: InspectionStateInput, db: Database, user: ManageUser):
    return plans.set_state(db, identifier, body, user)


@router.delete("/inspection-plans/{identifier}", status_code=204)
def remove(identifier: UUID, db: Database, user: ManageUser, expected_version: int):
    plans.remove(db, identifier, expected_version, user)


@router.post(
    "/inspection-plans/{identifier}/runs", response_model=InspectionRunResponse, status_code=202
)
def run_now(identifier: UUID, db: Database, user: RunUser):
    return plans.run_now(db, identifier, user)


@router.post(
    "/inspection-plans/{identifier}/trigger", response_model=InspectionRunResponse, status_code=202
)
def trigger(identifier: UUID, body: InspectionTriggerInput, db: Database, user: RunUser):
    return plans.trigger(db, identifier, body, user)


@router.get("/inspection-runs", response_model=list[InspectionRunResponse])
def runs(
    db: Database,
    user: ReadUser,
    response: Response,
    plan_id: UUID | None = None,
    search: str | None = Query(default=None, max_length=100),
    status: str | None = Query(
        default=None, pattern="^(queued|collecting|evaluating|completed|partial|failed|cancelled)$"
    ),
    offset: Offset = 0,
    limit: Limit = 50,
    run_kind: str | None = Query(default=None, pattern="^(standard|trial)$"),
    has_objects: bool | None = Query(default=None, description="按是否发现检查对象筛选"),
):
    return outcomes.run_responses(
        db,
        page(
            db,
            repository.runs(plan_id, search, status, run_kind, has_objects),
            response,
            offset,
            limit,
        ),
    )


@router.get("/inspection-runs/{identifier}", response_model=InspectionRunResponse)
def run_detail(identifier: UUID, db: Database, user: ReadUser):
    return outcomes.run_responses(db, [active_record(db, InspectionRun, identifier)])[0]


@router.delete("/inspection-runs/{identifier}", status_code=204)
def remove_run(identifier: UUID, db: Database, user: ManageUser):
    plans.remove_run(db, identifier, user)


@router.post("/inspection-runs/{identifier}/cancel", response_model=InspectionRunResponse)
def cancel(identifier: UUID, db: Database, user: RunUser):
    return plans.cancel(db, identifier, user)


@router.get("/inspection-runs/{identifier}/items", response_model=list[InspectionItemResponse])
def items(
    identifier: UUID,
    db: Database,
    user: ReadUser,
    response: Response,
    verdict: str | None = None,
    search: str | None = Query(default=None, max_length=200),
    result: str | None = Query(default=None, pattern="^(pass|fail|unknown)$"),
    offset: Offset = 0,
    limit: Limit = 50,
):
    active_record(db, InspectionRun, identifier)
    return [
        outcomes.item_response(row)
        for row in page(
            db, repository.items(identifier, verdict, search, result), response, offset, limit
        )
    ]


@router.get("/inspection-data-sources", response_model=list[InspectionDataSourceResponse])
def source_listing(
    db: Database,
    user: ReadUser,
    response: Response,
    search: str | None = Query(default=None, max_length=100),
    kind: str | None = Query(default=None, pattern="^(api|database)$"),
    http_connection_id: UUID | None = None,
    offset: Offset = 0,
    limit: Limit = 50,
):
    return [
        data_sources.response(row)
        for row in page(
            db, repository.data_sources(search, kind, http_connection_id), response, offset, limit
        )
    ]


@router.post(
    "/inspection-data-sources", response_model=InspectionDataSourceResponse, status_code=201
)
def source_create(body: InspectionDataSourceInput, db: Database, user: ManageUser):
    return data_sources.configure(db, body, user)


@router.get("/inspection-data-sources/{identifier}", response_model=InspectionDataSourceResponse)
def source_detail(identifier: UUID, db: Database, user: ReadUser):
    return data_sources.response(data_sources.active(db, identifier))


@router.put("/inspection-data-sources/{identifier}", response_model=InspectionDataSourceResponse)
def source_update(
    identifier: UUID, body: InspectionDataSourceInput, db: Database, user: ManageUser
):
    return data_sources.configure(db, body, user, identifier)


@router.post(
    "/inspection-data-sources/{identifier}/test", response_model=InspectionConnectionTestResponse
)
def source_test(identifier: UUID, db: Database, user: ManageUser):
    return data_sources.test_connection(db, identifier, user)


@router.get(
    "/inspection-data-sources/{identifier}/plans", response_model=list[InspectionPlanResponse]
)
def source_plans(
    identifier: UUID,
    db: Database,
    user: ReadUser,
    response: Response,
    offset: Offset = 0,
    limit: Limit = 50,
):
    data_sources.active(db, identifier)
    return [
        plans.response(row)
        for row in page(db, repository.source_references(identifier), response, offset, limit)
    ]


@router.delete("/inspection-data-sources/{identifier}", status_code=204)
def source_delete(identifier: UUID, db: Database, user: ManageUser):
    data_sources.remove(db, identifier, user)


@router.get("/inspection-items/{identifier}/reviews", response_model=list[InspectionReviewResponse])
def reviews(
    identifier: UUID,
    db: Database,
    user: ReadUser,
    response: Response,
    offset: Offset = 0,
    limit: Limit = 50,
):
    active_record(db, InspectionItem, identifier)
    return page(db, repository.reviews(identifier), response, offset, limit)


@router.post(
    "/inspection-items/{identifier}/reviews",
    response_model=InspectionReviewResponse,
    status_code=201,
)
def review(identifier: UUID, body: InspectionReviewInput, db: Database, user: ReviewUser):
    return plans.review(db, identifier, body, user)


@router.post(
    "/inspection-runs/{identifier}/resume", response_model=InspectionRunResponse, status_code=202
)
def resume(identifier: UUID, db: Database, user: RunUser):
    from app.services.inspections.recovery import resume as resume_run

    return resume_run(db, identifier, user)
