"""Explicit Admin preview/confirmation; immutable archives precede payload release."""

import base64
import gzip
import hashlib
import json
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import HTTPException
from fastapi.responses import FileResponse

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import ArchiveRecord, RetentionPlan
from app.repositories import governance as repository
from app.repositories.notifications import active_user, audit
from app.schemas.sources import SnapshotResponse, relative_path
from app.services.pagination import page
from app.services.records import active_record

DAYS = {"conversation": 90, "source_snapshot": 90, "prediction": 180, "temporary": 7}
MAX_ARCHIVE = 256 * 1024 * 1024


def boundary(root, path):
    root = root.resolve()
    absolute = path.absolute()
    if absolute == root or not absolute.resolve().is_relative_to(root):
        raise ValueError("path_outside_root")
    for part in [absolute, *absolute.parents]:
        if part == root:
            break
        if part.is_symlink() or part.is_junction():
            raise ValueError("symlink_rejected")
    return absolute


def temporary_path(name):
    if not re.fullmatch(r"capture-[A-Za-z0-9_-]{1,100}", name):
        raise ValueError("temporary_name_rejected")
    root = get_settings().source_store_dir / "staging"
    return boundary(root, root / name)


def file_bytes(path):
    if not path.exists():
        return 0
    total = 0
    for child in path.rglob("*"):
        boundary(path, child)
        if child.is_file():
            total += child.stat().st_size
    return total


def temporary_candidates(db, now, limit):
    root = get_settings().source_store_dir / "staging"
    if not root.is_dir():
        return []
    active = repository.source_running(db)
    rows = []
    for child in sorted(root.iterdir()):
        if len(rows) >= limit:
            break
        try:
            path = temporary_path(child.name)
            stamp = path.stat().st_mtime
            if not path.is_dir() or stamp > (now - timedelta(days=7)).timestamp():
                continue
            size = file_bytes(path)
            rows.append(
                {
                    "kind": "temporary",
                    "id": str(uuid5(NAMESPACE_URL, child.name + ":" + str(stamp))),
                    "name": child.name,
                    "stamp": stamp,
                    "bytes": size,
                    "protected_reasons": ["active_source_capture"] if active else [],
                }
            )
        except (ValueError, OSError):
            continue
    return rows


def preview(db, body, user):
    now = datetime.now(UTC)
    candidates = []
    for kind in dict.fromkeys(body.kinds):
        remaining = body.limit - len(candidates)
        if remaining <= 0:
            break
        if kind == "temporary":
            candidates.extend(temporary_candidates(db, now, remaining))
            continue
        for row in repository.candidates(db, kind, now - timedelta(days=DAYS[kind]), remaining):
            reasons = repository.reference_reasons(db, kind, row, now)
            size = repository.bytes_for(db, kind, row)
            if size > MAX_ARCHIVE:
                reasons.append("archive_size_limit")
            candidates.append(
                {
                    "kind": kind,
                    "id": str(row.id),
                    "bytes": size,
                    "protected_reasons": reasons,
                    "updated_at": row.updated_at.isoformat(),
                }
            )
    row = RetentionPlan(created_by=user.id, candidates=candidates)
    db.add(row)
    db.flush()
    audit(
        db,
        user,
        "cleanup_previewed",
        "retention_plan",
        row.id,
        {"candidates": len(candidates), "retention_days": DAYS},
    )
    db.commit()
    return row


def confirm(db, identifier, body, user):
    row = active_record(db, RetentionPlan, identifier)
    db.refresh(row, with_for_update=True)
    if (
        row.confirmed_by == user.id
        and row.status in ("queued", "running", "completed")
        and body.expected_version == row.version - 1
    ):
        return row
    if row.status not in ("preview", "failed") or row.version != body.expected_version:
        raise HTTPException(409, "Preview version changed")
    row.status, row.confirmed_by, row.confirmed_at, row.version = (
        "queued",
        user.id,
        datetime.now(UTC),
        row.version + 1,
    )
    audit(db, user, "cleanup_confirmed", "retention_plan", row.id, {"version": row.version})
    db.commit()
    return row


def listing(db, response, kind, offset=0, limit=50):
    return page(
        db,
        repository.plans() if kind == "plans" else repository.archives(),
        response,
        offset,
        limit,
    )


def serialized(row):
    result = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        result[column.name] = (
            value.isoformat()
            if isinstance(value, datetime)
            else str(value)
            if isinstance(value, UUID)
            else value
        )
    return result


def archive_path(identifier):
    root = get_settings().archive_dir
    return boundary(root, root / (str(UUID(str(identifier))) + ".json.gz"))


def payload(db, kind, row, candidate):
    files = {}
    if kind == "temporary":
        root = temporary_path(candidate["name"])
        resource = {"name": candidate["name"], "stamp": candidate["stamp"]}
        for child in root.rglob("*"):
            boundary(root, child)
            if child.is_file():
                files[child.relative_to(root).as_posix()] = base64.b64encode(
                    child.read_bytes()
                ).decode()
        return {"resource": resource, "files": files}
    if kind == "source_snapshot":
        resource = {
            **SnapshotResponse.model_validate(row).model_dump(mode="json"),
            "manifest": row.manifest,
        }
        root = get_settings().source_store_dir / "snapshots" / str(row.id)
        for item in row.manifest:
            relative = relative_path(item["path"])
            child = boundary(root, root.joinpath(*Path(relative).parts))
            data = child.read_bytes()
            if hashlib.sha256(data).hexdigest() != item["stored_sha256"]:
                raise ValueError("snapshot_hash_changed")
            files[relative] = base64.b64encode(data).decode()
        return {"resource": resource, "files": files}
    resource = serialized(row)
    if kind == "conversation":
        return {
            "resource": resource,
            **{
                name: [serialized(item) for item in values]
                for name, values in repository.conversation_rows(db, row.id).items()
            },
        }
    return {"resource": resource}


def sha256(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def blocked(db, candidate, now):
    kind = candidate["kind"]
    if kind == "temporary":
        path = temporary_path(candidate["name"])
        if not path.exists():
            return None, ["already_absent"]
        if repository.source_running(db):
            return None, ["active_source_capture"]
        if path.stat().st_mtime != candidate["stamp"]:
            return None, ["temporary_changed"]
        return None, []
    row = db.get(repository.MODELS[kind], UUID(candidate["id"]), with_for_update=True)
    if not row:
        return None, ["already_absent"]
    reasons = repository.reference_reasons(db, kind, row, now)
    if row.updated_at > now - timedelta(days=DAYS[kind]):
        reasons.append("recent_activity")
    if row.updated_at.isoformat() != candidate["updated_at"]:
        reasons.append("record_changed")
    return row, reasons


def release_payload(db, kind, row):
    now = datetime.now(UTC)
    repository.release_transaction(db)
    if kind == "conversation":
        for name, rows in repository.conversation_rows(db, row.id).items():
            for child in rows:
                if name == "runs":
                    child.evidence, child.knowledge, child.tool_calls, child.candidate = (
                        [],
                        [],
                        [],
                        {},
                    )
                    child.source_context, child.database_context = {}, {}
                elif name == "messages":
                    child.content = ""
                else:
                    child.data = {}
                child.deleted_at = child.updated_at = now
    elif kind == "prediction":
        row.horizons, row.anomalies, row.backtest, row.detector_state = {}, [], {}, {}
    else:
        row.manifest = []
    row.deleted_at = row.updated_at = now


def authorized(db, plan):
    user = active_user(db, plan.confirmed_by) if plan.confirmed_by else None
    return user if user and user.role.code == "admin" else None


def finish_item(plan):
    if len(plan.result) == len(plan.candidates):
        plan.status, plan.finished_at = "completed", datetime.now(UTC)


def cleanup_once():
    with SessionLocal() as db:
        repository.lock(db)
        plan = repository.claim_plan(db)
        if not plan:
            return False
        user = authorized(db, plan)
        if not user:
            plan.status, plan.error_code, plan.finished_at = (
                "failed",
                "admin_authorization_changed",
                datetime.now(UTC),
            )
            db.commit()
            return True
        done = {(item["kind"], item["id"]) for item in plan.result}
        candidate = next(
            (item for item in plan.candidates if (item["kind"], item["id"]) not in done), None
        )
        if not candidate:
            plan.status, plan.finished_at = "completed", datetime.now(UTC)
            db.commit()
            return True
        plan.status = "running"
        kind = candidate["kind"]
        archive = repository.archive_for(db, kind, UUID(candidate["id"]))
        try:
            if archive and archive.status in ("releasing", "complete"):
                row = None
                reasons = []
            else:
                row, reasons = blocked(db, candidate, datetime.now(UTC))
            if reasons:
                plan.result = [
                    *plan.result,
                    {
                        "kind": kind,
                        "id": candidate["id"],
                        "status": "protected",
                        "reasons": reasons,
                    },
                ]
                finish_item(plan)
                db.commit()
                return True
            if not archive or archive.status == "ready":
                content = payload(db, kind, row, candidate)
                encoded = json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode()
                if len(encoded) > MAX_ARCHIVE:
                    raise ValueError("archive_size_limit")
                if not archive:
                    archive = ArchiveRecord(
                        id=uuid4(),
                        plan_id=plan.id,
                        resource_kind=kind,
                        resource_id=UUID(candidate["id"]),
                        owner_id=row.owner_id if kind == "conversation" else None,
                        metadata_snapshot={
                            "retention_days": DAYS[kind],
                            "updated_at": candidate.get("updated_at"),
                        },
                    )
                    db.add(archive)
                archive_path(archive.id).parent.mkdir(parents=True, exist_ok=True)
                temporary = archive_path(archive.id).with_suffix(".tmp")
                with gzip.open(temporary, "wb") as stream:
                    stream.write(encoded)
                temporary.replace(archive_path(archive.id))
                archive.sha256, archive.archive_bytes = (
                    sha256(archive_path(archive.id)),
                    archive_path(archive.id).stat().st_size,
                )
                archive.database_reusable_bytes = (
                    0 if kind in ("temporary", "source_snapshot") else candidate["bytes"]
                )
                db.commit()
                # A second transaction rechecks references added while producing the archive.
                repository.lock(db)
                db.refresh(plan, with_for_update=True)
                if not authorized(db, plan):
                    raise ValueError("admin_authorization_changed")
                row, reasons = blocked(db, candidate, datetime.now(UTC))
                if reasons:
                    plan.result = [
                        *plan.result,
                        {
                            "kind": kind,
                            "id": candidate["id"],
                            "status": "protected",
                            "reasons": reasons,
                        },
                    ]
                    finish_item(plan)
                    db.commit()
                    return True
                if kind != "temporary":
                    release_payload(db, kind, row)
                archive.status = "releasing"
                db.commit()
            repository.lock(db)
            db.refresh(plan, with_for_update=True)
            if not authorized(db, plan):
                raise ValueError("admin_authorization_changed")
            if sha256(archive_path(archive.id)) != archive.sha256:
                raise ValueError("archive_integrity_error")
            if kind in ("source_snapshot", "temporary"):
                root = get_settings().source_store_dir / (
                    "snapshots" if kind == "source_snapshot" else "staging"
                )
                target = boundary(
                    root,
                    root / (candidate["id"] if kind == "source_snapshot" else candidate["name"]),
                )
                released = file_bytes(target)
                if target.exists():
                    shutil.rmtree(boundary(root, target))
                archive.released_file_bytes = released
            archive.status = "complete"
            plan.result = [
                *plan.result,
                {
                    "kind": kind,
                    "id": candidate["id"],
                    "status": "archived",
                    "archive_id": str(archive.id),
                },
            ]
            finish_item(plan)
            audit(
                db,
                user,
                "cleanup_archived",
                "archive",
                archive.id,
                {
                    "released_file_bytes": archive.released_file_bytes,
                    "database_reusable_bytes": archive.database_reusable_bytes,
                },
            )
            db.commit()
        except (OSError, ValueError) as exc:
            db.rollback()
            plan = db.get(RetentionPlan, plan.id, with_for_update=True)
            plan.status, plan.error_code, plan.finished_at = (
                "failed",
                str(exc) if isinstance(exc, ValueError) else "archive_storage_error",
                datetime.now(UTC),
            )
            db.commit()
    return True


def archive_content(db, identifier, user):
    row = active_record(db, ArchiveRecord, identifier)
    permission = {
        "conversation": "analyses:run",
        "source_snapshot": "sources:read",
        "prediction": "predictions:read",
        "temporary": "users:manage",
    }[row.resource_kind]
    from app.services.access_control import context

    permissions = context(db, user).capabilities
    if permission not in permissions or (
        row.resource_kind == "conversation"
        and row.owner_id != user.id
        and "analysis_conversations:read_all" not in permissions
    ):
        raise HTTPException(403, "Archive access is restricted")
    path = archive_path(row.id)
    try:
        if sha256(path) != row.sha256:
            raise ValueError
    except (OSError, ValueError):
        raise HTTPException(503, "Archive integrity could not be verified") from None
    return FileResponse(
        path, media_type="application/gzip", filename="archive-" + str(row.id) + ".json.gz"
    )


def archive_listing(db, response, user, resource_id=None, offset=0, limit=50):
    return page(db, repository.accessible_archives(user, resource_id), response, offset, limit)


def storage(db):
    directories = {}
    for name, path in (
        ("source_snapshots", get_settings().source_store_dir / "snapshots"),
        ("source_cache", get_settings().source_store_dir / "git-cache"),
        ("temporary", get_settings().source_store_dir / "staging"),
        ("archives", get_settings().archive_dir),
    ):
        try:
            directories[name] = {"bytes": file_bytes(path), "status": "available"}
        except (OSError, ValueError):
            directories[name] = {"bytes": None, "status": "unavailable"}
    return {
        "tables": repository.storage_tables(db),
        "attachment_bytes": repository.attachment_bytes(db),
        "directories": directories,
        "retention_days": DAYS,
        "source_cache_limit_bytes": get_settings().source_max_cache_bytes,
        "source_store_limit_bytes": get_settings().source_max_store_bytes,
    }
