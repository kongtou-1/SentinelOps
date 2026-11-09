import json
from datetime import UTC, datetime

from fastapi import HTTPException, Response

import app.repositories.resources.crud as _repository
from app.core.config import BACKEND_ROOT, get_settings
from app.core.service_auth import service_authorization
from app.integrations.prometheus import prometheus
from app.integrations.ssh import encrypt_credential, read_container, test_connection
from app.models import Container, Server, SSHCredential
from app.services.monitoring.config import discovery_targets
from app.services.monitoring.summary import container_health, server_summaries
from app.services.pagination import page
from app.services.records import active_record, save
from app.services.sources.identity import identity_inventory
from app.utils.docker import docker_identifier


def credentials(response, db, user, offset=0, limit=50):
    return page(
        db,
        _repository.credentials_query(),
        response,
        offset,
        limit,
    )


def create_credential(body, db, user):
    return save(
        db,
        SSHCredential(
            name=body.name, auth_type=body.auth_type, encrypted_secret=encrypt_credential(body)
        ),
    )


def replace_credential(credential_id, body, db, user):
    credential = active_record(db, SSHCredential, credential_id)
    credential.name = body.name
    credential.auth_type = body.auth_type
    credential.encrypted_secret = encrypt_credential(body)
    return save(db, credential)


def delete_credential(credential_id, db, user):
    credential = active_record(db, SSHCredential, credential_id)
    if _repository.delete_credential_query(credential_id, db):
        raise HTTPException(409, "Credential is assigned to a server")
    credential.deleted_at = _repository.delete_credential_query_2()
    db.commit()


def validate_server(db, body):
    if body.credential_id:
        active_record(db, SSHCredential, body.credential_id)


def servers(response, db, user, q=None, offset=0, limit=50):
    return page(
        db,
        _repository.servers_query(q),
        response,
        offset,
        limit,
    )


def create_server(body, db, user):
    validate_server(db, body)
    return save(db, Server(**body.model_dump()))


def server_detail(server_id, db, user):
    return active_record(db, Server, server_id)


def replace_server(server_id, body, db, user):
    server = active_record(db, Server, server_id)
    validate_server(db, body)
    values = body.model_dump()
    if "docker_state_target" not in body.model_fields_set:
        values.pop("docker_state_target")
    if "blackbox_exporter_target" not in body.model_fields_set:
        values.pop("blackbox_exporter_target")
    if any(
        key in values and getattr(server, key) != values[key]
        for key in ("blackbox_exporter_target", "monitoring_enabled")
    ):
        from app.services.monitoring.service_monitors import invalidate_source

        invalidate_source(db, server.id)
    if any(
        key in values and getattr(server, key) != values[key]
        for key in (
            "node_exporter_target",
            "cadvisor_target",
            "docker_state_target",
            "health_url",
            "monitoring_enabled",
        )
    ):
        server.collection_config_changed_at = datetime.now(UTC)
    for key, value in values.items():
        setattr(server, key, value)
    return save(db, server)


def delete_server(server_id, db, user):
    server = active_record(db, Server, server_id)
    server.deleted_at = _repository.delete_server_query()
    for container in _repository.delete_server_query_2(db, server_id):
        container.deleted_at = _repository.delete_server_query_3()
    db.commit()


def test_ssh(server_id, db, user):
    server = active_record(db, Server, server_id)
    if not server.credential_id or not server.ssh_host_key:
        raise HTTPException(409, "Configure a credential and pinned host key first")
    credential = active_record(db, SSHCredential, server.credential_id)
    result = test_connection(server, credential)
    server.last_ssh_test_at = _repository.test_ssh_query()
    server.last_ssh_test_status = result["status"]
    db.commit()
    return result


def targets(db, authorization=None):
    service_authorization(authorization, get_settings().monitoring_token_file)
    return discovery_targets(db)


def servers_summary(db, user, server_ids):
    records = _repository.servers_summary_query(db, server_ids)
    return server_summaries(records)


def sync_containers(server_id, db, user):
    return synchronize_inventory(server_id, db)


def synchronize_inventory(server_id, db):
    server = active_record(db, Server, server_id)
    # Check current server state after locking, including concurrent soft deletion.
    _repository.synchronize_inventory_query(db, server)
    if server.deleted_at is not None:
        raise HTTPException(404, "Resource not found")
    if not server.monitoring_enabled:
        raise HTTPException(409, "Monitoring is disabled")
    # Serialize synchronizations for this server; a failed scrape never marks assets stopped.
    up = prometheus("/api/v1/query", {"query": f'up{{job="cadvisor",server_id="{server_id}"}}'})
    if not up["result"] or any(float(x["value"][1]) != 1 for x in up["result"]):
        raise HTTPException(502, "cAdvisor target is unavailable; container inventory preserved")
    data = prometheus(
        "/api/v1/query",
        {
            "query": f'container_last_seen{{job="cadvisor",server_id="{server_id}",image!=""}}',
        },
    )
    now = datetime.now(UTC)
    identities = identity_inventory(server, prometheus)
    seen = set()
    for item in data["result"]:
        labels = item["metric"]
        docker_id = labels.get("id")
        timestamp = float(item["value"][1])
        if not docker_id or now.timestamp() - timestamp > 60:
            continue
        seen.add(docker_id)
        values = {
            "server_id": server_id,
            "docker_id": docker_id,
            "name": labels.get("name", docker_id),
            "image": labels.get("image", ""),
            "status": "running",
            "labels": labels,
            "last_seen_at": datetime.fromtimestamp(timestamp, UTC),
        }
        values.update(identities.get(docker_identifier(docker_id), {}))
        _repository.synchronize_inventory_query_2(db, values)
    for container in _repository.synchronize_inventory_query_3(db, server_id):
        if container.docker_id not in seen:
            container.status = "stopped"
    db.commit()
    return _repository.synchronize_inventory_query_4(db, server_id).all()


def inventory_metrics(db, authorization=None):
    service_authorization(authorization, get_settings().monitoring_token_file)
    lines = ["# TYPE platform_container_up gauge", "# TYPE platform_inventory_up gauge"]
    server_ids = _repository.inventory_metrics_query(db)
    for server_id in server_ids:
        try:
            synchronize_inventory(server_id, db)
            healthy = 1
        except HTTPException:
            db.rollback()
            healthy = 0
        server = _repository.inventory_metrics_query_2(db, server_id)
        if server.deleted_at is not None or not server.monitoring_enabled:
            continue
        epoch = server.collection_config_changed_at.isoformat()
        lines.append(
            f'platform_inventory_up{{server_id="{server_id}",source_epoch="{epoch}"}} {healthy}'
        )
        for container in _repository.inventory_metrics_query_3(db, server_id):
            labels = {
                "server_id": str(server_id),
                "source_epoch": epoch,
                "container_id": str(container.id),
                "id": container.docker_id,
                "name": container.name,
                "image": container.image,
            }
            label_text = ",".join(
                f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in labels.items()
            )
            # Preserve the last known value on collector failure, rather than claiming
            # that all containers stopped or resolving an existing stopped event.
            lines.append(
                f"platform_container_up{{{label_text}}} {int(container.status == 'running')}"
            )
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


def containers(response, db, user, server_id=None, status=None, q=None, offset=0, limit=50):
    query = _repository.containers_query(q)
    if server_id:
        query = _repository.containers_query_2(query, server_id)
    if status:
        query = _repository.containers_query_3(query, status)
    return page(db, _repository.containers_query_4(query), response, offset, limit)


def container_detail(container_id, db, user):
    container = active_record(db, Container, container_id)
    from app.services.access_control import container_server

    container_server(db, container)
    return container


def update_container_service(container_id, body, db, user):
    container = active_record(db, Container, container_id)
    from app.services.access_control import container_server

    server = container_server(db, container)
    _repository.update_container_service_query(db, server)
    dependencies = list(dict.fromkeys(body.depends_on))
    if container_id in dependencies:
        raise HTTPException(422, "服务不能依赖自身")
    for dependency_id in dependencies:
        dependency = active_record(db, Container, dependency_id)
        if dependency.server_id != container.server_id:
            raise HTTPException(422, "当前仅支持同一服务器内的服务依赖")
    container.service_name = body.service_name.strip()
    container.purpose = body.purpose.strip()
    container.depends_on = [str(value) for value in dependencies]
    return save(db, container)


def server_container_overview(server_id, db, user):
    from app.services.resources.business_services import attach_services
    from app.services.resources.overview import overview

    server = active_record(db, Server, server_id)
    inventory = _repository.server_container_overview_query(db, server_id)
    return attach_services(db, overview(server, inventory))


def read_container_health(container_id, db, user):
    container = active_record(db, Container, container_id)
    from app.services.access_control import container_server

    server = container_server(db, container)
    return container_health(server, container.docker_id)


def container_diagnostics(container_id, db, user, kind="state"):
    container = active_record(db, Container, container_id)
    from app.services.access_control import container_server

    server = container_server(db, container)
    credential = (
        active_record(db, SSHCredential, server.credential_id) if server.credential_id else None
    )
    result = read_container(server, credential, container.docker_id, logs=kind == "logs")
    return {"container_id": container.id, "collected_at": datetime.now(UTC), **result}


def retire_container(container_id, db, user):
    container = active_record(db, Container, container_id)
    from app.services.access_control import container_server

    container_server(db, container)
    container.deleted_at = _repository.retire_container_query()
    db.commit()


def collectors_compose(server_id, db, user):

    active_record(db, Server, server_id)
    source = BACKEND_ROOT / "collector-compose.yaml"
    return Response(
        source.read_text(encoding="utf-8"),
        media_type="application/yaml",
        headers={"Content-Disposition": 'attachment; filename="collectors-compose.yaml"'},
    )
