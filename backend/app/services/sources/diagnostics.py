"""Per-run source scope, resolved from alert targets and historical deployments only."""

import hashlib
import json
from pathlib import PurePosixPath
from uuid import UUID

import app.repositories.sources.diagnostics as _repository
from app.models import (
    ApplicationDeployment,
)
from app.schemas.source_tools import SourceCompare as SourceCompare
from app.schemas.source_tools import SourceFile as SourceFile
from app.schemas.source_tools import SourceInput as SourceInput
from app.schemas.source_tools import SourceSearch as SourceSearch
from app.schemas.sources import relative_path
from app.services.incidents.logic import linked_alerts
from app.utils.docker import docker_identifier
from app.utils.redaction import redact

SOURCE_TOOLS = {
    "list_source_tree": SourceInput,
    "search_source": SourceSearch,
    "read_source_file": SourceFile,
    "compare_source_snapshots": SourceCompare,
}


def context_hash(context):
    return hashlib.sha256(
        json.dumps(context, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def source_identity(row):
    return {
        "snapshot_id": str(row.id),
        "git_commit": row.git_commit,
        "content_hash": row.content_hash,
        "source": row.source,
        "status": row.status,
        "data_gaps": row.data_gaps,
        **({"svn_revision": row.svn_revision} if row.source == "svn" else {}),
    }


def freeze_source_context(db, incident_id):
    context = {"version": 1, "bindings": [], "snapshots": [], "candidates": [], "data_gaps": []}
    snapshots = {}
    gaps = context["data_gaps"]
    alerts = sorted(linked_alerts(db, incident_id), key=lambda a: (a.starts_at, str(a.id)))
    for alert in alerts[:50]:
        target_id, column = alert.llm_endpoint_id, ApplicationDeployment.llm_endpoint_id
        if not target_id:
            candidate = (
                _repository.monitor_target(db, alert)
                or alert.labels.get("container_id")
                or alert.rule_snapshot.get("prediction_config", {}).get("container_id")
            )
            try:
                target_id = UUID(str(candidate)) if candidate else None
            except ValueError:
                target_id = None
            container = _repository.freeze_source_context_query(db, target_id)
            if not candidate:
                native = alert.labels.get("id") or alert.labels.get("docker_id") or ""
                docker_id = docker_identifier(native)
                if docker_id:
                    matches = _repository.freeze_source_context_query_2(alert, db, docker_id)
                    container = matches[0] if len(matches) == 1 else None
                    target_id = container.id if container else None
            if container is None or container.server_id != alert.server_id:
                target_id = None
            column = ApplicationDeployment.container_id
        if target_id is None:
            gaps.append("source_target_unresolved")
            # Host alerts show candidates but grant no source access to co-located services.
            if alert.server_id:
                candidates = _repository.freeze_source_context_query_3(alert, db)
                context["candidates"].extend(
                    {
                        "application_id": str(d.application_id),
                        "deployment_id": str(d.id),
                        "service_code": d.binding.get("service_code"),
                        "reason": "host_candidate_not_fault_attribution",
                    }
                    for d in candidates
                )
            continue
        deployments = _repository.freeze_source_context_query_4(alert, column, db, target_id)
        if len(deployments) != 1:
            gaps.append("deployment_missing" if not deployments else "deployment_ambiguous")
            continue
        deployment = deployments[0]
        release = _repository.freeze_source_context_query_5(db, deployment)
        binding = {
            "alert_id": str(alert.id),
            "target_id": str(target_id),
            "occurred_at": alert.starts_at.isoformat(),
            "application_id": str(deployment.application_id),
            "deployment_id": str(deployment.id),
            "release_id": str(release.id),
            "release_version": release.version,
            "environment": deployment.environment,
            "config_version": deployment.config_version,
            "identity": deployment.binding,
            "change_summary": release.change_summary,
            "test_result": release.test_result,
            "build_source_status": release.build_source_status,
            "image_id": release.image_id,
            "repo_digest": release.repo_digest,
            "expected_commit": release.expected_commit,
            **(
                {"expected_revision": release.expected_revision}
                if release.expected_revision
                else {}
            ),
            "snapshot_ids": [],
            "data_gaps": [],
        }
        if deployment.binding.get("status") != "verified":
            binding["data_gaps"].append("deployment_identity_unverified")
        if release.build_source_status != "clean_git":
            binding["data_gaps"].append("build_source_unverified")

        def add_snapshot(identifier, role, source_deployment=None, source_release=None):
            source_deployment = source_deployment or deployment
            source_release = source_release or release
            source = _repository.freeze_source_context_add_snapshot_query(db, identifier)
            if not source or source.deleted_at or source.status not in ("ready", "partial"):
                return False
            entry = {
                **source_identity(source),
                "application_id": str(deployment.application_id),
                "deployment_id": str(source_deployment.id),
                "release_id": str(source_release.id),
                "release_version": source_release.version,
                "role": role,
                "path_mappings": source_deployment.binding.get("path_mappings", []),
            }
            snapshots[(str(deployment.id), str(source.id))] = entry
            binding["snapshot_ids"].append(str(source.id))
            binding["data_gaps"].extend(source.data_gaps)
            if source.source == "local":
                binding["data_gaps"].append("local_source_version_unverified")
            return True

        if not add_snapshot(release.snapshot_id, "release"):
            binding["data_gaps"].append("release_source_unavailable")
            reference = _repository.freeze_source_context_query_6(db, deployment)
            if reference and add_snapshot(reference.id, "reference"):
                binding["data_gaps"].append("reference_source_version_unverified")
        previous = _repository.freeze_source_context_query_7(column, db, deployment, target_id)
        if previous:
            prior_release = _repository.freeze_source_context_query_8(db, previous)
            if (
                prior_release.snapshot_id
                and str(prior_release.snapshot_id) not in binding["snapshot_ids"]
            ):
                add_snapshot(prior_release.snapshot_id, "previous_release", previous, prior_release)
        if not binding["snapshot_ids"]:
            binding["data_gaps"].append("source_unavailable_continue_runtime")
        binding["data_gaps"] = sorted(set(binding["data_gaps"]))
        gaps.extend(binding["data_gaps"])
        context["bindings"].append(binding)
    context["snapshots"] = list(snapshots.values())
    context["candidates"] = list({c["deployment_id"]: c for c in context["candidates"]}.values())[
        :20
    ]
    if len(alerts) > 50:
        gaps.append("source_context_limit")
    if not context["bindings"]:
        gaps.append("source_unavailable_continue_runtime")
    context["data_gaps"] = sorted(set(gaps))
    context = redact(context)
    if len(json.dumps(context, ensure_ascii=False).encode()) > 65536:
        return {
            "version": 1,
            "bindings": [],
            "snapshots": [],
            "candidates": [],
            "data_gaps": ["source_context_limit", "source_unavailable_continue_runtime"],
        }
    return context


def source_evidence_valid(db, context, evidence):
    if evidence.get("source") == "get_alert_context":
        recorded = evidence.get("data", {}).get("source_context")
        return recorded is None or context_hash(recorded) == context_hash(context)
    if evidence.get("source") not in SOURCE_TOOLS:
        return True
    data = evidence.get("data", {})
    if data.get("source_context_hash") != context_hash(context):
        return False
    for identity in data.get("source_refs", []):
        if not any(
            all(
                s.get(k) == identity.get(k)
                for k in ("snapshot_id", "git_commit", "content_hash", "svn_revision")
            )
            for s in context.get("snapshots", [])
        ):
            return False
        row = _repository.source_evidence_valid_query(db, identity)
        if (
            not row
            or row.content_hash != identity["content_hash"]
            or row.git_commit != identity["git_commit"]
            or row.svn_revision != identity.get("svn_revision")
        ):
            return False
    return bool(data.get("source_refs"))


def dispatch_source(db, context, name, body):
    # Reuse the bounded, hash-checked first-stage readers; no network or synchronization.
    from app.services.sources.catalog import file_result, source_diff, source_search, source_tree

    identifiers = [body.snapshot_id]
    if name == "compare_source_snapshots":
        identifiers.append(body.other_id)
    selected = []
    for identifier in identifiers:
        matches = [s for s in context.get("snapshots", []) if s["snapshot_id"] == str(identifier)]
        if not matches:
            raise PermissionError("Snapshot is outside the frozen source scope")
        selected.append(matches[0])
    if len(selected) == 2 and selected[0]["application_id"] != selected[1]["application_id"]:
        raise PermissionError("Compare only versions of the same service")
    refs = [
        {k: s[k] for k in ("snapshot_id", "git_commit", "content_hash", "svn_revision") if k in s}
        for s in selected
    ]
    base = {"source_refs": refs, "source_context_hash": context_hash(context)}
    for identity in selected:
        source = _repository.dispatch_source_query(db, identity)
        if not source or source.deleted_at or source.status not in ("ready", "partial"):
            return {**base, "availability": "unavailable", "reason": "snapshot_not_ready"}
        if (
            source.content_hash != identity["content_hash"]
            or source.git_commit != identity["git_commit"]
            or source.svn_revision != identity.get("svn_revision")
        ):
            return {**base, "availability": "unavailable", "reason": "snapshot_identity_changed"}
    if name == "list_source_tree":
        result = source_tree(body.snapshot_id, db, None, path=body.path, limit=100)
    elif name == "search_source":
        result = source_search(body.snapshot_id, db, None, q=body.query, path=body.path)
        # Hash the exact returned (redacted) fragment for reproducible citations.
        for match in result["matches"]:
            match["snippet_hash"] = hashlib.sha256(redact(match["text"]).encode()).hexdigest()
    elif name == "read_source_file":
        path = body.path
        if body.runtime_path:
            mappings = {
                json.dumps(s["path_mappings"], sort_keys=True)
                for s in context["snapshots"]
                if s["snapshot_id"] == str(body.snapshot_id)
            }
            if len(mappings) != 1:
                return {**base, "availability": "unavailable", "reason": "runtime_path_ambiguous"}
            path = None
            for mapping in sorted(
                selected[0]["path_mappings"], key=lambda m: len(m["runtime_prefix"]), reverse=True
            ):
                prefix = mapping["runtime_prefix"].rstrip("/")
                if body.runtime_path == prefix or body.runtime_path.startswith(prefix + "/"):
                    path = relative_path(
                        str(
                            PurePosixPath(mapping["repository_prefix"])
                            / body.runtime_path[len(prefix) :].lstrip("/")
                        )
                    )
                    break
            if path is None:
                return {**base, "availability": "unavailable", "reason": "runtime_path_unmapped"}
        result = file_result(
            _repository.dispatch_source_query_2(body, db), path, body.start_line, body.end_line
        )
        if result.get("availability") == "available":
            result["snippet_hash"] = hashlib.sha256(redact(result["content"]).encode()).hexdigest()
    else:
        result = source_diff(body.snapshot_id, body.other_id, db, None, path=body.path)
        if "diff" in result:
            result["path"] = body.path
            result["snippet_hash"] = hashlib.sha256(redact(result["diff"]).encode()).hexdigest()
    return {**base, **result}


def diagnosis_gaps(context, evidence):
    gaps = list(context.get("data_gaps", [])) if context else ["source_context_unrecorded"]
    for record in evidence:
        if record.get("source") in SOURCE_TOOLS:
            data = record.get("data", {})
            if data.get("reason"):
                gaps.append(data["reason"])
            gaps.extend(data.get("data_gaps", []))
            if record.get("error_code"):
                gaps.append(record["error_code"])
    return list(dict.fromkeys(gaps))
