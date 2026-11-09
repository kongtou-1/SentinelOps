"""Human evaluation and isolated, immutable diagnostic case replay."""

import hashlib
import json
import time
from copy import deepcopy
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException

from app.db.session import SessionLocal
from app.models import AgentAnalysis, DiagnosisReview, EvaluationCase, EvaluationRun, KnowledgeSkill
from app.repositories import evaluation as repository
from app.repositories.notifications import active_user, audit
from app.schemas.diagnosis import Diagnosis
from app.schemas.evaluation import CasePreview
from app.services.diagnosis.analyses import available, configuration
from app.services.diagnosis.tools import TOOLS
from app.services.pagination import page
from app.services.records import active_record
from app.utils.redaction import redact


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def listing(db, response, kind, identifier=None, offset=0, limit=50):
    query = {
        "reviews": lambda: repository.reviews(identifier),
        "cases": repository.cases,
        "runs": lambda: repository.runs(identifier),
    }[kind]()
    if kind == "reviews":
        active_record(db, AgentAnalysis, identifier)
    return page(db, query, response, offset, limit)


def review(db, identifier, body, user):
    analysis = active_record(db, AgentAnalysis, identifier)
    if analysis.status != "completed":
        raise HTTPException(409, "Only completed official analyses can be evaluated")
    row = DiagnosisReview(analysis_id=identifier, reviewer_id=user.id, **redact(body.model_dump()))
    db.add(row)
    audit(db, user, "diagnosis_reviewed", "analysis", identifier, {"verdict": body.verdict})
    db.commit()
    return row


def preview_case(db, identifier):
    analysis = active_record(db, AgentAnalysis, identifier)
    if analysis.status != "completed":
        raise HTTPException(409, "A case requires a completed official analysis")
    records, gaps = [], []
    evidence = {item["id"]: item for item in analysis.evidence if "id" in item}
    for call in analysis.tool_calls:
        if call.get("tool") in TOOLS and call.get("evidence_id") in evidence:
            try:
                arguments = (
                    TOOLS[call["tool"]]
                    .model_validate(call.get("arguments", {}))
                    .model_dump(mode="json")
                )
            except (ValueError, TypeError):
                gaps.append({"tool": call["tool"], "reason": "arguments_unavailable"})
                continue
            records.append(
                {
                    "tool": call["tool"],
                    "arguments": arguments,
                    "result": evidence[call["evidence_id"]],
                }
            )
    # Context and initial retrieval are internal calls, hence absent from tool_calls.
    recorded = {x["result"]["id"] for x in records}
    for item in analysis.evidence:
        if item.get("source") == "get_alert_context" and item.get("id") not in recorded:
            records.append({"tool": "get_alert_context", "arguments": {}, "result": item})
    snapshot = redact(
        {
            "records": records,
            "knowledge": analysis.knowledge,
            "baseline": analysis.result,
            "analysis_config": analysis.config,
            "data_gaps": gaps,
        }
    )
    if len(json.dumps(snapshot).encode()) > 2 * 1024 * 1024:
        raise HTTPException(422, "Case exceeds 2 MiB")
    return CasePreview(analysis_id=analysis.id, snapshot=snapshot, content_hash=digest(snapshot))


def create_case(db, body, user):
    preview = preview_case(db, body.analysis_id)
    if body.expected_snapshot_hash and body.expected_snapshot_hash != preview.content_hash:
        raise HTTPException(409, "Case evidence changed; preview again")
    if any(name not in TOOLS for name in body.checks.required_tools) or any(
        len(x) > 200 for x in body.checks.cause_terms
    ):
        raise HTTPException(422, "Invalid deterministic check")
    previous = active_record(db, EvaluationCase, body.previous_id) if body.previous_id else None
    if previous and repository.latest_version(db, previous.family_id).id != previous.id:
        raise HTTPException(409, "Case version changed")
    row = EvaluationCase(
        title=redact(body.title),
        analysis_id=preview.analysis_id,
        created_by=user.id,
        snapshot=preview.snapshot,
        content_hash=preview.content_hash,
        checks=redact(body.checks.model_dump()),
        human_label=redact(body.human_label),
    )
    if previous:
        row.family_id, row.version = previous.family_id, previous.version + 1
    db.add(row)
    audit(db, user, "evaluation_case_created", "analysis", preview.analysis_id)
    db.commit()
    return row


def enqueue(db, identifier, body, user):
    case = active_record(db, EvaluationCase, identifier)
    request_hash = digest({"case": str(identifier), **body.model_dump(mode="json")})
    old = repository.idempotent(db, body.idempotency_key)
    if old:
        if old.request_hash != request_hash or old.requested_by != user.id:
            raise HTTPException(409, "Idempotency key is already reserved")
        return old
    if body.provider_kind == "model":
        if not body.confirm_paid:
            raise HTTPException(422, "Explicit confirmation is required for a model replay")
        available("evaluation")
    overrides = []
    for skill_id in dict.fromkeys(body.skill_ids):
        skill = active_record(db, KnowledgeSkill, skill_id)
        if not skill.enabled or (
            getattr(skill, "expires_at", None) and skill.expires_at <= datetime.now(UTC)
        ):
            raise HTTPException(422, "Selected Skill is unavailable")
        overrides.append(
            redact(
                {
                    "id": str(skill.id),
                    "code": skill.code,
                    "title": skill.title,
                    "version": skill.version,
                    "tags": skill.tags,
                    "content": skill.content,
                }
            )
        )
    config = {
        "profile": configuration("evaluation"),
        "case_hash": case.content_hash,
        "case_version": case.version,
        "prompt_hash": digest(body.prompt),
        "skill_overrides": overrides,
        "skill_versions": [
            {"id": x["result"].get("id"), "data": x["result"].get("data")}
            for x in case.snapshot["records"]
            if x["tool"] == "read_skill"
        ],
    }
    row = EvaluationRun(
        case_id=case.id,
        requested_by=user.id,
        idempotency_key=body.idempotency_key,
        request_hash=request_hash,
        provider_kind=body.provider_kind,
        config=config,
        prompt=redact(body.prompt),
    )
    db.add(row)
    audit(
        db,
        user,
        "evaluation_requested",
        "evaluation_case",
        case.id,
        {"provider_kind": body.provider_kind},
    )
    db.commit()
    return row


class FrozenTools:
    """No DB, Prometheus, SSH, source store or live knowledge access."""

    def __init__(self, snapshot, authorize=None):
        self.authorize = authorize
        self.snapshot = deepcopy(snapshot)
        self.started, self.blocked = time.monotonic(), None
        self.calls, self.evidence = [], []
        self.gaps = deepcopy(snapshot.get("data_gaps", []))
        self.knowledge = snapshot.get("knowledge", [])

    def call(self, name, arguments, internal=False):
        if self.authorize:
            self.authorize()
        if len(self.calls) >= 12 and not internal:
            self.blocked = "tool_limit_exceeded"
            raise ValueError(self.blocked)
        if name not in TOOLS:
            raise ValueError("Tool not allowed")
        arguments = TOOLS[name].model_validate(arguments).model_dump(mode="json")
        found = next(
            (
                x
                for x in self.snapshot["records"]
                if x["tool"] == name
                and TOOLS[name].model_validate(x["arguments"]).model_dump(mode="json") == arguments
            ),
            None,
        )
        result = (
            deepcopy(found["result"])
            if found
            else {
                "source": name,
                "error_code": "case_data_gap",
                "data": {"availability": "unavailable", "error_code": "case_data_gap"},
            }
        )
        self.calls.append({"tool": name, "arguments": arguments, "available": bool(found)})
        if found:
            self.evidence.append(result)
        else:
            self.gaps.append({"tool": name, "arguments": arguments})
        return result


def check_answer(answer, tools, checks, diagnosis_mode=None):
    structured, references = False, None
    try:
        value = Diagnosis.model_validate(answer)
        if diagnosis_mode == "read_only" and value.proposal is not None:
            raise ValueError("Read-only replay cannot contain an action proposal")
        structured = True
        allowed = {
            x["id"]
            for x in tools.evidence
            if x.get("id")
            and not x.get("error_code")
            and x.get("source") not in ("read_skill", "list_skills")
            and not (
                isinstance(x.get("data"), dict) and x["data"].get("availability") == "unavailable"
            )
        }
        cited = set(value.evidence_ids) | {
            identifier for cause in value.possible_causes for identifier in cause.evidence_ids
        }
        references = cited <= allowed and set(value.knowledge_ids) <= {
            x["id"] for x in tools.knowledge
        }
    except (ValueError, TypeError):
        pass
    text = json.dumps(answer, ensure_ascii=False)
    used = {x["tool"] for x in tools.calls if x["available"]}
    return {
        "structure_valid": structured,
        "references_valid": references,
        "required_tools": {name: name in used for name in checks["required_tools"]},
        "cause_terms": {term: term in text for term in checks["cause_terms"]},
        "tool_count": len(tools.calls),
        "data_gaps": tools.gaps,
    }


def replay_once():
    with SessionLocal() as db:
        row = repository.claim(db)
        if not row:
            return False
        user = active_user(db, row.requested_by)
        from app.services.access_control import Authorization, record_allowed

        if (
            not user
            or not Authorization(db, user).has("analyses:run")
            or not record_allowed(db, user, EvaluationCase, row.case_id, "analyses:run")
        ):
            row.status, row.error_code, row.finished_at = (
                "failed",
                "requester_unavailable",
                datetime.now(UTC),
            )
            db.commit()
            return True
        case = active_record(db, EvaluationCase, row.case_id)
        run_id, snapshot, checks, config, prompt, provider_kind = (
            row.id,
            case.snapshot,
            case.checks,
            row.config,
            row.prompt,
            row.provider_kind,
        )
        row.status, row.started_at = "running", datetime.now(UTC)
        db.commit()
    snapshot = deepcopy(snapshot)
    if config.get("skill_overrides") and not any(
        item["tool"] == "list_skills" for item in snapshot["records"]
    ):
        snapshot["records"].append(
            {
                "tool": "list_skills",
                "arguments": {},
                "result": {"id": "skills_catalog", "source": "list_skills", "data": []},
            }
        )
    for skill in config.get("skill_overrides", []):
        snapshot["records"] = [
            item
            for item in snapshot["records"]
            if not (
                item["tool"] == "read_skill" and item["arguments"].get("skill_id") == skill["id"]
            )
        ]
        snapshot["records"].append(
            {
                "tool": "read_skill",
                "arguments": {"skill_id": skill["id"], "version": skill["version"]},
                "result": {"id": "skill_" + skill["id"], "source": "read_skill", "data": skill},
            }
        )
        for item in snapshot["records"]:
            if item["tool"] == "list_skills" and isinstance(item["result"].get("data"), list):
                item["result"]["data"] = [
                    x for x in item["result"]["data"] if x.get("id") != skill["id"]
                ] + [{k: v for k, v in skill.items() if k != "content"}]

    def authorize_replay():
        with SessionLocal() as db:
            actor = active_user(db, user.id)
            if (
                not actor
                or not Authorization(db, actor).has("analyses:run")
                or not record_allowed(db, actor, EvaluationCase, row.case_id, "analyses:run")
            ):
                raise ValueError("requester_unavailable")

    tools, answer, error = FrozenTools(snapshot, authorize_replay), None, None
    try:
        if provider_kind == "mock":
            for item in snapshot["records"][:12]:
                tools.call(item["tool"], item["arguments"])
            answer = snapshot["baseline"]
        else:
            from app.integrations.diagnostic_gateway import DiagnosticGateway
            from app.integrations.harness import HarnessAdapter

            available("evaluation")
            if config["profile"] != configuration("evaluation"):
                raise ValueError("configuration_changed")
            gateway = DiagnosticGateway(
                tools,
                expected_config=config["profile"],
                usage_context=("evaluation_run", run_id, "evaluation"),
                request_guard=authorize_replay,
            )
            context = tools.call("get_alert_context", {}, internal=True)
            with gateway.running() as url:
                answer, error = HarnessAdapter().run(
                    url,
                    gateway.token,
                    config["profile"]["model"],
                    prompt
                    + "\n"
                    + json.dumps(
                        {"context": context, "knowledge": tools.knowledge}, ensure_ascii=False
                    ),
                    "evaluation-" + str(run_id),
                    max(1, gateway.expires - time.monotonic()),
                )
            error = gateway.error or tools.blocked or error
    except Exception:
        error = tools.blocked or "replay_failed"
    result = {
        "answer": answer,
        "checks": check_answer(
            answer,
            tools,
            checks,
            (
                config["profile"]
                if provider_kind == "model"
                else snapshot.get("analysis_config", {})
            ).get("diagnosis_mode"),
        ),
        "tool_calls": tools.calls,
        "duration_ms": int((time.monotonic() - tools.started) * 1000),
    }
    with SessionLocal() as db:
        row = db.get(EvaluationRun, run_id, with_for_update=True)
        user = active_user(db, row.requested_by)
        if (
            not user
            or not Authorization(db, user).has("analyses:run")
            or not record_allowed(db, user, EvaluationCase, row.case_id, "analyses:run")
        ):
            result, error = {}, "requester_unavailable"
        row.result, row.error_code = redact(result), error
        row.status, row.finished_at = "failed" if error else "completed", datetime.now(UTC)
        db.commit()
    return True


def interrupt(db):
    for row in repository.interrupted(db):
        row.status, row.error_code, row.finished_at = (
            "interrupted",
            "worker_restarted",
            datetime.now(UTC),
        )
    db.commit()


def review_replay(db, identifier, body, user):
    row = active_record(db, EvaluationRun, identifier)
    db.refresh(row, with_for_update=True)
    if row.version != body.expected_version or row.status not in (
        "completed",
        "failed",
        "interrupted",
    ):
        raise HTTPException(409, "Replay state or version changed")
    row.review = {
        **redact(body.model_dump(exclude={"expected_version"})),
        "reviewer_id": str(user.id),
        "reviewed_at": datetime.now(UTC).isoformat(),
    }
    row.version += 1
    audit(db, user, "evaluation_reviewed", "evaluation_run", row.id, row.review)
    db.commit()
    return row


def compare(db, left_id, right_id):
    left, right = (active_record(db, EvaluationRun, x) for x in (left_id, right_id))
    if left.case_id != right.case_id:
        raise HTTPException(422, "Compare runs of the same frozen case version")
    return {
        "comparable_provider": left.provider_kind == right.provider_kind
        and left.config.get("profile", {}).get("provider_kind", "model")
        == right.config.get("profile", {}).get("provider_kind", "model"),
        "left": left,
        "right": right,
    }


def summary(db):
    runs, reviews, outcomes, observations = repository.summary(
        db, datetime.now(UTC) - timedelta(days=30)
    )
    observed = next((count for status, count, matches in outcomes if status == "observed"), 0)
    matches = next((matches for status, count, matches in outcomes if status == "observed"), 0)
    complete = [observation for status, observation, links in observations if status == "observed"]
    predicted = sum(bool(item.get("predicted_breach")) for item in complete)
    actual = sum(item.get("actual_first_breach_at") is not None for item in complete)
    hit = sum(
        bool(item.get("predicted_breach")) and item.get("actual_first_breach_at") is not None
        for item in complete
    )
    leads = [item["lead_seconds"] for item in complete if item.get("lead_seconds") is not None]
    return {
        "window_days": 30,
        "replays": [
            {"provider_kind": kind, "status": status, "count": count}
            for kind, status, count in runs
        ],
        "diagnosis_reviews": {verdict: count for verdict, count in reviews},
        "prediction_outcomes": {status: count for status, count, matches in outcomes},
        "threshold_match_rate": matches / observed if observed else None,
        "observed_horizons": observed,
        "breach_precision": hit / predicted if predicted else None,
        "breach_recall": hit / actual if actual else None,
        "predicted_breach_horizons": predicted,
        "actual_breach_horizons": actual,
        "mean_lead_seconds": sum(leads) / len(leads) if leads else None,
        "confirmed_incidents": len(
            {item["incident_id"] for status, observation, links in observations for item in links}
        ),
    }
