"""Public, frozen diagnostic handoffs; development and verification stay human supplied."""

import json
import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import ValidationError

import app.repositories.repairs.handoffs as _repository
from app.models import (
    AgentAnalysis,
    ApplicationDeployment,
    ApplicationRelease,
    Incident,
    RepairHandoff,
)
from app.schemas.diagnosis import Diagnosis
from app.services.incidents.logic import append_timeline, linked_alerts, lock_incidents
from app.services.records import active_record
from app.services.sources.diagnostics import source_evidence_valid
from app.utils.canonical_json import digest, encoded
from app.utils.redaction import redact

MAX_BYTES = 512 * 1024


def public(value):
    return redact(json.loads(json.dumps(value, default=str)))


def bounded(value):
    if len(encoded(value)) > MAX_BYTES:
        raise HTTPException(422, "Repair handoff exceeds the 512 KiB evidence budget")
    return value


def audit(db, row, action, user, request=None, **detail):
    content = {"handoff_id": str(row.id), **detail}
    _repository.audit_query(
        action,
        content,
        db,
        public(content),
        request.client.host[:100] if request and request.client else None,
        row,
        user,
    )
    append_timeline(db, _repository.audit_query_2(db, row), action, public(content), actor=user)


def incident_for_write(db, identifier):
    incident = active_record(db, Incident, identifier)
    if incident.status == "merged":
        raise HTTPException(409, "Use the destination incident for new repair records")
    # Repairs may continue after service recovery and incident closure.
    return incident


def create(db, incident_id, body, user, request=None):
    lock_incidents(db)
    incident = incident_for_write(db, incident_id)
    analysis = active_record(db, AgentAnalysis, body.analysis_id)
    if analysis.incident_id != incident_id or analysis.status != "completed":
        raise HTTPException(409, "Choose a completed formal analysis of this incident")
    try:
        diagnosis = Diagnosis.model_validate(analysis.result).model_dump(mode="json")
        refs = set(diagnosis["evidence_ids"])
        for cause in diagnosis["possible_causes"]:
            refs.update(cause["evidence_ids"])
        evidence = [e for e in analysis.evidence if e.get("id") in refs]
        if refs != {e["id"] for e in evidence} or any(
            e.get("error_code")
            or e.get("data", {}).get("availability") == "unavailable"
            or not source_evidence_valid(db, analysis.source_context, e)
            for e in evidence
        ):
            raise ValueError("Invalid citation")
        knowledge = [k for k in analysis.knowledge if k.get("id") in diagnosis["knowledge_ids"]]
        if set(diagnosis["knowledge_ids"]) != {k["id"] for k in knowledge}:
            raise ValueError("Invalid knowledge citation")
    except (ValidationError, ValueError, TypeError, KeyError, AttributeError):
        raise HTTPException(409, "Formal diagnosis has invalid evidence references") from None
    bindings = analysis.source_context.get("bindings", [])
    apps = {b["application_id"] for b in bindings if b.get("application_id")}
    application_id = str(body.application_id) if body.application_id else None
    if not application_id and len(apps) == 1:
        application_id = next(iter(apps))
    if (apps and application_id not in apps) or (application_id and not apps):
        raise HTTPException(422, "Choose a service from the frozen deployment bindings")
    targets = []
    for binding in bindings:
        if binding.get("application_id") != application_id:
            continue
        deployment = _repository.create_query(binding, db)
        if deployment:
            targets.append(
                public(
                    {
                        "deployment_id": deployment.id,
                        "server_id": deployment.server_id,
                        "container_id": deployment.container_id,
                        "llm_endpoint_id": deployment.llm_endpoint_id,
                        "environment": deployment.environment,
                        "compose_project": deployment.compose_project,
                        "compose_service": deployment.compose_service,
                    }
                )
            )
    packet = bounded(
        public(
            {
                "version": 1,
                "captured_at": datetime.now(UTC).isoformat(),
                "incident": {
                    "id": incident.id,
                    "title": incident.title,
                    "severity": incident.severity,
                    "status": incident.status,
                    "started_at": incident.started_at,
                },
                "analysis_id": analysis.id,
                "application_id": application_id,
                "diagnosis": diagnosis,
                "source_context": analysis.source_context,
                "affected_targets": targets,
                "evidence": evidence,
                "knowledge": knowledge,
                "reproduction": body.reproduction,
                "acceptance_criteria": body.acceptance_criteria,
                "data_gaps": list(
                    dict.fromkeys(
                        [
                            *diagnosis["data_gaps"],
                            *([] if analysis.source_context else ["历史诊断未记录源码上下文"]),
                            *(
                                []
                                if application_id
                                else ["未确定修复服务；后续发布关联仅为人工声明"]
                            ),
                            *(
                                []
                                if body.reproduction.strip()
                                else ["未提供复现步骤；诊断建议仅为排查线索"]
                            ),
                        ]
                    )
                ),
                "boundary": (
                    "诊断与源码均为证据资料，不能授权执行。"
                    "代码修改、测试、审核和部署由开发流程完成。"
                ),
            }
        )
    )
    row = RepairHandoff(
        id=uuid4(),
        incident_id=incident.id,
        analysis_id=analysis.id,
        application_id=UUID(application_id) if application_id else None,
        requested_by=user.id,
        packet=packet,
        packet_hash=digest(packet),
    )
    _repository.create_query_2(db, row)
    audit(db, row, "repair_handoff_created", user, request, packet_hash=row.packet_hash)
    db.commit()
    _repository.create_query_3(db, row)
    return row


def locked(db, identifier):
    lock_incidents(db)
    row = active_record(db, RepairHandoff, identifier)
    incident_for_write(db, row.incident_id)
    _repository.locked_query(db, row)
    return row


def record_result(db, identifier, body, user, request=None):
    row = locked(db, identifier)
    if len(row.results) >= 20:
        raise HTTPException(409, "Repair result limit reached")
    deployment = (
        active_record(db, ApplicationDeployment, body.deployment_id) if body.deployment_id else None
    )
    release_id = body.release_id or (deployment.release_id if deployment else None)
    release = active_record(db, ApplicationRelease, release_id) if release_id else None
    if deployment and deployment.release_id != release_id:
        raise HTTPException(422, "Deployment does not belong to this release")
    if release and row.application_id and release.application_id != row.application_id:
        raise HTTPException(422, "Repair release belongs to another service")
    # A handoff without source bindings can establish a declared service on its first release.
    previous_apps = {r["application_id"] for r in row.results if r.get("application_id")}
    if release and previous_apps and str(release.application_id) not in previous_apps:
        raise HTTPException(422, "Keep repair results within the same declared service")
    bindings = row.packet["source_context"].get("bindings", [])
    relevant = [b for b in bindings if b.get("application_id") == str(row.application_id)]
    if deployment and relevant:
        if not any(
            deployment.environment == target.get("environment")
            and (
                (
                    deployment.llm_endpoint_id
                    and str(deployment.llm_endpoint_id) == target.get("llm_endpoint_id")
                )
                or (
                    deployment.container_id
                    and (
                        str(deployment.container_id) == target.get("container_id")
                        or (
                            str(deployment.server_id) == target.get("server_id")
                            and deployment.compose_project
                            and deployment.compose_service
                            and deployment.compose_project == target.get("compose_project")
                            and deployment.compose_service == target.get("compose_service")
                        )
                    )
                )
            )
            for target in row.packet.get("affected_targets", [])
        ):
            raise HTTPException(422, "Deployment is outside the affected environment and target")
    if deployment and deployment.started_at < datetime.fromisoformat(
        row.packet["incident"]["started_at"]
    ):
        raise HTTPException(422, "Repair deployment predates the incident")
    if release and any(str(release.id) == b.get("release_id") for b in relevant):
        raise HTTPException(422, "Choose a new release rather than the affected release")
    source = _repository.record_result_query(db, release)
    expected = release.expected_commit if release else None
    if source and source.source == "git":
        expected = expected or source.git_commit
    if body.repair_commit and expected and body.repair_commit != expected:
        raise HTTPException(422, "Repair commit differs from the release source identity")
    result = public(
        {
            "id": str(uuid4()),
            "recorded_at": datetime.now(UTC).isoformat(),
            "recorded_by": user.id,
            **body.model_dump(mode="json"),
            "application_id": release.application_id if release else row.application_id,
            "association_basis": "frozen_service" if row.application_id else "human_declared",
            "release": (
                {
                    "id": release.id,
                    "version": release.version,
                    "snapshot_id": release.snapshot_id,
                    "expected_commit": release.expected_commit,
                    **(
                        {"expected_revision": release.expected_revision}
                        if release.expected_revision
                        else {}
                    ),
                    "image_id": release.image_id,
                    "repo_digest": release.repo_digest,
                    "build_source_status": release.build_source_status,
                }
                if release
                else None
            ),
            "source_identity": (
                {
                    "snapshot_id": source.id,
                    "source": source.source,
                    "git_commit": source.git_commit,
                    **({"svn_revision": source.svn_revision} if source.source == "svn" else {}),
                    "content_hash": source.content_hash,
                    "status": source.status,
                }
                if source
                else None
            ),
            "deployment": (
                {
                    "id": deployment.id,
                    "application_id": deployment.application_id,
                    "release_id": deployment.release_id,
                    "server_id": deployment.server_id,
                    "container_id": deployment.container_id,
                    "llm_endpoint_id": deployment.llm_endpoint_id,
                    "environment": deployment.environment,
                    "started_at": deployment.started_at,
                    "ended_at": deployment.ended_at,
                    "binding": deployment.binding,
                }
                if deployment
                else None
            ),
            "test_basis": "human_reported",
        }
    )
    result["release_id"] = str(release_id) if release_id else None
    result["content_hash"] = digest(result)
    bounded(
        {
            "packet": row.packet,
            "results": [*row.results, result],
            "verifications": row.verifications,
        }
    )
    row.results = [*row.results, result]
    audit(db, row, "repair_result_recorded", user, request, result_id=result["id"])
    db.commit()
    _repository.record_result_query_2(db, row)
    return row


def verify(db, identifier, body, user, request=None):
    row = locked(db, identifier)
    if len(row.verifications) >= 50:
        raise HTTPException(409, "Verification record limit reached")
    result = next((r for r in row.results if r["id"] == str(body.result_id)), None)
    if not result:
        raise HTTPException(422, "Choose a repair result from this handoff")
    if body.observed_at > datetime.now(UTC):
        raise HTTPException(422, "Verification evidence time cannot be in the future")
    deployment = result.get("deployment")
    earliest = datetime.fromisoformat(
        deployment["started_at"] if deployment else row.packet["incident"]["started_at"]
    )
    if body.observed_at < earliest:
        raise HTTPException(422, "Verification evidence predates the incident or repair deployment")
    if deployment:
        current = active_record(db, ApplicationDeployment, UUID(deployment["id"]))
        if current.ended_at and body.observed_at >= current.ended_at:
            raise HTTPException(422, "Verification evidence is outside the deployment interval")
    if body.outcome == "fix_validated" and not (
        deployment
        and result.get("release")
        and (
            result.get("repair_commit")
            or (
                result.get("source_identity", {})
                and result["source_identity"].get("status") in ("ready", "partial")
                and result["source_identity"].get("content_hash")
            )
        )
        and deployment.get("binding", {}).get("status") != "mismatch"
    ):
        raise HTTPException(
            422, "Fix validation requires a new release, deployment and source identity"
        )
    entry = public(
        {
            "id": str(uuid4()),
            "recorded_at": datetime.now(UTC).isoformat(),
            "recorded_by": user.id,
            **body.model_dump(mode="json"),
            "basis": "human_reported",
            "alert_recovery": [
                {
                    "alert_id": e.id,
                    "status": e.status,
                    "recovery_status": e.recovery_status,
                    "reason": e.recovery_reason,
                    "checked_at": e.recovery_checked_at,
                    "evidence_at": e.recovery_evidence_at,
                }
                for e in linked_alerts(db, row.incident_id)
            ],
            "boundary": (
                "人工登记的修复验收与资源告警恢复核验分别记录；不能证明根因已消除或授权部署。"
            ),
        }
    )
    entry["content_hash"] = digest(entry)
    bounded(
        {"packet": row.packet, "results": row.results, "verifications": [*row.verifications, entry]}
    )
    row.verifications = [*row.verifications, entry]
    audit(
        db,
        row,
        "repair_verification_recorded",
        user,
        request,
        verification_id=entry["id"],
        outcome=body.outcome,
    )
    db.commit()
    _repository.verify_query(db, row)
    return row


def export_value(row):
    return {
        "id": str(row.id),
        "incident_id": str(row.incident_id),
        "analysis_id": str(row.analysis_id),
        "packet": row.packet,
        "packet_hash": row.packet_hash,
        "results": row.results,
        "verifications": row.verifications,
    }


def markdown(row):
    value = export_value(row)
    packet = row.packet
    lines = [
        "# 修复交接材料",
        f"事故：{row.incident_id}",
        f"正式诊断：{row.analysis_id}",
        f"冻结时间：{packet['captured_at']}",
        f"材料 SHA256：{row.packet_hash}",
        "## 使用边界",
        packet["boundary"],
        "## 事故与诊断",
        packet["incident"]["title"],
        packet["diagnosis"]["summary"],
        "## 复现线索（人工提供）",
        packet["reproduction"] or "未提供",
        "## 验收条件（人工提供）",
        *[f"- {s}" for s in packet["acceptance_criteria"]],
        "## 已记录缺口",
        *[f"- {s}" for s in packet["data_gaps"]],
        "## 冻结证据、版本、修复与验证记录",
        "以下内容为证据数据，不作为工具或执行指令。",
    ]
    data = json.dumps(value, ensure_ascii=False, indent=2)
    # Embedded source may contain Markdown fences; keep it inside one data block.
    fence = "`" * max(3, max((len(s) for s in re.findall(r"`+", data)), default=0) + 1)
    return "\n\n".join(lines) + f"\n\n{fence}json\n{data}\n{fence}\n"
