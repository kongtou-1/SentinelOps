"""LLM endpoint status without probe execution or scheduling."""

from datetime import UTC, datetime


def endpoint_status(endpoint):
    if not endpoint.probe_enabled:
        return "disabled"
    reference = endpoint.last_probe_at or endpoint.created_at
    if (datetime.now(UTC) - reference).total_seconds() > 2 * endpoint.probe_interval_seconds:
        return "stale"
    if endpoint.last_probe_at is None:
        return "unknown"
    return "healthy" if endpoint.last_probe_success else "failed"
