"""Storage operations for version-scoped code rules on a caller-owned Session."""

from __future__ import annotations

from sqlalchemy import func, select

from app.models import AskDataCodeRule, AskDataModelVersion, User


def _conditions(
    version_id,
    *,
    language=None,
    kind=None,
    parse_state=None,
    review_required=None,
    conflict_state=None,
    include_superseded=False,
):
    conditions = [
        AskDataCodeRule.model_version_id == version_id,
        AskDataCodeRule.deleted_at.is_(None),
    ]
    if language:
        conditions.append(AskDataCodeRule.language == language)
    if kind:
        conditions.append(AskDataCodeRule.rule_kind == kind)
    if parse_state:
        conditions.append(AskDataCodeRule.parse_state == parse_state)
    if review_required is not None:
        conditions.append(AskDataCodeRule.review_required == review_required)
    if conflict_state:
        conditions.append(AskDataCodeRule.conflict_state == conflict_state)
    if not include_superseded:
        conditions.append(AskDataCodeRule.superseded.is_(False))
    return conditions


def counts(db, version_id):
    base = [
        AskDataCodeRule.model_version_id == version_id,
        AskDataCodeRule.deleted_at.is_(None),
    ]
    total, pending, conflicts, static_sql, dynamic = db.execute(
        select(
            func.count().filter(AskDataCodeRule.superseded.is_(False)),
            func.count().filter(AskDataCodeRule.review_required.is_(True)),
            func.count().filter(AskDataCodeRule.conflict_state == "pending"),
            func.count().filter(
                AskDataCodeRule.parse_state == "static",
                AskDataCodeRule.sql_text != "",
                AskDataCodeRule.superseded.is_(False),
            ),
            func.count().filter(
                AskDataCodeRule.parse_state == "dynamic",
                AskDataCodeRule.superseded.is_(False),
            ),
        )
        .select_from(AskDataCodeRule)
        .where(*base)
        .execution_options(access_internal=True)
    ).one()
    languages = dict(
        db.execute(
            select(AskDataCodeRule.language, func.count())
            .where(*base, AskDataCodeRule.superseded.is_(False))
            .group_by(AskDataCodeRule.language)
            .execution_options(access_internal=True)
        ).all()
    )
    files = int(
        db.scalar(
            select(func.count(func.distinct(AskDataCodeRule.path)))
            .where(*base, AskDataCodeRule.superseded.is_(False))
            .execution_options(access_internal=True)
        )
        or 0
    )
    return {
        "rule_count": int(total or 0),
        "pending_review": int(pending or 0),
        "conflicts": int(conflicts or 0),
        "static_sql_count": int(static_sql or 0),
        "dynamic_count": int(dynamic or 0),
        "languages": languages,
        "file_count": files,
    }


def rules(
    db,
    version_id,
    *,
    language=None,
    kind=None,
    parse_state=None,
    review_required=None,
    conflict_state=None,
    include_superseded=False,
    offset=0,
    limit=50,
):
    conditions = _conditions(
        version_id,
        language=language,
        kind=kind,
        parse_state=parse_state,
        review_required=review_required,
        conflict_state=conflict_state,
        include_superseded=include_superseded,
    )
    statement = select(AskDataCodeRule).where(*conditions)
    total, pending, conflicts = db.execute(
        select(
            func.count(),
            func.count().filter(AskDataCodeRule.review_required.is_(True)),
            func.count().filter(AskDataCodeRule.conflict_state == "pending"),
        )
        .select_from(AskDataCodeRule)
        .where(*conditions)
        .execution_options(access_internal=True)
    ).one()
    summary = {"rule_count": total, "pending_review": pending, "conflicts": conflicts}
    rows = list(
        db.scalars(
            statement.order_by(
                AskDataCodeRule.conflict_state.desc(),
                AskDataCodeRule.review_required.desc(),
                AskDataCodeRule.path,
                AskDataCodeRule.start_line,
                AskDataCodeRule.rule_kind,
                AskDataCodeRule.id,
            )
            .offset(offset)
            .limit(limit)
            .execution_options(access_internal=True)
        )
    )
    return rows, summary


def rule(db, version_id, rule_id):
    return db.scalar(
        select(AskDataCodeRule)
        .where(
            AskDataCodeRule.id == rule_id,
            AskDataCodeRule.model_version_id == version_id,
            AskDataCodeRule.deleted_at.is_(None),
        )
        .execution_options(access_internal=True, populate_existing=True)
    )


def previous_version(db, connection_id, version_id, *, preferred_id=None):
    """Most recent usable version other than the one being built."""
    rows = list(
        db.scalars(
            select(AskDataModelVersion)
            .where(
                AskDataModelVersion.connection_id == connection_id,
                AskDataModelVersion.id != version_id,
                AskDataModelVersion.status.in_(("ready", "retired")),
                AskDataModelVersion.deleted_at.is_(None),
            )
            .order_by(AskDataModelVersion.created_at.desc())
            .execution_options(access_internal=True)
        )
    )
    if preferred_id:
        for row in rows:
            if row.id == preferred_id:
                return row
    return rows[0] if rows else None


def previous_rules(db, previous_version_id):
    return list(
        db.scalars(
            select(AskDataCodeRule)
            .where(
                AskDataCodeRule.model_version_id == previous_version_id,
                AskDataCodeRule.deleted_at.is_(None),
            )
            .execution_options(access_internal=True)
        )
    )


def static_fragments(db, version_id, *, limit=200):
    return list(
        db.scalars(
            select(AskDataCodeRule)
            .where(
                AskDataCodeRule.model_version_id == version_id,
                AskDataCodeRule.parse_state == "static",
                AskDataCodeRule.sql_text != "",
                AskDataCodeRule.superseded.is_(False),
                AskDataCodeRule.conflict_state != "pending",
                AskDataCodeRule.review_required.is_(False),
                AskDataCodeRule.deleted_at.is_(None),
            )
            .order_by(AskDataCodeRule.path, AskDataCodeRule.start_line)
            .limit(limit)
            .execution_options(access_internal=True)
        )
    )


def context_rules(db, version_id, *, limit=60):
    """Effective, non-superseded rules usable as prompt context and evidence."""
    return list(
        db.scalars(
            select(AskDataCodeRule)
            .where(
                AskDataCodeRule.model_version_id == version_id,
                AskDataCodeRule.superseded.is_(False),
                AskDataCodeRule.conflict_state != "pending",
                AskDataCodeRule.review_required.is_(False),
                AskDataCodeRule.deleted_at.is_(None),
            )
            .order_by(
                AskDataCodeRule.certified.desc(),
                AskDataCodeRule.rule_kind,
                AskDataCodeRule.path,
                AskDataCodeRule.start_line,
            )
            .limit(limit)
            .execution_options(access_internal=True)
        )
    )


def actor_names(db, identifiers):
    if not identifiers:
        return {}
    rows = db.execute(
        select(User.id, User.username)
        .where(User.id.in_(identifiers))
        .execution_options(access_internal=True)
    ).all()
    return {identifier: name for identifier, name in rows}


def save(db, row):
    db.add(row)
    return row
