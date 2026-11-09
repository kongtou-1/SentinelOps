"""Page persisted execution facts without loading a run's full evidence into memory."""

from sqlalchemy import DateTime, String, and_, cast, func, literal, or_, select, true, union_all
from sqlalchemy.types import JSON

from app.models import InspectionCheckResult as Check
from app.models import InspectionItem as Item
from app.models import InspectionTrace as Trace


def references(run, before=None, limit=61):
    def event(kind, identifier, at):
        return select(
            literal(kind).label("kind"),
            (literal(kind + ":") + cast(identifier, String)).label("id"),
            at.label("occurred_at"),
            cast(identifier, String).label("record_id"),
            literal(None, String).label("call_id"),
        )

    queries = [
        event("queued", literal(str(run.id)), literal(run.created_at, DateTime(timezone=True))),
    ]
    for kind, at in (("started", run.started_at), ("finished", run.finished_at)):
        if at:
            queries.append(event(kind, literal(str(run.id)), literal(at, DateTime(timezone=True))))
    visible_item = or_(Check.item_id.is_(None), Item.deleted_at.is_(None))
    for kind, at in (("check_started", Check.started_at), ("check_finished", Check.finished_at)):
        queries.append(
            event(kind, Check.id, at)
            .outerjoin(Item, Item.id == Check.item_id)
            .where(
                Check.run_id == run.id, Check.deleted_at.is_(None), visible_item, at.is_not(None)
            )
        )
    queries.extend(
        [
            event("object", Item.id, Item.created_at).where(
                Item.run_id == run.id, Item.deleted_at.is_(None)
            ),
            event("tool", Trace.id, Trace.created_at)
            .join(Check, Check.id == Trace.check_result_id)
            .outerjoin(Item, Item.id == Check.item_id)
            .where(
                Trace.run_id == run.id,
                Trace.deleted_at.is_(None),
                Check.deleted_at.is_(None),
                visible_item,
            ),
        ]
    )
    if not run.snapshot.get("checks"):
        calls = func.json_array_elements(Item.evidence["tool_calls"]).table_valued("value")
        calls = calls.render_derived(name="calls").lateral()
        value = cast(calls.c.value, JSON)
        queries.append(
            select(
                literal("legacy_tool").label("kind"),
                (
                    literal("legacy_tool:")
                    + cast(Item.id, String)
                    + literal(":")
                    + value["id"].as_string()
                ).label("id"),
                cast(value["observed_at"].as_string(), DateTime(timezone=True)).label(
                    "occurred_at"
                ),
                cast(Item.id, String).label("record_id"),
                value["id"].as_string().label("call_id"),
            )
            .select_from(Item)
            .join(calls, true())
            .where(Item.run_id == run.id, Item.deleted_at.is_(None))
        )
    events = union_all(*queries).subquery()
    query = select(events)
    if before:
        at, identifier = before
        query = query.where(
            or_(
                events.c.occurred_at < at,
                and_(events.c.occurred_at == at, events.c.id < identifier),
            )
        )
    return query.order_by(events.c.occurred_at.desc(), events.c.id.desc()).limit(limit)


def records(db, model, identifiers):
    return (
        {
            str(row.id): row
            for row in db.scalars(
                select(model).where(model.id.in_(identifiers), model.deleted_at.is_(None))
            )
        }
        if identifiers
        else {}
    )


def current(db, run):
    if run.snapshot.get("checks"):
        return db.scalar(
            select(Check)
            .outerjoin(Item, Item.id == Check.item_id)
            .where(
                Check.run_id == run.id,
                Check.deleted_at.is_(None),
                or_(Check.item_id.is_(None), Item.deleted_at.is_(None)),
                Check.execution_status == "running",
            )
            .order_by(Check.started_at, Check.id)
            .limit(1)
        )
    return db.scalar(
        select(Item)
        .where(Item.run_id == run.id, Item.deleted_at.is_(None), Item.agent_status == "running")
        .order_by(Item.created_at, Item.id)
        .limit(1)
    )
