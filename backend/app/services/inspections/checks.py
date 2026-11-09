"""Version-bound check definitions. Definitions belong to the existing task version."""

from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import HTTPException

from app.repositories import inspections as repository
from app.schemas.inspections import InspectionCheck
from app.services import service_databases
from app.services.inspections import data_sources, sources
from app.utils.redaction import redact


def configuration(db, plan):
    snapshot = {"definitions": plan.check_items, "skills": {}, "queries": {}, "databases": {}}
    for value in plan.check_items:
        check = InspectionCheck.model_validate(value)
        if not check.enabled or check.kind != "agent":
            continue
        for identifier in [check.skill_id, *check.auxiliary_skill_ids]:
            skill = repository.skill(db, identifier)
            if (
                not skill
                or skill.deleted_at
                or not skill.enabled
                or skill.applicable_versions
                or (skill.expires_at and skill.expires_at <= datetime.now(UTC))
                or (skill.application_ids and str(plan.application_id) not in skill.application_ids)
            ):
                raise HTTPException(409, "inspection_skill_unavailable")
            snapshot["skills"][str(identifier)] = {
                "id": str(identifier),
                "version": skill.version,
                "content": skill.content,
                "scripts": skill.scripts,
                "resources": skill.resources,
            }
        for identifier in check.source_ids:
            row = data_sources.active(db, identifier)
            if (
                not row.enabled
                or row.environment != plan.environment
                or (row.application_id and row.application_id != plan.application_id)
            ):
                raise HTTPException(409, "inspection_source_scope_unavailable")
            data_sources.validate(db, row)
            if row.source_kind == "api" and not row.source_config.get("agent_api_enabled"):
                raise HTTPException(422, "inspection_api_not_authorized")
            snapshot["queries"][str(identifier)] = row.version
            if row.source_kind == "database":
                snapshot["databases"][str(identifier)] = service_databases.frozen(
                    sources.validate_database(db, row)
                )
    return redact(snapshot)


def guard(db, plan, run):
    frozen = run.snapshot["checks"]
    current = configuration(db, plan)
    if current != frozen:
        raise ValueError("inspection_check_configuration_changed")


def definition(run, check_id):
    return InspectionCheck.model_validate(
        next(c for c in run.snapshot["checks"]["definitions"] if c["check_id"] == check_id)
    )


def query_page(db, row, run, limit, cursor, lookup=None, parameters=None):
    context = SimpleNamespace(
        window_start=run.window_start,
        window_end=run.window_end,
        page_cursor=cursor,
        snapshot={"database": run.snapshot["checks"]["databases"].get(str(row.id))},
    )
    if row.source_kind == "database":
        if row.source_config.get("query_sql"):
            from app.schemas.inspections import InspectionSource

            connection = sources.validate_database(db, row)
            if service_databases.frozen(connection) != context.snapshot["database"]:
                raise ValueError("database_configuration_changed")
            return sources.saved_query_page(
                InspectionSource.model_validate(row.source_config),
                connection,
                context,
                limit,
                lookup,
                parameters,
            )
        if parameters:
            raise ValueError("inspection_query_parameters_not_authorized")
        return sources.database_page(db, row, context, limit, lookup)
    raise ValueError("inspection_database_query_required")
