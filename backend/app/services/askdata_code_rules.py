"""Stage-4 source-rule build, review and context services.

Source code is only ever read through the existing hash-checked snapshot
reader. Nothing from a monitored repository is imported or executed. Rules are
version-scoped: a rebuild writes a fresh set, carries human revisions forward
and flags changed / removed code instead of overwriting published evidence.
"""

from __future__ import annotations

import hashlib
import logging
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from app.db.session import SessionLocal
from app.integrations.askdata_code import extract_rules, language_for
from app.integrations.askdata_code.sql_analysis import authorized_fragment, resolve_table
from app.integrations.git import SourceUnavailable
from app.models import (
    ApplicationDeployment,
    ApplicationRelease,
    AskDataCodeRule,
    AskDataModelVersion,
    SourceSnapshot,
)
from app.repositories import askdata as lifecycle
from app.repositories import askdata_code_rules as repository
from app.schemas.askdata_code_rules import (
    AskDataCodeRuleDetail,
    AskDataCodeRulePage,
    AskDataCodeRuleResponse,
    AskDataCodeRuleSummary,
    AskDataSourceSnapshotOption,
    AskDataSourceSnapshotPage,
)
from app.services import access_control, service_databases
from app.services import askdata as settings

logger = logging.getLogger(__name__)

MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_FILE_BYTES = 512 * 1024
MAX_SOURCE_FILES = 400
_BUSINESS_KINDS = {
    "status_filter",
    "soft_delete",
    "amount_unit",
    "time_field",
    "metric",
    "sql_fragment",
    "join",
}
_REVIEW_CHANGED = "代码规则已变化，待复核"
_REVIEW_REMOVED = "源码中已移除该规则，待复核"
_CONFLICT_NOTE = "人工口径与当前代码规则不一致，待处理"


def _version(db, user, version_id, permission="askdata:read"):
    row = lifecycle.model_version(db, version_id)
    if row is None:
        raise HTTPException(404, "问数模型版本不存在")
    connection = settings.authorized_connection(db, user, row.connection_id, permission)
    return row, connection


def _serialize(row, names=None) -> AskDataCodeRuleResponse:
    return AskDataCodeRuleResponse(
        id=row.id,
        connection_id=row.connection_id,
        model_version_id=row.model_version_id,
        source_snapshot_id=row.source_snapshot_id,
        git_commit=row.git_commit,
        svn_revision=row.svn_revision,
        content_hash=row.content_hash or "",
        language=row.language,
        path=row.path,
        start_line=row.start_line,
        end_line=row.end_line,
        snippet_hash=row.snippet_hash or "",
        rule_kind=row.rule_kind,
        object_key=row.object_key,
        expression=row.expression or "",
        sql_text=row.sql_text or "",
        detail=dict(row.detail or {}),
        parse_state=row.parse_state,
        source=row.source,
        fingerprint=row.fingerprint,
        certified=bool(row.certified),
        edited_by_name=(names or {}).get(row.edited_by, ""),
        review_required=bool(row.review_required),
        review_note=row.review_note or "",
        conflict_with=row.conflict_with,
        conflict_state=row.conflict_state,
        superseded=bool(row.superseded),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# --------------------------------------------------------------------------
# Snapshot selection: prefer the release deployed into the target environment.
# --------------------------------------------------------------------------


def _release_snapshot(db, connection):
    if connection.application_id is None or not connection.environment:
        return None
    return db.scalar(
        select(ApplicationRelease.snapshot_id)
        .join(
            ApplicationDeployment,
            ApplicationDeployment.release_id == ApplicationRelease.id,
        )
        .where(
            ApplicationDeployment.application_id == connection.application_id,
            ApplicationDeployment.environment == connection.environment,
            ApplicationDeployment.ended_at.is_(None),
            ApplicationRelease.snapshot_id.is_not(None),
        )
        .order_by(ApplicationDeployment.started_at.desc())
        .limit(1)
    )


def recommend_snapshot(db, user, connection):
    """Return ``(snapshot_id, origin, label)``; failure degrades to none."""
    identifier = _release_snapshot(db, connection)
    if identifier is None:
        return None, "none", ""
    snapshot = db.get(SourceSnapshot, identifier)
    if (
        snapshot is None
        or snapshot.deleted_at
        or snapshot.status not in ("ready", "partial")
        or snapshot.application_id != connection.application_id
    ):
        return None, "none", ""
    try:
        access_control.require(db, user, "sources:read", "application", snapshot.application_id)
    except HTTPException:
        return None, "none", ""
    return (
        snapshot.id,
        "release",
        f"目标环境 {connection.environment} 当前发布版本关联快照",
    )


def snapshot_options(db, user, connection_id) -> AskDataSourceSnapshotPage:
    connection = settings.authorized_connection(db, user, connection_id)
    recommended, origin, label = recommend_snapshot(db, user, connection)
    rows = []
    if connection.application_id:
        try:
            access_control.require(
                db, user, "sources:read", "application", connection.application_id
            )
        except HTTPException:
            return AskDataSourceSnapshotPage(
                items=[],
                recommended_id=None,
                origin=origin,
                label=label,
                application_id=connection.application_id,
                environment=connection.environment or "",
            )
        snapshots = list(
            db.scalars(
                select(SourceSnapshot)
                .where(
                    SourceSnapshot.application_id == connection.application_id,
                    SourceSnapshot.status.in_(("ready", "partial")),
                    SourceSnapshot.deleted_at.is_(None),
                )
                .order_by(SourceSnapshot.created_at.desc())
                .limit(20)
            )
        )
        release_ids = set(
            db.scalars(
                select(ApplicationRelease.snapshot_id).where(
                    ApplicationRelease.application_id == connection.application_id,
                    ApplicationRelease.snapshot_id.is_not(None),
                )
            )
        )
        for row in snapshots:
            identity = (row.git_commit or row.svn_revision or "")[:12]
            rows.append(
                AskDataSourceSnapshotOption(
                    id=row.id,
                    label=f"{identity or '本地源码'} · {row.created_at:%Y-%m-%d %H:%M}",
                    origin="release" if row.id in release_ids else "reference",
                    git_commit=row.git_commit,
                    svn_revision=row.svn_revision,
                    status=row.status,
                    file_count=row.file_count or 0,
                    recommended=row.id == recommended,
                )
            )
    return AskDataSourceSnapshotPage(
        items=rows,
        recommended_id=recommended,
        origin=origin,
        label=label,
        application_id=connection.application_id,
        environment=connection.environment or "",
    )


# --------------------------------------------------------------------------
# Build step: read snapshot text, extract rules, persist and index.
# --------------------------------------------------------------------------


def _read_snapshot_files(db, user, snapshot):
    """Only snapshot-relative, hash-checked text files within a byte budget."""
    access_control.require(db, user, "sources:read", "application", snapshot.application_id)
    from app.services.sources.store import read_snapshot_file

    files: dict[str, str] = {}
    gaps: dict = {"manifest_files": len(snapshot.manifest or [])}
    total = 0
    for item in snapshot.manifest or []:
        path = str(item.get("path", ""))
        if language_for(path) is None:
            continue
        if int(item.get("bytes", 0)) > MAX_FILE_BYTES:
            gaps.setdefault("oversized_files", []).append(path)
            continue
        if len(files) >= MAX_SOURCE_FILES or total + int(item.get("bytes", 0)) > MAX_SOURCE_BYTES:
            gaps["source_byte_limit"] = total
            break
        try:
            value = read_snapshot_file(snapshot, path)
        except SourceUnavailable as exc:
            code = (
                "source_snapshot_integrity_error"
                if str(exc) == "snapshot_integrity_error"
                else "source_snapshot_unavailable"
            )
            raise HTTPException(409, code) from None
        except (OSError, UnicodeError):
            raise HTTPException(409, "source_snapshot_unavailable") from None
        files[path] = value
        total += int(item.get("bytes", 0))
    return files, gaps


def _natural_key(row):
    return (row.language, row.path, row.rule_kind, row.object_key)


def _removed_fingerprint(prior) -> str:
    return hashlib.sha256(f"removed:{prior.fingerprint}".encode()).hexdigest()


def _build_rows(drafts, *, connection_id, version_id, identity, previous_rules):
    previous = {}
    for prior in previous_rules or []:
        previous.setdefault(_natural_key(prior), []).append(prior)
    rows: list[AskDataCodeRule] = []
    seen_fingerprints: set[str] = set()
    for draft in drafts:
        fingerprint = draft.fingerprint()
        if fingerprint in seen_fingerprints:
            continue
        seen_fingerprints.add(fingerprint)
        key = (draft.language, draft.path, draft.rule_kind, draft.object_key)
        detail = {**draft.detail, "snippet": draft.snippet[:600]}
        row = AskDataCodeRule(
            id=uuid4(),
            connection_id=connection_id,
            model_version_id=version_id,
            source_snapshot_id=identity["source_snapshot_id"],
            git_commit=identity["git_commit"],
            svn_revision=identity["svn_revision"],
            content_hash=identity["content_hash"],
            language=draft.language,
            path=draft.path[:500],
            start_line=draft.start_line,
            end_line=draft.end_line,
            snippet_hash=hashlib.sha256(draft.snippet.encode("utf-8")).hexdigest(),
            rule_kind=draft.rule_kind,
            object_key=draft.object_key[:300],
            expression=draft.expression[:4000],
            sql_text=draft.sql_text[:8000],
            detail=detail,
            parse_state=draft.parse_state,
            source="auto",
            fingerprint=fingerprint,
            certified=False,
            review_required=False,
            conflict_state="none",
            superseded=False,
        )
        candidates = previous.get(key, [])
        prior = min(
            candidates,
            key=lambda item: (
                item.fingerprint != fingerprint,
                (item.detail or {}).get("code_expression", item.expression) != draft.expression,
                abs(item.start_line - draft.start_line),
            ),
            default=None,
        )
        if prior is not None:
            candidates.remove(prior)
        if prior is not None and (prior.source == "manual" or prior.certified):
            row.source = "manual"
            row.expression = prior.expression
            row.detail = {**detail, "code_expression": draft.expression}
            if (prior.detail or {}).get("description"):
                row.detail["description"] = prior.detail["description"]
            row.certified = bool(prior.certified)
            row.edited_by = prior.edited_by
            row.review_required = bool(prior.review_required)
            row.review_note = prior.review_note or ""
            row.conflict_state = prior.conflict_state or "none"
            row.conflict_with = prior.conflict_with
            if prior.fingerprint != fingerprint:
                row.review_required = True
                row.review_note = _REVIEW_CHANGED
                row.conflict_with = prior.id
                if prior.expression != draft.expression:
                    row.conflict_state = "pending"
                    row.review_note = _CONFLICT_NOTE
        rows.append(row)

    for prior in (item for group in previous.values() for item in group):
        if not (prior.certified or prior.source == "manual" or prior.rule_kind in _BUSINESS_KINDS):
            continue
        rows.append(
            AskDataCodeRule(
                id=uuid4(),
                connection_id=connection_id,
                model_version_id=version_id,
                source_snapshot_id=prior.source_snapshot_id,
                git_commit=prior.git_commit,
                svn_revision=prior.svn_revision,
                content_hash=prior.content_hash or "",
                language=prior.language,
                path=prior.path,
                start_line=prior.start_line,
                end_line=prior.end_line,
                snippet_hash=prior.snippet_hash,
                rule_kind=prior.rule_kind,
                object_key=prior.object_key,
                expression=prior.expression,
                sql_text="",
                detail={**(prior.detail or {}), "code_expression": ""},
                parse_state="dynamic",
                source=prior.source,
                fingerprint=prior.fingerprint if prior.superseded else _removed_fingerprint(prior),
                certified=bool(prior.certified),
                edited_by=prior.edited_by,
                review_required=True,
                review_note=_REVIEW_REMOVED,
                conflict_with=prior.id,
                conflict_state="pending",
                superseded=True,
            )
        )
    return rows


def _index_rules(version_id, rows):
    """Add code rules to the semantic vector collection as retrieval candidates."""
    if not rows:
        return 0
    from app.integrations.askdata_semantic import PlatformVectorStore, embedding_factory

    candidates = [row for row in rows if not row.superseded][:200]
    texts = [
        f"{row.rule_kind} {row.object_key}: {row.expression} 来源 {row.path}:{row.start_line}"
        for row in candidates
    ]
    try:
        vectors = embedding_factory(texts)
    except Exception as exc:  # local embedding model may be absent in a test host
        logger.warning(
            "Code-rule embedding unavailable (version=%s): %s", version_id, type(exc).__name__
        )
        return 0
    records = []
    for row, text, vector in zip(candidates, texts, vectors, strict=True):
        if not vector:
            continue
        path = f"code_rule:{row.id}"
        records.append(
            {
                "text": text,
                "path": path,
                "metadata": {
                    "source_path": path,
                    "embedding": vector,
                    "source": "code_rule",
                    "label": "CodeRule",
                    "rule_kind": row.rule_kind,
                    "object_key": row.object_key,
                    "language": row.language,
                    "certified": bool(row.certified),
                    "model_version_id": str(version_id),
                    "path": row.path,
                    "line": row.start_line,
                },
            }
        )
    if not records:
        return 0
    try:
        return int(PlatformVectorStore(version_id).run([records]) or 0)
    except Exception as exc:
        logger.warning(
            "Code-rule vector index failed (version=%s): %s", version_id, type(exc).__name__
        )
        return 0


def extract_for_build(identifier, user_id, connection_id, version_id, *, source_reader=None):
    """Build step entry point. Returns counts written into ``builds.detail``."""
    with SessionLocal() as db:
        version = db.get(AskDataModelVersion, version_id)
        user = lifecycle.active_user(db, user_id)
        connection = lifecycle.connection(db, connection_id)
        if version is None or user is None or connection is None:
            raise HTTPException(403, "构建上下文不可用")
        settings.validate_version(db, user, connection_id, version_id, "askdata:manage")
        schema = connection.schema_cache.get("tables", {})
        allowed = {
            key: {"columns": [col for col in table["columns"] if col["name"] in grants]}
            for key, grants in connection.allowed_tables.items()
            if connection.query_enabled and (table := schema.get(key)) is not None
        }
        dialect = "postgres" if connection.engine == "postgresql" else "mysql"
        configuration = lifecycle.configuration(db, connection_id)
        active_version_id = configuration.active_model_version_id if configuration else None
        snapshot_id = version.source_snapshot_id
        origin = (version.context or {}).get("source_origin", "manual" if snapshot_id else "none")
        label = (version.context or {}).get("source_label", "")
        snapshot = db.get(SourceSnapshot, snapshot_id) if snapshot_id else None
        if snapshot_id and (
            snapshot is None
            or snapshot.deleted_at
            or snapshot.status not in ("ready", "partial")
            or snapshot.application_id != connection.application_id
        ):
            raise HTTPException(404, "模型源码快照不可用")
        identity = {
            "source_snapshot_id": snapshot_id,
            "git_commit": snapshot.git_commit if snapshot else None,
            "svn_revision": snapshot.svn_revision if snapshot else None,
            "content_hash": (snapshot.content_hash or "") if snapshot else "",
        }
        previous = repository.previous_version(
            db, connection_id, version_id, preferred_id=active_version_id
        )
        prior_rows = repository.previous_rules(db, previous.id) if previous else []
        if source_reader is not None:
            files, read_gaps = source_reader(snapshot, user, db)
        elif snapshot is not None:
            files, read_gaps = _read_snapshot_files(db, user, snapshot)
        else:
            files, read_gaps = {}, {"reason": "no_source_snapshot"}

    drafts, gaps = extract_rules(files)
    gaps = {**read_gaps, **gaps}
    rows = _build_rows(
        drafts,
        connection_id=connection_id,
        version_id=version_id,
        identity=identity,
        previous_rules=prior_rows,
    )
    with SessionLocal() as db:
        actor = lifecycle.active_user(db, user_id)
        if actor is None:
            raise HTTPException(403, "构建授权已撤销")
        settings.validate_version(db, actor, connection_id, version_id, "askdata:manage")
        build = lifecycle.build(db, identifier)
        version = db.get(AskDataModelVersion, version_id)
        if build is not None:
            build.detail = {**(build.detail or {}), "source_snapshot_origin": origin}
        if version is not None:
            version.context = {
                **(version.context or {}),
                "source_origin": origin,
                "source_label": label,
                "source_gaps": gaps,
            }
        for row in rows:
            repository.save(db, row)
        db.commit()
    indexed = _index_rules(version_id, _authorized_rules(rows, allowed, dialect))
    return {
        "source_rules": len(rows),
        "source_files": len(files),
        "code_embeddings": indexed,
        "source_static_sql": sum(1 for row in rows if row.sql_text),
        "source_dynamic": sum(1 for row in rows if row.parse_state == "dynamic"),
        "source_snapshot_origin": origin,
        "source_snapshot_label": label,
        "source_gaps": gaps,
    }


# --------------------------------------------------------------------------
# Read / review API
# --------------------------------------------------------------------------


def summary(db, user, version_id) -> AskDataCodeRuleSummary:
    version, connection = _version(db, user, version_id)
    snapshot = (
        db.get(SourceSnapshot, version.source_snapshot_id) if version.source_snapshot_id else None
    )
    counts = repository.counts(db, version.id)
    context = version.context or {}
    return AskDataCodeRuleSummary(
        source_snapshot_id=version.source_snapshot_id,
        git_commit=snapshot.git_commit if snapshot else None,
        svn_revision=snapshot.svn_revision if snapshot else None,
        content_hash=(snapshot.content_hash or "") if snapshot else "",
        source_origin=str(context.get("source_origin") or ("manual" if snapshot else "none")),
        source_label=str(context.get("source_label") or ""),
        application_id=connection.application_id,
        environment=connection.environment or "",
        gaps=dict(context.get("source_gaps") or {}),
        **counts,
    )


def listing(
    db,
    user,
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
) -> AskDataCodeRulePage:
    version, _ = _version(db, user, version_id)
    rows, counts = repository.rules(
        db,
        version.id,
        language=language,
        kind=kind,
        parse_state=parse_state,
        review_required=review_required,
        conflict_state=conflict_state,
        include_superseded=include_superseded,
        offset=offset,
        limit=limit,
    )
    names = repository.actor_names(db, {row.edited_by for row in rows if row.edited_by})
    return AskDataCodeRulePage(
        items=[_serialize(row, names) for row in rows],
        total=counts["rule_count"],
        pending_review=counts["pending_review"],
        conflicts=counts["conflicts"],
    )


def detail(db, user, version_id, rule_id) -> AskDataCodeRuleDetail:
    version, _ = _version(db, user, version_id)
    row = repository.rule(db, version.id, rule_id)
    if row is None:
        raise HTTPException(404, "代码规则不存在")
    payload = _serialize(
        row, repository.actor_names(db, {row.edited_by} if row.edited_by else set())
    )
    return AskDataCodeRuleDetail(
        **payload.model_dump(), snippet=str((row.detail or {}).get("snippet", ""))
    )


def _managed_rule(db, user, version_id, rule_id):
    version, connection = _version(db, user, version_id, "askdata:manage")
    row = repository.rule(db, version.id, rule_id)
    if row is None:
        raise HTTPException(404, "代码规则不存在")
    return row, connection


def revise(db, user, version_id, rule_id, body) -> AskDataCodeRuleResponse:
    row, connection = _managed_rule(db, user, version_id, rule_id)
    if row.superseded:
        raise HTTPException(409, "源码已移除该规则，请确认复核")
    detail = dict(row.detail or {})
    detail.setdefault("code_expression", row.expression)
    if body.expression is not None:
        row.expression = body.expression
    if body.description is not None:
        detail["description"] = body.description
    row.detail = detail
    if body.certified is not None:
        row.certified = body.certified
    row.source = "manual"
    row.edited_by = user.id
    row.review_required = False
    row.review_note = ""
    if row.conflict_state == "pending":
        row.conflict_state = "resolved"
    service_databases.repository.audit(db, connection, user, "askdata_code_rule_revised")
    db.commit()
    db.refresh(row)
    return _serialize(row, repository.actor_names(db, {user.id}))


def resolve(db, user, version_id, rule_id) -> AskDataCodeRuleResponse:
    row, connection = _managed_rule(db, user, version_id, rule_id)
    row.review_required = False
    row.review_note = ""
    if row.conflict_state == "pending":
        row.conflict_state = "resolved"
    if row.superseded:
        from datetime import UTC, datetime

        row.deleted_at = datetime.now(UTC)
    service_databases.repository.audit(db, connection, user, "askdata_code_rule_resolved")
    db.commit()
    db.refresh(row)
    return _serialize(row, repository.actor_names(db, {row.edited_by} if row.edited_by else set()))


def resolve_conflict(db, user, version_id, rule_id, body) -> AskDataCodeRuleResponse:
    row, connection = _managed_rule(db, user, version_id, rule_id)
    if row.superseded:
        raise HTTPException(409, "源码已移除该规则，请确认复核")
    if row.conflict_state != "pending":
        raise HTTPException(409, "该规则没有待处理冲突")
    if body.action == "accept_code":
        code_expression = str((row.detail or {}).get("code_expression", ""))
        if not code_expression:
            raise HTTPException(409, "没有可用的代码口径")
        row.expression = code_expression
        row.source = "auto"
        row.certified = False
    row.conflict_state = "resolved"
    row.review_required = False
    row.review_note = ""
    row.edited_by = user.id
    service_databases.repository.audit(db, connection, user, "askdata_code_rule_conflict_resolved")
    db.commit()
    db.refresh(row)
    return _serialize(row, repository.actor_names(db, {user.id}))


# --------------------------------------------------------------------------
# Inputs for the upstream SQL pipeline and for one ask-data run.
# --------------------------------------------------------------------------


def source_queries(connection_id, version_id, allowed, dialect="postgres"):
    """Authorized static SQL fragments for ``PlatformDatabase.get_queries``."""
    del connection_id  # version_id already scopes rows to one connection
    with SessionLocal() as db:
        rows = repository.static_fragments(db, version_id)
        statements = [row.sql_text for row in rows]
    result, seen = [], set()
    for statement in statements:
        if not statement or statement in seen:
            continue
        if not authorized_fragment(statement, allowed, dialect):
            continue
        seen.add(statement)
        result.append(statement)
    return result


def context_payload(version_id, allowed, dialect="postgres", *, limit=24):
    """Code rules as domain rules, glossary and reference SQL for the prompt."""
    with SessionLocal() as db:
        rows = repository.context_rules(db, version_id, limit=800)
    rows = _authorized_rules(rows, allowed, dialect)
    priority = {
        "status_filter": 0,
        "soft_delete": 0,
        "amount_unit": 1,
        "time_field": 1,
        "metric": 2,
        "filter": 2,
        "sql_fragment": 3,
        "join": 4,
    }
    rows.sort(
        key=lambda row: (
            not row.certified,
            priority.get(row.rule_kind, 5),
            row.path,
            row.start_line,
        )
    )
    domain, glossary, examples, lines = [], [], [], []
    for row in rows:
        description = str((row.detail or {}).get("description") or "")
        provenance = f"{row.path}:{row.start_line}"
        state = "已认证口径" if row.certified else "未认证参考"
        text = f"{row.expression}（{state}，{row.rule_kind}，代码出处 {provenance}）"
        if description:
            text = f"{description}；{text}"
        if row.certified and len(domain) < limit:
            domain.append({"name": row.object_key or row.rule_kind, "description": text})
        if len(glossary) < limit:
            glossary.append({"name": row.object_key or row.rule_kind, "description": text})
        if len(lines) < limit:
            lines.append(f"- [{row.rule_kind}] {text}")
        if (
            row.sql_text
            and row.parse_state == "static"
            and len(examples) < 2
            and authorized_fragment(row.sql_text, allowed, dialect)
        ):
            examples.append(
                {"question": row.expression[:200], "sql": row.sql_text, "evidence": provenance}
            )
    return {
        "domain_rules": domain,
        "glossary": glossary,
        "sql_examples": examples,
        "evidence": "\n".join(["代码业务规则（来自源码快照，仅作口径依据）：", *lines])
        if lines
        else "",
    }


def _authorized_rules(rows, allowed, dialect):
    """Only expose rules whose physical scope is covered by the connection grant.

    Global enums/units need a fully granted file scope. Ambiguous or dynamic
    references remain visible in the management UI, outside query prompts.
    """
    scopes, columns, entities = {}, {}, {}
    for row in rows:
        scope = (row.language, row.path)
        if row.rule_kind == "table_mapping":
            scopes.setdefault(scope, set()).add(row.object_key)
            entity = (row.detail or {}).get("entity")
            if entity:
                entities[(scope, entity)] = row.object_key
        if row.rule_kind == "column_mapping" and "." in row.object_key:
            table, column = row.object_key.rsplit(".", 1)
            columns.setdefault(scope, []).append((table, column))

    def permitted(table, column=None):
        key = resolve_table(table, allowed)
        return key is not None and (
            column is None or column in {item["name"] for item in allowed[key].get("columns", [])}
        )

    result = []
    for row in rows:
        if row.review_required or row.superseded or row.conflict_state == "pending":
            continue
        if row.parse_state != "static":
            continue
        detail = row.detail or {}
        sql = row.sql_text or detail.get("source_sql")
        if sql:
            if authorized_fragment(sql, allowed, dialect):
                result.append(row)
            continue
        if row.rule_kind == "sql_fragment":
            continue
        scope = (row.language, row.path)
        if row.rule_kind == "table_mapping":
            valid = permitted(row.object_key)
        elif row.rule_kind in ("amount_unit", "enum_value"):
            tables = scopes.get(scope, set())
            valid = bool(tables) and all(permitted(table) for table in tables)
            valid = valid and all(permitted(*pair) for pair in columns.get(scope, []))
        else:
            references = [row.object_key, *detail.get("columns", [])]
            if detail.get("reference"):
                references.append(detail["reference"])
            valid = True
            for reference in references:
                if "." in reference:
                    table, column = reference.rsplit(".", 1)
                    table = entities.get((scope, table), table)
                    valid = valid and permitted(table, column)
                else:
                    tables = scopes.get(scope, set())
                    matches = [table for table in tables if permitted(table, reference)]
                    valid = valid and len(matches) == 1
        if valid:
            result.append(row)
    return result


def evidence_rules(db, version_id, sql, *, limit=10):
    """Rules actually relevant to the executed SQL, with code provenance."""
    version = lifecycle.model_version(db, version_id)
    connection = lifecycle.connection(db, version.connection_id) if version else None
    if connection is None:
        return []
    schema = connection.schema_cache.get("tables", {})
    allowed = {
        key: {"columns": [col for col in table["columns"] if col["name"] in grants]}
        for key, grants in connection.allowed_tables.items()
        if (table := schema.get(key)) is not None
    }
    dialect = "postgres" if connection.engine == "postgresql" else "mysql"
    rows = _authorized_rules(repository.context_rules(db, version_id, limit=800), allowed, dialect)
    lowered = (sql or "").casefold()
    matched = []
    for row in rows:
        key = (row.object_key or "").casefold()
        columns = [str(item).casefold() for item in (row.detail or {}).get("tables", [])]
        if (key and key in lowered) or any(name and name in lowered for name in columns):
            matched.append(row)
        if len(matched) >= limit:
            break
    return [
        {
            "kind": "code_rule",
            "name": row.object_key or row.rule_kind,
            "description": str((row.detail or {}).get("description") or row.expression)[:200],
            "certified": bool(row.certified and not row.review_required),
            "path": row.path,
            "line": row.start_line,
            "commit": (row.git_commit or row.svn_revision or "")[:40],
            "snapshot_id": str(row.source_snapshot_id) if row.source_snapshot_id else None,
            "language": row.language,
            "parse_state": row.parse_state,
        }
        for row in matched
    ]


def apply_snapshot_context(db, user, connection, body):
    """Resolve the version's snapshot and its provenance label."""
    if body.source_snapshot_id is not None:
        from app.services import askdata_versions

        askdata_versions._snapshot(db, user, connection, body.source_snapshot_id)
        return body.source_snapshot_id, "manual", "手动选择的参考版本"
    identifier, origin, label = recommend_snapshot(db, user, connection)
    return identifier, origin, label
