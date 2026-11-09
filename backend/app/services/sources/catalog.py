import difflib
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from uuid import uuid4

from fastapi import HTTPException

import app.repositories.sources.catalog as _repository
from app.core.secrets import cipher
from app.models import (
    Application,
    ApplicationDeployment,
    ApplicationRelease,
    Container,
    LLMEndpoint,
    SourceSnapshot,
)
from app.repositories.users import active_by_username
from app.schemas.sources import (
    ApplicationResponse,
    DeploymentResponse,
    SyncInput,
    relative_path,
)
from app.services.pagination import page
from app.services.records import active_record, save
from app.services.sources.store import SourceUnavailable, read_snapshot_file
from app.utils.redaction import redact


def audit(db, application_id, user, action, **detail):
    _repository.audit_query(action, application_id, db, detail, user)


def application_response(row):
    result = ApplicationResponse.model_validate(row)
    result.has_git_token = bool(row.encrypted_git_token)
    result.has_svn_password = bool(row.encrypted_svn_password)
    return result


def applications(response, db, user, offset=0, limit=50, q=""):
    records = page(
        db,
        _repository.applications_query(q),
        response,
        offset,
        limit,
    )
    return [application_response(r) for r in records]


def service_owner(db, owner, previous=None):
    owner = owner.strip()
    if owner and owner != previous and active_by_username(db, owner) is None:
        raise HTTPException(422, "请选择系统中已启用的用户作为负责人")
    return owner


def set_application(row, body, db):
    old_url, old_username = row.git_url, row.git_username
    old_svn_url, old_svn_username = row.svn_url, row.svn_username
    values = body.model_dump(
        exclude={"git_token", "clear_git_token", "svn_password", "clear_svn_password"}
    )
    if "owner" in values:
        values["owner"] = service_owner(db, values["owner"], row.owner)
    for key, value in values.items():
        setattr(row, key, value)
    if (old_url, old_username) != (row.git_url, row.git_username) and not body.git_token:
        row.encrypted_git_token = None
    if body.clear_git_token:
        row.encrypted_git_token = None
    if body.git_token:
        if not body.git_url or not body.git_username:
            raise HTTPException(422, "Git token requires HTTPS URL and username")
        row.encrypted_git_token = (
            cipher().encrypt(body.git_token.get_secret_value().encode()).decode()
        )
    if (old_svn_url, old_svn_username) != (row.svn_url, row.svn_username) and not body.svn_password:
        row.encrypted_svn_password = None
    if body.clear_svn_password:
        row.encrypted_svn_password = None
    if body.svn_password:
        if not body.svn_url or not body.svn_username:
            raise HTTPException(422, "SVN password requires HTTPS URL and username")
        row.encrypted_svn_password = (
            cipher().encrypt(body.svn_password.get_secret_value().encode()).decode()
        )


def create_application(body, db, user):
    row = Application(id=uuid4())
    set_application(row, body, db)
    _repository.create_application_query(db, row)
    audit(db, row.id, user, "application_created")
    return application_response(save(db, row))


def application(application_id, db, user):
    return application_response(active_record(db, Application, application_id))


def update_application(application_id, body, db, user):
    row = active_record(db, Application, application_id)
    _repository.update_application_query(db, row)
    set_application(row, body, db)
    audit(db, row.id, user, "application_updated")
    return application_response(save(db, row))


def delete_application(application_id, db, user):
    row = active_record(db, Application, application_id)
    _repository.delete_application_query(db, row)
    if _repository.delete_application_query_2(db, row):
        raise HTTPException(409, "End active deployments before retiring the application")
    row.deleted_at, row.enabled, row.encrypted_git_token = datetime.now(UTC), False, None
    row.encrypted_svn_password = None
    audit(db, row.id, user, "application_retired")
    db.commit()


def queue_snapshot(db, app, body, user):
    _repository.queue_snapshot_query(app, db)
    if not app.enabled or app.deleted_at:
        raise HTTPException(409, "Application source connection is disabled")
    if body.expected_revision and not app.svn_url:
        raise HTTPException(422, "Expected SVN revision requires an SVN connection")
    if body.expected_commit and app.svn_url:
        raise HTTPException(422, "Expected Git commit cannot be used with an SVN connection")
    request = {
        "source": body.source,
        "expected_commit": body.expected_commit,
        "git_url": app.git_url,
        "git_ref": app.git_ref,
        "svn_url": app.svn_url,
        "svn_revision": app.svn_revision,
        "svn_username": app.svn_username,
        "expected_revision": body.expected_revision,
        "local_path": app.local_path,
        "source_subdir": app.source_subdir,
    }
    previous = _repository.queue_snapshot_query_2(app, db)
    for item in previous:
        if item.request == request:
            return item
    row = SourceSnapshot(
        application_id=app.id,
        requested_by=user.id,
        request=request,
    )
    _repository.queue_snapshot_query_3(db, row)
    audit(db, app.id, user, "source_sync_queued", snapshot_id=str(row.id))
    return row


def synchronize_source(application_id, body, db, user):
    app = active_record(db, Application, application_id)
    return save(db, queue_snapshot(db, app, body, user))


def snapshots(application_id, response, db, user, offset=0, limit=50):
    app = active_record(db, Application, application_id)
    shared = [SourceSnapshot.application_id == application_id]
    if app.git_url:
        shared.append(
            (SourceSnapshot.source == "git")
            & (SourceSnapshot.request["git_url"].as_string() == app.git_url)
        )
    if app.local_path:
        shared.append(
            (SourceSnapshot.source == "local")
            & (SourceSnapshot.request["local_path"].as_string() == app.local_path)
        )
    if app.svn_url:
        shared.append(
            (SourceSnapshot.source == "svn")
            & (SourceSnapshot.request["svn_url"].as_string() == app.svn_url)
        )
    return page(
        db,
        _repository.snapshots_query(shared),
        response,
        offset,
        limit,
    )


def snapshot_record(db, identifier):
    row = active_record(db, SourceSnapshot, identifier)
    return row


def snapshot(snapshot_id, db, user):
    return snapshot_record(db, snapshot_id)


def path_value(value):
    try:
        return relative_path(value)
    except ValueError:
        raise HTTPException(422, "Invalid repository-relative path") from None


def source_tree(snapshot_id, db, user, path=".", offset=0, limit=100):
    row, path = snapshot_record(db, snapshot_id), path_value(path)
    files = [
        x
        for x in row.manifest
        if path == "." or x["path"] == path or x["path"].startswith(path + "/")
    ]
    return {
        "availability": "available" if row.status in ("ready", "partial") else "unavailable",
        "reason": None if files else "no_matching_files",
        "total": len(files),
        "files": files[offset : offset + limit],
    }


def file_result(row, path, start_line, end_line):
    if row.status not in ("ready", "partial"):
        return {"availability": "unavailable", "reason": "snapshot_not_ready"}
    try:
        value = read_snapshot_file(row, path)
    except SourceUnavailable as exc:
        return {"availability": "unavailable", "reason": str(exc)}
    lines = value.splitlines()
    if lines and start_line > len(lines):
        return {"availability": "unavailable", "reason": "line_range_outside_file"}
    return {
        "availability": "available",
        "snapshot_id": str(row.id),
        "path": path,
        "git_commit": row.git_commit,
        "svn_revision": row.svn_revision,
        "content_hash": row.content_hash,
        "start_line": start_line,
        "end_line": min(end_line, len(lines)),
        "total_lines": len(lines),
        "content": "\n".join(lines[start_line - 1 : end_line]),
    }


def source_file(snapshot_id, db, user, path, start_line=1, end_line=200):
    if not start_line <= end_line < start_line + 200:
        raise HTTPException(422, "Read at most 200 lines, with end >= start")
    return file_result(snapshot_record(db, snapshot_id), path_value(path), start_line, end_line)


def source_search(snapshot_id, db, user, q, path="."):
    row, path = snapshot_record(db, snapshot_id), path_value(path)
    hits, scanned, issues = [], 0, []
    for item in row.manifest:
        if path != "." and not (item["path"] == path or item["path"].startswith(path + "/")):
            continue
        scanned += item["bytes"]
        if scanned > 10 * 1024 * 1024:
            issues.append("search_byte_limit")
            break
        try:
            value = read_snapshot_file(row, item["path"])
        except SourceUnavailable as exc:
            issues.append(str(exc))
            continue
        for line_number, line in enumerate(value.splitlines(), 1):
            if q.casefold() in line.casefold():
                hits.append({"path": item["path"], "line": line_number, "text": line[:1000]})
                if len(hits) == 50:
                    break
        if len(hits) == 50:
            issues.append("search_result_limit")
            break
    return {
        "availability": "available" if row.status in ("ready", "partial") else "unavailable",
        "matches": hits,
        "data_gaps": sorted(set(issues)),
    }


def source_diff(snapshot_id, other_id, db, user, path=None):
    before, after = snapshot_record(db, snapshot_id), snapshot_record(db, other_id)
    same_source = before.source == after.source and (
        before.source == "git"
        and before.request.get("git_url")
        and before.request.get("git_url") == after.request.get("git_url")
        or before.source == "svn"
        and before.request.get("svn_url")
        and before.request.get("svn_url") == after.request.get("svn_url")
        or before.source == "local"
        and before.request.get("local_path")
        and before.request.get("local_path") == after.request.get("local_path")
    )
    if before.application_id != after.application_id and not same_source:
        raise HTTPException(422, "Compare snapshots belonging to the same application")
    if before.status not in ("ready", "partial") or after.status not in ("ready", "partial"):
        return {"availability": "unavailable", "reason": "snapshot_not_ready"}
    if path is not None:
        path = path_value(path)
        try:
            left = read_snapshot_file(before, path).splitlines()
            right = read_snapshot_file(after, path).splitlines()
        except SourceUnavailable as exc:
            return {"availability": "unavailable", "reason": str(exc)}
        result = list(
            difflib.unified_diff(left, right, fromfile="before/" + path, tofile="after/" + path)
        )
        return {
            "availability": "available",
            "diff": "\n".join(result[:400]),
            "truncated": len(result) > 400,
        }
    left = {x["path"]: x["sha256"] for x in before.manifest}
    right = {x["path"]: x["sha256"] for x in after.manifest}
    changes = [
        {
            "path": p,
            "change": "added" if p not in left else "removed" if p not in right else "modified",
        }
        for p in sorted(left.keys() | right.keys())
        if left.get(p) != right.get(p)
    ]
    return {"availability": "available", "changes": changes[:100], "total": len(changes)}


def resolve_source_path(application_id, runtime_path, db, user):
    app = active_record(db, Application, application_id)
    if ".." in PurePosixPath(runtime_path).parts or not runtime_path.startswith("/"):
        raise HTTPException(422, "Invalid runtime path")
    for mapping in sorted(app.path_mappings, key=lambda x: len(x["runtime_prefix"]), reverse=True):
        prefix = mapping["runtime_prefix"].rstrip("/")
        if runtime_path == prefix or runtime_path.startswith(prefix + "/"):
            path = PurePosixPath(mapping["repository_prefix"]) / runtime_path[len(prefix) :].lstrip(
                "/"
            )
            return {"availability": "available", "path": str(path)}
    return {"availability": "unavailable", "reason": "runtime_path_unmapped"}


def validate_snapshot(db, app_id, snapshot_id, expected_commit, expected_revision=None):
    row = snapshot_record(db, snapshot_id)
    app = active_record(db, Application, app_id)
    shared_git = row.source == "git" and app.git_url and row.request.get("git_url") == app.git_url
    shared_local = (
        row.source == "local" and app.local_path and row.request.get("local_path") == app.local_path
    )
    shared_svn = row.source == "svn" and app.svn_url and row.request.get("svn_url") == app.svn_url
    if (row.application_id != app_id and not (shared_git or shared_local or shared_svn)) or (
        row.status not in ("ready", "partial")
    ):
        raise HTTPException(422, "Choose a ready snapshot of this service or its explicit source")
    if expected_commit and row.git_commit and row.git_commit != expected_commit:
        raise HTTPException(422, "Snapshot commit does not match the release")
    if expected_commit and row.source == "svn":
        raise HTTPException(422, "SVN snapshot cannot be linked to a Git commit")
    if expected_revision and row.svn_revision != expected_revision:
        raise HTTPException(422, "Snapshot SVN revision does not match the release")
    return row


def create_release(application_id, body, db, user):
    app = active_record(db, Application, application_id)
    _repository.create_release_query(app, db)
    if _repository.create_release_query_2(app, body, db):
        raise HTTPException(409, "Release version is already reserved")
    values = redact(body.model_dump())
    if body.snapshot_id:
        selected = validate_snapshot(
            db, app.id, body.snapshot_id, body.expected_commit, body.expected_revision
        )
        if not values["expected_commit"] and selected.git_commit:
            values["expected_commit"] = selected.git_commit
        if not values["expected_revision"] and selected.svn_revision:
            values["expected_revision"] = selected.svn_revision
        if body.build_source_status == "clean_git" and not selected.git_commit:
            raise HTTPException(422, "Snapshot without a Git commit cannot be declared clean Git")
    if body.build_source_status == "clean_git" and not values["expected_commit"]:
        raise HTTPException(422, "Clean Git build evidence requires a fixed commit")
    row = ApplicationRelease(application_id=app.id, **values)
    _repository.create_release_query_3(db, row)
    if not body.snapshot_id and app.enabled and (app.git_url or app.svn_url or app.local_path):
        queue_snapshot(
            db,
            app,
            SyncInput(
                expected_commit=body.expected_commit, expected_revision=body.expected_revision
            ),
            user,
        )
    audit(db, app.id, user, "release_registered", version=body.version)
    return save(db, row)


def releases(application_id, response, db, user, offset=0, limit=50):
    active_record(db, Application, application_id)
    return page(
        db,
        _repository.releases_query(application_id),
        response,
        offset,
        limit,
    )


def attach_snapshot(release_id, snapshot_id, db, user):
    row = active_record(db, ApplicationRelease, release_id)
    _repository.attach_snapshot_query(db, row)
    selected = validate_snapshot(
        db, row.application_id, snapshot_id, row.expected_commit, row.expected_revision
    )
    if row.snapshot_id and row.snapshot_id != snapshot_id:
        raise HTTPException(409, "Release source is frozen; register a new release")
    row.snapshot_id = selected.id
    audit(
        db,
        row.application_id,
        user,
        "release_source_attached",
        release_id=str(row.id),
        snapshot_id=str(snapshot_id),
    )
    return save(db, row)


def deployment_binding(container, release):
    now = datetime.now(UTC)
    result = {
        "status": "declared",
        "observed_image_id": None,
        "observed_at": None,
        "expected_image_id": release.image_id,
        "source_status": "reference",
        "reason": "runtime_identity_unavailable",
    }
    if container is None:
        result["reason"] = "endpoint_identity_unavailable"
        return result
    result.update(
        {
            "docker_id": container.docker_id,
            "container_name": container.name,
            "observed_image_id": container.image_id,
            "observed_at": container.identity_observed_at.isoformat()
            if container.identity_observed_at
            else None,
        }
    )
    if (
        not container.identity_observed_at
        or not -5 <= (now - container.identity_observed_at).total_seconds() <= 60
    ):
        result["reason"] = "runtime_identity_stale"
    elif not release.image_id or not container.image_id:
        result["reason"] = "image_id_missing"
    elif release.image_id != container.image_id:
        result.update(status="mismatch", reason="image_id_mismatch")
    else:
        result.update(status="verified", reason=None)
    return result


def create_deployment(application_id, body, db, user):
    app = active_record(db, Application, application_id)
    if body.started_at > datetime.now(UTC) + timedelta(seconds=5) or (
        body.ended_at and body.ended_at > datetime.now(UTC)
    ):
        raise HTTPException(422, "Register actual deployment times, not future schedules")
    _repository.create_deployment_query(app, db)
    release = active_record(db, ApplicationRelease, body.release_id)
    if release.application_id != app.id:
        raise HTTPException(422, "Release belongs to another application")
    target = active_record(
        db,
        Container if body.container_id else LLMEndpoint,
        body.container_id or body.llm_endpoint_id,
    )
    _repository.create_deployment_query_2(db, target)
    condition = (
        ApplicationDeployment.container_id == target.id
        if body.container_id
        else ApplicationDeployment.llm_endpoint_id == target.id
    )
    overlap = _repository.create_deployment_query_3(body, condition)
    if body.ended_at:
        overlap = _repository.create_deployment_query_4(body, overlap)
    if _repository.create_deployment_query_5(db, overlap):
        raise HTTPException(409, "Target already has an overlapping deployment")
    container = target if body.container_id else None
    values = body.model_dump()
    if (
        container
        and container.identity_observed_at
        and -5 <= (datetime.now(UTC) - container.identity_observed_at).total_seconds() <= 60
    ):
        if container.identity.get("application") not in (None, "", app.code):
            raise HTTPException(422, "Container application label belongs to another service")
        for key in ("compose_project", "compose_service"):
            observed = container.identity.get(key)
            if observed and values[key] and values[key] != observed:
                raise HTTPException(422, "Container Compose identity contradicts the declaration")
            if observed and not values[key]:
                values[key] = observed
    binding = deployment_binding(container, release)
    if body.started_at < datetime.now(UTC) - timedelta(seconds=60) or body.ended_at:
        binding.update(status="declared", reason="historical_binding_not_observed")
    # Service identity and path mappings are frozen with the deployment, including shared images.
    binding.update(
        {
            "service_code": app.code,
            "entrypoint": app.entrypoint,
            "source_subdir": app.source_subdir,
            "path_mappings": app.path_mappings,
            "snapshot_id": str(release.snapshot_id) if release.snapshot_id else None,
            "expected_commit": release.expected_commit,
            **(
                {"expected_revision": release.expected_revision}
                if release.expected_revision
                else {}
            ),
        }
    )
    if release.snapshot_id:
        selected = _repository.create_deployment_query_6(db, release)
        binding.update(
            {
                "git_commit": selected.git_commit,
                **({"svn_revision": selected.svn_revision} if selected.source == "svn" else {}),
                "source_content_hash": selected.content_hash,
                "source_origin": selected.source,
                "source_status": (
                    "fixed_git"
                    if selected.git_commit
                    else "fixed_svn"
                    if selected.svn_revision
                    else "local_reference"
                ),
            }
        )
    row = ApplicationDeployment(
        application_id=app.id,
        server_id=target.server_id,
        binding=binding,
        **values,
    )
    audit(
        db,
        app.id,
        user,
        "deployment_registered",
        release_id=str(release.id),
        target_id=str(target.id),
        binding_status=binding["status"],
    )
    return save(db, row)


def deployments(application_id, response, db, user, offset=0, limit=50):
    active_record(db, Application, application_id)
    return page(
        db,
        _repository.deployments_query(application_id),
        response,
        offset,
        limit,
    )


def end_deployment(deployment_id, body, db, user):
    row = active_record(db, ApplicationDeployment, deployment_id)
    active_record(db, Application, row.application_id)
    _repository.end_deployment_query(db, row)
    if body.ended_at <= row.started_at or body.ended_at > datetime.now(UTC):
        raise HTTPException(422, "End time must be after start and no later than now")
    if row.ended_at and row.ended_at != body.ended_at:
        raise HTTPException(409, "Deployment end is already frozen")
    row.ended_at = body.ended_at
    audit(db, row.application_id, user, "deployment_ended", deployment_id=str(row.id))
    return save(db, row)


def container_deployments(container_id, db, user):
    active_record(db, Container, container_id)
    rows = _repository.container_deployments_query(container_id, db)
    return [
        {
            **DeploymentResponse.model_validate(d).model_dump(),
            "application_name": a.name,
            "service_code": a.code,
            "version": r.version,
            "current_identity": deployment_binding(
                _repository.container_deployments_query_2(container_id, db), r
            ),
        }
        for d, a, r in rows
    ]
