from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Response

import app.repositories.llm.management as _repository
from app.core.config import get_settings
from app.core.secrets import cipher
from app.core.service_auth import service_authorization
from app.integrations.prometheus import prometheus
from app.models import LLMEndpoint
from app.repositories.agent_config import probe_credential
from app.schemas.llm import EndpointResponse
from app.services.llm.status import endpoint_status
from app.services.monitoring.metrics import llm_metric_expression
from app.services.pagination import page
from app.services.records import active_record, save


def endpoint_response(endpoint):
    return EndpointResponse.model_validate(
        {
            **{
                field: getattr(endpoint, field)
                for field in EndpointResponse.model_fields
                if field != "status"
            },
            "status": endpoint_status(endpoint),
        }
    )


def endpoints(response, db, user, q=None, offset=0, limit=50):
    return [
        endpoint_response(e)
        for e in page(
            db,
            _repository.endpoints_query(q),
            response,
            offset,
            limit,
        )
    ]


def credential_values(db, body, endpoint=None):
    if body.api_key is not None:
        return None, cipher().encrypt(body.api_key.get_secret_value().encode()).decode()
    channel_id = body.agent_channel_id
    if endpoint is not None and "agent_channel_id" not in body.model_fields_set:
        channel_id = endpoint.agent_channel_id
    if channel_id is not None:
        if not probe_credential(db, channel_id, body.base_url):
            raise HTTPException(422, "请选择已启用且 API 地址一致的 Agent 模型通道")
        return channel_id, None
    if endpoint is not None and endpoint.encrypted_api_key is not None:
        return None, endpoint.encrypted_api_key
    raise HTTPException(422, "请填写 API Key")


def create_endpoint(body, request, db, user):
    values = body.model_dump(exclude={"api_key", "agent_channel_id"})
    channel_id, encrypted_key = credential_values(db, body)
    endpoint = save(
        db,
        LLMEndpoint(
            **values,
            agent_channel_id=channel_id,
            encrypted_api_key=encrypted_key,
        ),
    )
    if endpoint.probe_enabled:
        request.app.state.llm_probes.initialize(endpoint.id)
    return endpoint_response(endpoint)


def endpoint_detail(endpoint_id, db, user):
    return endpoint_response(active_record(db, LLMEndpoint, endpoint_id))


def endpoint_status_summary(endpoint_id, db, user):
    endpoint = active_record(db, LLMEndpoint, endpoint_id)
    end = datetime.now(UTC)
    start = end - timedelta(minutes=30)
    counts = _repository.status_counts(db, endpoint, start, end)
    by_bucket = {int(row.bucket): row for row in counts}
    buckets = []
    for index in range(60):
        row = by_bucket.get(index)
        buckets.append(
            {
                "start": start + timedelta(seconds=index * 30),
                "end": start + timedelta(seconds=(index + 1) * 30),
                "request_count": row.requests if row else 0,
                "success_count": row.successes if row else 0,
            }
        )
    requests = sum(bucket["request_count"] for bucket in buckets)
    successes = sum(bucket["success_count"] for bucket in buckets)
    return {
        "endpoint_id": endpoint.id,
        "status": endpoint_status(endpoint),
        "start": start,
        "end": end,
        "updated_at": end,
        "last_request_at": max((row.last_request_at for row in counts), default=None),
        "request_count": requests,
        "success_count": successes,
        "success_rate": successes / requests if requests else None,
        "buckets": buckets,
    }


def update_endpoint(endpoint_id, body, request, db, user):
    service = request.app.state.llm_probes
    with service.exclusive(endpoint_id):
        endpoint = active_record(db, LLMEndpoint, endpoint_id)
        channel_id, encrypted_key = credential_values(db, body, endpoint)
        values = body.model_dump(exclude={"api_key", "agent_channel_id"})
        changed = (
            any(
                getattr(endpoint, key) != value
                for key, value in values.items()
                if key not in {"name", "probe_interval_seconds", "probe_enabled"}
            )
            or body.api_key is not None
            or endpoint.agent_channel_id != channel_id
        )
        for key, value in values.items():
            setattr(endpoint, key, value)
        endpoint.agent_channel_id = channel_id
        endpoint.encrypted_api_key = encrypted_key
        if changed:
            endpoint.probe_config_changed_at = datetime.now(UTC)
            for field in (
                "last_probe_at",
                "last_probe_success",
                "last_error_type",
                "last_http_status",
                "last_latency_ms",
            ):
                setattr(endpoint, field, None)
        endpoint = save(db, endpoint)
        if changed or not endpoint.probe_enabled:
            service.forget(endpoint_id)
        if endpoint.probe_enabled:
            service.initialize(endpoint_id)
        return endpoint_response(endpoint)


def delete_endpoint(endpoint_id, request, db, user):
    service = request.app.state.llm_probes
    with service.exclusive(endpoint_id):
        endpoint = active_record(db, LLMEndpoint, endpoint_id)
        endpoint.deleted_at = _repository.delete_endpoint_query()
        db.commit()
        service.forget(endpoint_id)


async def probe_endpoint(endpoint_id, request, user):
    from app.services.access_control import resource_guard

    return await request.app.state.llm_probes.run(
        endpoint_id, authorize=resource_guard(user.id, "llm:probe", "llm_endpoint", endpoint_id)
    )


def export_metrics(request, db, authorization=None):
    service_authorization(authorization, get_settings().monitoring_token_file)
    return Response(
        request.app.state.llm_probes.metrics(db), media_type="text/plain; version=0.0.4"
    )


def endpoint_metrics(
    endpoint_id, db, user, metric="llm_success_rate", start=None, end=None, step=30
):
    endpoint = active_record(db, LLMEndpoint, endpoint_id)
    expression = llm_metric_expression(metric, endpoint_id)
    if (start is None) != (end is None):
        raise HTTPException(422, "Provide both start and end")
    if start is not None:
        seconds = (end - start).total_seconds()
        if seconds <= 0 or seconds > 31 * 86400 or seconds / step > 11000:
            raise HTTPException(422, "Invalid range or too many points")
        data = prometheus(
            "/api/v1/query_range",
            {
                "query": expression,
                "start": start.timestamp(),
                "end": end.timestamp(),
                "step": step,
            },
        )
    else:
        data = prometheus(
            "/api/v1/query", {"query": expression, "time": datetime.now(UTC).timestamp()}
        )
    # Prometheus NaN denotes an empty denominator, never a healthy 100%.
    for series in data.get("result", []):
        points = series.get("values", [series["value"]] if "value" in series else [])
        for point in points:
            if point[1] in ("NaN", "+Inf", "-Inf"):
                point[1] = None
    return {"metric": metric, "status": endpoint_status(endpoint), **data}
