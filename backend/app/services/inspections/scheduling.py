"""Small five-field Cron scheduler; the existing operations worker owns dispatch."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


def cron_fields(expression):
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("Cron requires minute hour day month weekday")
    result = []
    for field, (lower, upper) in zip(
        fields, [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)], strict=True
    ):
        values = set()
        for part in field.split(","):
            base, separator, step = part.partition("/")
            stride = int(step) if separator else 1
            if stride < 1 or stride > upper + 1:
                raise ValueError("Invalid Cron step")
            if base == "*":
                start, end = lower, upper
            elif "-" in base:
                start, end = map(int, base.split("-"))
            else:
                start = int(base)
                end = upper if separator else start
            if not lower <= start <= end <= upper:
                raise ValueError("Invalid Cron range")
            values.update(range(start, end + 1, stride))
        result.append(values)
    result[4] = {v % 7 for v in result[4]}
    return result


def next_cron(expression, timezone, now):
    minutes, hours, days, months, weekdays = cron_fields(expression)
    zone = ZoneInfo(timezone)
    local = now.astimezone(zone)
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    fields = expression.split()
    for _ in range(366 * 5):
        dom, dow = day.day in days, (day.weekday() + 1) % 7 in weekdays
        matches_day = dom and dow if fields[2] == "*" or fields[4] == "*" else dom or dow
        if day.month in months and matches_day:
            for hour in sorted(hours):
                for minute in sorted(minutes):
                    candidate = day.replace(hour=hour, minute=minute)
                    utc = candidate.astimezone(UTC)
                    # Skip missing wall-clock times at a DST transition.
                    if utc > now and utc.astimezone(zone).replace(fold=0) == candidate:
                        return utc
        day += timedelta(days=1)
    raise ValueError("Cron has no execution time in the next five years")


def next_run(plan, now=None, initial=False):
    now = now or datetime.now(UTC)
    if plan.schedule_kind in {"manual", "event"}:
        return None
    if plan.schedule_kind == "once":
        return plan.scheduled_at if initial else None
    if plan.schedule_kind == "cron":
        return next_cron(plan.cron_expression, plan.schedule_timezone, now)
    return now if initial else now + timedelta(seconds=plan.interval_seconds)
