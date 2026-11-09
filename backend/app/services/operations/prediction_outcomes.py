import math
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException

from app.integrations.prometheus import prometheus
from app.models import Incident, PredictionOutcome, PredictionResult, PredictionTask
from app.repositories import evaluation as repository
from app.repositories.notifications import audit
from app.services.incidents.logic import linked_alerts
from app.services.pagination import page
from app.services.predictions.tasks import expression, snapshot
from app.services.records import active_record
from app.utils.redaction import redact


def assess(result, hours, values):
    start = int(result.data_end_at.timestamp())
    end = start + hours * 3600
    expected = set(range(start + 300, end + 1, 300))
    points = {}
    for at, raw in values:
        value = float(raw)
        if math.isfinite(value) and float(at) == int(float(at)) and int(float(at)) in expected:
            points[int(float(at))] = value
    details = {
        "start": start,
        "end": end,
        "expected_samples": len(expected),
        "observed_samples": len(points),
        "threshold": result.config["threshold"],
        "source_epoch": result.config["source_epoch"],
    }
    forecast = result.horizons.get(str(hours), {})
    if forecast.get("quality") == "insufficient_data" or not forecast.get("points"):
        return "excluded", {**details, "reason": "forecast_unavailable"}
    if set(points) != expected:
        return "incomplete", {**details, "reason": "observation_incomplete"}
    if forecast.get("risk", {}).get("level") == "unknown":
        return "excluded", {**details, "reason": "forecast_not_validated"}
    breach = next((at for at in sorted(points) if points[at] > result.config["threshold"]), None)
    predicted = forecast.get("risk", {}).get("first_breach_at") is not None
    lead = (
        breach - result.finished_at.timestamp()
        if breach and predicted and result.finished_at
        else None
    )
    return "observed", {
        **details,
        "predicted_breach": predicted,
        "actual_first_breach_at": breach,
        "threshold_match": predicted == bool(breach),
        "lead_seconds": lead if lead is not None and lead > 0 else None,
    }


def evaluate_due(db, now=None):
    now = now or datetime.now(UTC)
    for result in repository.completed_predictions(db, now):
        existing = {
            row.horizon_hours for row in repository.values(db, repository.outcomes(result.id))
        }
        for hours in (1, 6, 24):
            if (
                hours in existing
                or not result.data_end_at
                or now < result.data_end_at + timedelta(hours=hours)
            ):
                continue
            start = int(result.data_end_at.timestamp())
            try:
                task = active_record(db, PredictionTask, result.task_id)
                if snapshot(db, task) != result.config:
                    db.add(
                        PredictionOutcome(
                            result_id=result.id,
                            horizon_hours=hours,
                            status="excluded",
                            observation={"reason": "configuration_changed"},
                        )
                    )
                    continue
                data = prometheus(
                    "/api/v1/query_range",
                    {
                        "query": expression(result.config),
                        "start": start + 300,
                        "end": start + hours * 3600,
                        "step": 300,
                    },
                    timeout_seconds=3,
                )
                matches = [
                    s
                    for s in data.get("result", [])
                    if all(
                        s.get("metric", {}).get(k) == v
                        for k, v in result.config["selector"].items()
                    )
                ]
                status, observation = assess(
                    result, hours, matches[0].get("values", []) if len(matches) == 1 else []
                )
                db.expire_all()
                if snapshot(db, task) != result.config:
                    status, observation = "excluded", {"reason": "configuration_changed"}
            except (HTTPException, ValueError, TypeError, KeyError):
                # Unavailable infrastructure is retried; an incomplete finalized window is excluded.
                continue
            db.add(
                PredictionOutcome(
                    result_id=result.id, horizon_hours=hours, status=status, observation=observation
                )
            )
        db.commit()


def listing(db, response, identifier, offset=0, limit=50):
    active_record(db, PredictionResult, identifier)
    return page(db, repository.outcomes(identifier), response, offset, limit)


def link(db, identifier, body, user):
    row = active_record(db, PredictionOutcome, identifier)
    db.refresh(row, with_for_update=True)
    if row.version != body.expected_version:
        raise HTTPException(409, "Outcome version changed")
    result = active_record(db, PredictionResult, row.result_id)
    incident = active_record(db, Incident, body.incident_id)
    config = result.config
    start = result.data_end_at
    end = start + timedelta(hours=row.horizon_hours)
    events = linked_alerts(db, incident.id)
    matches = [
        event
        for event in events
        if start <= event.starts_at <= end
        and (
            str(event.prediction_task_id) == str(result.task_id)
            or (
                config.get("llm_endpoint_id")
                and str(event.llm_endpoint_id) == config["llm_endpoint_id"]
            )
            or (
                config.get("container_id")
                and event.labels.get("container_id") == config["container_id"]
            )
            or (
                not config.get("container_id")
                and config.get("server_id")
                and str(event.server_id) == config["server_id"]
            )
        )
    ]
    if not matches:
        raise HTTPException(422, "Incident has no matching resource observation in this horizon")
    if not any(item["incident_id"] == str(incident.id) for item in row.incident_links):
        row.incident_links = [
            *row.incident_links,
            {
                "incident_id": str(incident.id),
                "confirmed_by": str(user.id),
                "at": datetime.now(UTC).isoformat(),
                "evidence": redact(body.evidence),
            },
        ]
        row.version += 1
        audit(db, user, "prediction_incident_linked", "prediction_outcome", row.id)
        db.commit()
    return row
