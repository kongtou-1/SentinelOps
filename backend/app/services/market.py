"""Human-reviewed discovery content; never registered as diagnostic or execution skills."""

from datetime import UTC, datetime
from typing import Literal

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

import app.repositories.market as _repository
from app.models import MarketCategory, MarketEntry, MarketRevision
from app.schemas.market import (
    MarketContent,
)

Kind = Literal["skill", "tool"]


def record(db, model, identifier, lock=False):
    query = _repository.record_query(identifier, model)
    if lock:
        query = _repository.record_query_2(query)
    row = _repository.record_query_3(db, query)
    if row is None:
        raise HTTPException(404, "内容不存在")
    return row


def own(entry, user):
    if entry.owner_id != user.id:
        raise HTTPException(403, "只能修改本人的提交")


def latest(db, entry):
    return _repository.latest_query(db, entry)


def revision_data(revision):
    return {
        "id": revision.id,
        "number": revision.number,
        "edit_version": revision.edit_version,
        "status": revision.status,
        "category_id": revision.category_id,
        "title": revision.title,
        "summary": revision.summary,
        "tags": revision.tags,
        **revision.content,
        "submitted_at": revision.submitted_at,
        "reviewed_at": revision.reviewed_at,
        "reviewer_id": revision.reviewer_id,
        "review_note": revision.review_note,
        "created_at": revision.created_at,
        "updated_at": revision.updated_at,
    }


def entry_data(entry, revision, category, owner):
    return {
        "id": entry.id,
        "kind": entry.kind,
        "owner_id": entry.owner_id,
        "owner_name": owner.username,
        "listed": entry.listed,
        "published_at": entry.published_at,
        "published_revision_id": entry.published_revision_id,
        "category_name": category.name,
        "revision": revision_data(revision),
    }


def audit(db, user, action, entry=None, revision=None, category=None, detail=None):
    _repository.audit_query(action, category, db, detail, entry, revision, user)


def commit(db):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "内容或分类已变化，请刷新后重试") from None


def enabled_category(db, identifier):
    category = record(db, MarketCategory, identifier, lock=True)
    if not category.enabled:
        raise HTTPException(409, "该分类已停用，请选择其他分类")
    return category


def set_content(revision, body):
    data = MarketContent.model_validate(body.model_dump(include=set(MarketContent.model_fields)))
    values = data.model_dump(mode="json")
    revision.category_id = data.category_id
    for name in ("title", "summary", "tags"):
        setattr(revision, name, values.pop(name))
    values.pop("category_id")
    revision.content = values


def checked_revision(db, revision_id, version):
    initial = record(db, MarketRevision, revision_id)
    entry = record(db, MarketEntry, initial.entry_id, lock=True)
    revision = record(db, MarketRevision, revision_id, lock=True)
    if revision.edit_version != version or latest(db, entry).id != revision.id:
        raise HTTPException(409, "版本已变化，请刷新后重试")
    return entry, revision


def categories(db, user, kind=None):
    query = _repository.categories_query()
    if kind:
        query = _repository.categories_query_2(kind, query)
    counts = _repository.categories_query_3(db, query)
    return [
        {
            "id": category.id,
            "name": category.name,
            "description": category.description,
            "sort_order": category.sort_order,
            "enabled": category.enabled,
            "updated_at": category.updated_at,
            "count": counts.get(category.id, 0),
        }
        for category in _repository.categories_query_4(db)
    ]


def create_category(body, db, user):
    category = MarketCategory(**body.model_dump(exclude={"expected_updated_at"}))
    _repository.create_category_query(category, db)
    try:
        _repository.create_category_query_2(db)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "分类名称已存在") from None
    audit(db, user, "category_created", category=category, detail={"name": category.name})
    commit(db)
    return {"id": category.id}


def update_category(category_id, body, db, user):
    category = record(db, MarketCategory, category_id, lock=True)
    if body.expected_updated_at != category.updated_at.isoformat():
        raise HTTPException(409, "分类已变化，请刷新后重试")
    before = {
        name: getattr(category, name) for name in ("name", "description", "enabled", "sort_order")
    }
    for name, value in body.model_dump(exclude={"expected_updated_at"}).items():
        setattr(category, name, value)
    audit(
        db,
        user,
        "category_updated",
        category=category,
        detail={"before": before, "after": body.model_dump(exclude={"expected_updated_at"})},
    )
    commit(db)
    return {"id": category.id}


def delete_category(category_id, db, user):
    category = record(db, MarketCategory, category_id, lock=True)
    if _repository.delete_category_query(category, db):
        raise HTTPException(409, "分类已有内容或历史版本引用，请使用停用")
    category.deleted_at = datetime.now(UTC)
    audit(db, user, "category_deleted", category=category, detail={"name": category.name})
    commit(db)


def entries(
    response,
    db,
    user,
    scope="published",
    kind=None,
    category_id=None,
    status=None,
    q="",
    offset=0,
    limit=24,
):
    if scope == "manage" and user.role.code != "admin":
        raise HTTPException(403, "需要管理员权限")
    current = MarketEntry.published_revision_id
    if scope != "published":
        current = _repository.entries_query()
    query = _repository.entries_query_2(current)
    if scope == "published":
        query = _repository.entries_query_3(query)
    elif scope == "mine":
        query = _repository.entries_query_4(query, user)
    if scope != "published" and status:
        query = _repository.entries_query_5(query, status)
    if kind:
        query = _repository.entries_query_6(kind, query)
    if category_id:
        query = _repository.entries_query_7(category_id, query)
    if q.strip():
        pattern = (
            "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        )
        query = _repository.entries_query_8(pattern, query)
    response.headers["X-Total-Count"] = str(_repository.entries_query_9(db, query))
    order = MarketEntry.published_at if scope == "published" else MarketRevision.updated_at
    return [
        entry_data(*row) for row in _repository.entries_query_10(db, limit, offset, order, query)
    ]


def create_entry(body, db, user):
    enabled_category(db, body.category_id)
    entry = MarketEntry(kind=body.kind, owner_id=user.id)
    _repository.create_entry_query(db, entry)
    revision = MarketRevision(entry_id=entry.id)
    set_content(revision, body)
    _repository.create_entry_query_2(db, revision)
    audit(db, user, "draft_created", entry, revision)
    commit(db)
    return {"id": entry.id, "revision": revision_data(revision)}


def public_entry(entry_id, db, user):
    entry = record(db, MarketEntry, entry_id)
    if not entry.listed or not entry.published_revision_id:
        raise HTTPException(404, "内容尚未发布或已下架")
    revision = record(db, MarketRevision, entry.published_revision_id)
    if revision.status != "approved" or revision.entry_id != entry.id:
        raise HTTPException(404, "内容尚未发布或已下架")
    return entry_data(
        entry,
        revision,
        record(db, MarketCategory, revision.category_id),
        _repository.public_entry_query(db, entry),
    )


def workbench(entry_id, db, user):
    entry = record(db, MarketEntry, entry_id)
    if entry.owner_id != user.id and user.role.code != "admin":
        raise HTTPException(403, "只能查看本人的未发布内容")
    revisions = _repository.workbench_query(db, entry)
    result = entry_data(
        entry,
        revisions[0],
        record(db, MarketCategory, revisions[0].category_id),
        _repository.workbench_query_2(db, entry),
    )
    result["revisions"] = [revision_data(row) for row in revisions]
    result["audit"] = [
        {
            "id": log.id,
            "action": log.action,
            "user_name": actor.username,
            "created_at": log.created_at,
            "detail": log.detail,
        }
        for log, actor in _repository.workbench_query_3(db, entry)
    ]
    return result


def save_draft(entry_id, body, db, user):
    entry = record(db, MarketEntry, entry_id, lock=True)
    own(entry, user)
    current = latest(db, entry)
    if current.id != body.base_revision_id or current.edit_version != body.expected_version:
        raise HTTPException(409, "版本已变化，请刷新后重试")
    if current.status == "pending":
        raise HTTPException(409, "待审核内容请先撤回再修改")
    enabled_category(db, body.category_id)
    if current.status != "draft":
        current = MarketRevision(entry_id=entry.id, number=current.number + 1)
        _repository.save_draft_query(current, db)
    else:
        current.edit_version += 1
    set_content(current, body)
    _repository.save_draft_query_2(db)
    audit(db, user, "draft_saved", entry, current)
    commit(db)
    return {"id": entry.id, "revision": revision_data(current)}


def submit_revision(revision_id, body, db, user):
    entry, revision = checked_revision(db, revision_id, body.expected_version)
    own(entry, user)
    if revision.status != "draft":
        raise HTTPException(409, "只有草稿可以提交审核")
    enabled_category(db, revision.category_id)
    content = revision.content
    if not all(content.get(field, "").strip() for field in ("description", "usage", "target_url")):
        raise HTTPException(422, "请补充详细介绍、使用说明和仓库／下载链接或官网")
    if entry.kind == "tool" and not content.get("reason", "").strip():
        raise HTTPException(422, "请填写工具推荐理由")
    revision.status, revision.submitted_at = "pending", datetime.now(UTC)
    revision.edit_version += 1
    audit(
        db,
        user,
        "submitted",
        entry,
        revision,
        detail={"title": revision.title, "number": revision.number},
    )
    commit(db)
    return revision_data(revision)


def withdraw_revision(revision_id, body, db, user):
    entry, revision = checked_revision(db, revision_id, body.expected_version)
    own(entry, user)
    if revision.status != "pending":
        raise HTTPException(409, "只有待审核内容可以撤回")
    revision.status, revision.submitted_at = "draft", None
    revision.edit_version += 1
    audit(db, user, "withdrawn", entry, revision)
    commit(db)
    return revision_data(revision)


def review_revision(revision_id, body, db, user):
    entry, revision = checked_revision(db, revision_id, body.expected_version)
    if revision.status != "pending":
        raise HTTPException(409, "该版本已不在待审核状态")
    if body.decision == "reject" and not body.note.strip():
        raise HTTPException(422, "退回必须填写原因")
    now = datetime.now(UTC)
    if body.decision == "approve":
        enabled_category(db, revision.category_id)
        entry.published_revision_id, entry.listed, entry.published_at = revision.id, True, now
        revision.status = "approved"
    else:
        revision.status = "rejected"
    revision.reviewer_id, revision.reviewed_at = user.id, now
    revision.review_note = body.note.strip()
    revision.edit_version += 1
    audit(
        db,
        user,
        revision.status,
        entry,
        revision,
        detail={"note": revision.review_note, "number": revision.number},
    )
    commit(db)
    return revision_data(revision)


def unpublish_entry(entry_id, body, db, user):
    entry = record(db, MarketEntry, entry_id, lock=True)
    if not entry.listed:
        raise HTTPException(409, "内容已经下架")
    entry.listed = False
    # Invalidate a concurrent pending review; the author must explicitly resubmit.
    revision = latest(db, entry)
    if revision.status == "pending":
        revision.status, revision.submitted_at = "draft", None
        revision.edit_version += 1
    audit(db, user, "unpublished", entry, detail={"note": body.note})
    commit(db)
    return {"id": entry.id, "listed": False}


def category_audit(db, user):
    return [
        {
            "id": log.id,
            "action": log.action,
            "category_id": log.category_id,
            "user_name": actor.username,
            "created_at": log.created_at,
            "detail": log.detail,
        }
        for log, actor in _repository.category_audit_query(db)
    ]
