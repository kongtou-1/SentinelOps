import json
from datetime import UTC, datetime
from types import SimpleNamespace

import yaml
from cryptography.fernet import InvalidToken
from fastapi import HTTPException

import app.repositories.monitoring.service_monitors as repository
from app.core.secrets import cipher
from app.integrations.service_probes import module_name, probe, request_config
from app.integrations.service_token_auth import TokenSession
from app.models import Application, Container, Server, ServiceMonitor
from app.schemas.monitoring import MonitorResponse
from app.services.llm.status import endpoint_status
from app.services.monitoring.metrics import metric_availability
from app.services.monitoring.service_probes import status
from app.services.records import active_record
from app.services.resources.overview import overview


def response(row):
    try:
        auth_configured = bool(request_config(row).get("token_auth"))
    except (InvalidToken, HTTPException):
        auth_configured = False
    values = {
        name: getattr(row, name)
        for name in MonitorResponse.model_fields
        if name not in {"request_configured", "token_auth_configured", "status"}
    }
    return MonitorResponse(
        **values,
        request_configured=bool(row.encrypted_request),
        token_auth_configured=auth_configured,
        status=status(row),
    )


def list_monitors(db, application_id=None):
    if application_id:
        active_record(db, Application, application_id)
    return [response(row) for row in repository.monitors(db, application_id)]


def encrypted_request(body, row=None):
    config = request_config(row) if row and row.encrypted_request else {}
    for key, value in (("headers", body.headers), ("body", body.request_body)):
        if value is not None:
            config[key] = value
    if body.token_auth is False:
        config.pop("token_auth", None)
    elif body.token_auth is not None:
        config["token_auth"] = body.token_auth.model_dump()
    if body.probe_server_id and any(config.values()):
        raise HTTPException(422, "Clear encrypted request data before choosing a host probe")
    if body.protocol != "http" and any(config.values()):
        raise HTTPException(422, "Clear encrypted request data before changing protocol")
    if body.method != "POST" and config.get("body"):
        raise HTTPException(422, "Clear the POST body before changing method")
    return cipher().encrypt(json.dumps(config).encode()).decode() if any(config.values()) else None


async def test_probe(db, body):
    row = active_record(db, ServiceMonitor, body.monitor_id) if body.monitor_id else None
    if body.probe_server_id:
        raise HTTPException(422, "Draft tests require a platform probe")
    monitor = SimpleNamespace(
        **body.model_dump(exclude={"headers", "request_body", "token_auth", "monitor_id"}),
        encrypted_request=encrypted_request(body, row),
    )
    session = TokenSession(allow_login_fallback=False)
    result = await probe(monitor, token_session=session)
    auth = request_config(monitor).get("token_auth") if result.success else None
    refresh_tested = False
    if result.success and auth and auth["refresh_url"]:
        if not session.refresh_token:
            return {
                "success": False,
                "error_code": "auth_missing_refresh_token",
                "http_status": None,
                "latency_seconds": result.latency_seconds,
            }
        session.expires_at = 0
        first_latency = result.latency_seconds
        result = await probe(monitor, token_session=session)
        if result.latency_seconds is not None:
            result.latency_seconds += first_latency
        refresh_tested = True
    return {
        "success": result.success,
        "error_code": result.error_code,
        "http_status": result.http_status,
        "latency_seconds": result.latency_seconds,
        "refresh_tested": refresh_tested,
    }


def configure(db, application_id, body, runtime, identifier=None):
    application = active_record(db, Application, application_id)
    row = (
        active_record(db, ServiceMonitor, identifier)
        if identifier
        else ServiceMonitor(application_id=application.id)
    )
    if row.application_id != application.id:
        raise HTTPException(422, "Monitor belongs to another service")
    if body.container_id:
        container = active_record(db, Container, body.container_id)
        if container.application_id != application_id:
            raise HTTPException(422, "Target container must belong to this service")
    if body.probe_server_id:
        source = active_record(db, Server, body.probe_server_id)
        if not source.blackbox_exporter_target:
            raise HTTPException(422, "Configure the source server's Blackbox Exporter first")
    encrypted = encrypted_request(body, row)
    for key, value in body.model_dump(
        exclude={"headers", "request_body", "token_auth", "safe_post"}
    ).items():
        setattr(row, key, value)
    row.encrypted_request = encrypted
    row.config_changed_at = datetime.now(UTC)
    for key in (
        "last_probe_at",
        "last_probe_success",
        "last_error_code",
        "last_http_status",
        "last_latency_seconds",
        "last_success_at",
        "last_failure_at",
    ):
        setattr(row, key, None)
    row.last_evidence = {}
    repository.add(db, row)
    db.commit()
    db.refresh(row)
    runtime.forget(row.id)
    return response(row)


def remove(db, identifier, runtime):
    row = active_record(db, ServiceMonitor, identifier)
    row.deleted_at = datetime.now(UTC)
    row.enabled = False
    db.commit()
    runtime.forget(identifier)


def invalidate_source(db, server_id):
    for row in repository.monitors(db):
        if row.probe_server_id == server_id:
            row.config_changed_at = datetime.now(UTC)
            row.last_probe_at = row.last_probe_success = row.last_latency_seconds = None
            row.last_error_code = None
            row.last_success_at = row.last_failure_at = None
            row.last_evidence = {}


def configure_traffic(db, identifier, body):
    application = active_record(db, Application, identifier)
    application.traffic_target = body.traffic_target
    application.traffic_config_changed_at = datetime.now(UTC)
    db.commit()
    return {"traffic_target": application.traffic_target}


def business_status(rows):
    critical = [row for row in rows if row.importance == "critical" and row.enabled]
    if any(status(row) == "failed" for row in critical):
        return "failed"
    if not critical or any(status(row) != "healthy" for row in critical):
        return "unknown"
    return "healthy"


def observability(db, identifier):
    application = active_record(db, Application, identifier)
    monitors = repository.monitors(db, identifier)
    containers = repository.containers(db, identifier)
    observations = []
    effective_containers = []
    for server_id in {c.server_id for c in containers}:
        server = repository.server(db, server_id)
        if server and not server.deleted_at:
            from app.services.resources.business_services import attach_services

            current = attach_services(
                db, overview(server, repository.server_containers(db, server_id))
            )
            items = [c for c in current["containers"] if c.application_id == identifier]
            effective_containers.extend(items)
            samples = {o["container_id"]: o for o in current["observations"]}
            for container in items:
                observations.append(
                    {
                        "id": str(container.id),
                        "container_id": str(container.id),
                        "name": container.name,
                        "server_id": str(server.id),
                        "server_name": server.name,
                        **{
                            k: v
                            for k, v in samples.get(container.id, {}).items()
                            if k != "container_id"
                        },
                    }
                )
    applications = {str(item.id): item for item in repository.applications(db)}

    def service(item):
        return {
            "id": str(item.id),
            "name": item.name,
            "status": business_status(repository.monitors(db, item.id))
            if item.enabled
            else "paused",
        }

    endpoints = repository.endpoints(db, identifier)
    alerts = repository.related_alerts(
        db,
        [r.id for r in monitors],
        [c.id for c in effective_containers],
        [e.id for e in endpoints],
    )
    incidents = repository.incidents(db, [a.id for a in alerts])
    return {
        "application_id": identifier,
        "name": application.name,
        "business_status": business_status(monitors) if application.enabled else "paused",
        "traffic_target": application.traffic_target,
        "monitors": [response(row) for row in monitors],
        "containers": observations,
        "dependencies": [
            service(applications[str(v)]) for v in application.depends_on if str(v) in applications
        ],
        "dependents": [
            service(v) for v in applications.values() if str(identifier) in v.depends_on
        ],
        "llm_endpoints": [
            {"id": str(e.id), "name": e.name, "status": endpoint_status(e)} for e in endpoints
        ],
        "alerts": [
            {
                "id": str(a.id),
                "name": a.annotations.get("summary") or a.labels.get("alertname", "Alert"),
                "severity": a.severity,
                "status": a.status,
            }
            for a in alerts
        ],
        "incidents": [
            {"id": str(i.id), "title": i.title, "status": i.status, "severity": i.severity}
            for i in incidents
        ],
    }


def metric_expression(metric, monitor, application):
    labels = (
        'job="service-probe",monitor_id='
        + json.dumps(str(monitor.id))
        + ",source_epoch="
        + json.dumps(monitor.config_changed_at.isoformat())
    )

    def series(name, extra=""):
        return name + "{" + labels + extra + "}"

    last = series("service_probe_last_run_timestamp_seconds")
    interval = series("service_probe_interval_seconds")
    known = series("service_probe_has_result") + " == 1"
    fresh = f"time() - {last} < 2 * {interval}"
    health = 'up{job="service-probe"}'
    guard = f"and on() ({health} == 1) and on() (time() - timestamp({health}) < 60)"
    if metric.startswith("service_probe_"):
        if metric == "service_probe_stale":
            expression = f"(time() - {last}) > bool (2 * {interval})"
        elif metric == "service_probe_success_rate":
            total = f"sum without(outcome) (rate({series('service_probe_requests_total')}[5m]))"
            successful = series("service_probe_requests_total", ',outcome="success"')
            success = f"sum without(outcome) (rate({successful}[5m]))"
            expression = f"({success}) / ({total}) and ({total} > 0) and ({known}) and ({fresh})"
        else:
            name = (
                "service_probe_success"
                if metric == "service_probe_up"
                else "service_probe_latency_seconds"
            )
            expression = f"{series(name)} and ({known}) and ({fresh})"
        return f"({expression}) {guard}"
    traffic = (
        'job="service-traffic",application_id='
        + json.dumps(str(application.id))
        + ",source_epoch="
        + json.dumps(application.traffic_config_changed_at.isoformat())
    )
    health = "up{" + traffic + "}"
    labels = (
        traffic
        + ',traffic="business",method='
        + json.dumps(monitor.method)
        + ",route="
        + json.dumps(monitor.route)
    )
    requests = "sentinelops_http_requests_total{" + labels + "}"
    total = f"sum(rate({requests}[5m]))"
    if metric == "service_request_rate":
        expression = total
    elif metric == "service_request_count":
        expression = f"sum(increase({requests}[5m]))"
    elif metric == "service_error_rate":
        errors = "sentinelops_http_requests_total{" + labels + ',status_class=~"4xx|5xx"}'
        expression = f"sum(rate({errors}[5m])) / ({total}) and ({total} > 0)"
    else:
        bucket = "sentinelops_http_request_duration_seconds_bucket{" + labels + "}"
        expression = f"histogram_quantile(0.95, sum by(le) (rate({bucket}[5m]))) and ({total} > 0)"
    return f"({expression}) and on() ({health} == 1) and on() (time() - timestamp({health}) < 60)"


def metrics(db, identifier, metric, start=None, end=None, step=30):
    from app.integrations.prometheus import prometheus

    row = active_record(db, ServiceMonitor, identifier)
    application = active_record(db, Application, row.application_id)
    if not row.enabled or not application.enabled:
        return {"result": [], "availability": "paused", "reason": "monitoring_paused"}
    if not metric.startswith("service_probe_") and (
        not application.traffic_target or not row.route
    ):
        return {
            "result": [],
            "availability": "unconfigured",
            "reason": "traffic_metrics_unconfigured",
        }
    expression = metric_expression(metric, row, application)
    if (start is None) != (end is None):
        raise HTTPException(422, "Provide start and end together")
    if start is None:
        return metric_availability(
            prometheus("/api/v1/query", {"query": expression, "timeout": "8s"})
        )
    seconds = (end - start).total_seconds()
    if seconds <= 0 or seconds > 31 * 86400 or seconds / step > 11000:
        raise HTTPException(422, "Invalid history range")
    return metric_availability(
        prometheus(
            "/api/v1/query_range",
            {
                "query": expression,
                "start": start.timestamp(),
                "end": end.timestamp(),
                "step": step,
                "timeout": "8s",
            },
        ),
        start,
        end,
        step,
    )


def blackbox_config(db, server_id):
    active_record(db, Server, server_id)
    modules = {}
    for row in repository.active_monitors(db):
        if row.probe_server_id != server_id:
            continue
        config = {"prober": row.protocol, "timeout": str(row.timeout_seconds) + "s"}
        if row.protocol == "http":
            config["http"] = {
                "headers": {"X-SentinelOps-Probe": "1"},
                "method": row.method,
                "valid_status_codes": [row.expected_status],
                "follow_redirects": False,
                "preferred_ip_protocol": "ip4",
            }
        elif row.protocol == "dns":
            config["dns"] = {
                "query_name": row.target,
                "query_type": "A",
                "valid_rcodes": ["NOERROR"],
                "validate_answer_rrs": {"fail_if_none_matches_regexp": [".+"]},
            }
        elif row.protocol == "icmp":
            config["icmp"] = {"preferred_ip_protocol": "ip4"}
        modules[module_name(row)] = config
    return yaml.safe_dump({"modules": modules}, sort_keys=False)
