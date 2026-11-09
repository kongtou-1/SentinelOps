"""Ask-data usage statistics: volume, outcome, latency, failure mix and model use.

The page is administrator-only and returns aggregates. User questions and result
rows never leave their owner's conversation, so nothing here selects from a run's
``result`` column.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from app.repositories import askdata_statistics as repository
from app.schemas.askdata_stage5 import (
    AskDataStatisticCertification,
    AskDataStatisticFailure,
    AskDataStatisticModels,
    AskDataStatisticPoint,
    AskDataStatisticsResponse,
    AskDataStatisticsTotals,
)
from app.services import askdata as settings

MAX_DAYS = 180


def _percentile(values, ratio):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(ratio * (len(ordered) - 1))))
    return round(ordered[index], 3)


def statistics(db, user, connection_id=None, days=30):
    if connection_id:
        settings.authorized_connection(db, user, connection_id, "askdata:manage")
    days = max(1, min(int(days or 30), MAX_DAYS))
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    rows = repository.runs(db, connection_id, start, end)

    durations, failures, daily = [], Counter(), Counter()
    counts = Counter()
    for row in rows:
        status = row.status
        counts[status] += 1
        if status == "failed" and row.error_code:
            failures[row.error_code] += 1
        last = row.last_event_at or row.created_at
        if last and row.created_at:
            durations.append(max(0.0, (last - row.created_at).total_seconds()))
        bucket = row.created_at.astimezone(UTC).date() if row.created_at else None
        if bucket is not None:
            daily[bucket] += 1
            if status == "failed":
                daily[(bucket, "failed")] += 1

    total = len(rows)
    completed = counts["completed"]
    success_rate = round(completed / total, 4) if total else 0.0
    points = []
    for offset in range(days - 1, -1, -1):
        day = (end - timedelta(days=offset)).date()
        points.append(
            AskDataStatisticPoint(
                label=day.isoformat(),
                runs=daily[day],
                failed=daily[(day, "failed")],
            )
        )
    certification_raw = repository.certification(db, connection_id)
    total_definitions = certification_raw["total"]
    usage = repository.model_usage(db, connection_id, start, end)
    return AskDataStatisticsResponse(
        start=start,
        end=end,
        days=days,
        totals=AskDataStatisticsTotals(
            runs=total,
            completed=completed,
            clarification=counts["needs_clarification"],
            failed=counts["failed"],
            cancelled=counts["cancelled"],
            success_rate=success_rate,
            average_seconds=round(sum(durations) / len(durations), 3) if durations else 0.0,
            p50_seconds=_percentile(durations, 0.5),
            p95_seconds=_percentile(durations, 0.95),
        ),
        failures=[
            AskDataStatisticFailure(code=code, count=count)
            for code, count in failures.most_common(10)
        ],
        daily=points,
        models=AskDataStatisticModels(**usage),
        certification=AskDataStatisticCertification(
            certified=certification_raw["certified"],
            draft=max(0, total_definitions - certification_raw["certified"]),
            pending_review=certification_raw["pending_review"],
            coverage=round(certification_raw["certified"] / total_definitions, 4)
            if total_definitions
            else 0.0,
        ),
    )
