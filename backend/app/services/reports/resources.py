"""Resolve report resources from frozen evidence, without attributing host faults to services."""

from datetime import datetime
from uuid import UUID

import app.repositories.reports.resources as _repository
from app.models import Application, ApplicationDeployment, Container, LLMEndpoint, Server
from app.utils.docker import docker_identifier


def identifier(value):
    try:
        return UUID(str(value)) if value else None
    except ValueError:
        return None


def resource_context(db, evidence):
    if "resource_context" in evidence:
        return evidence["resource_context"]
    resources, services, inventory, assets, deployments = {}, {}, {}, {}, {}

    def asset(model, key):
        if key and (model, key) not in assets:
            assets[(model, key)] = _repository.resource_context_asset_query(db, key, model)
        return assets.get((model, key))

    def add_resource(kind, model, value):
        key = identifier(value)
        row = asset(model, key)
        if key:
            resources[(kind, str(key))] = {
                "kind": kind,
                "id": str(key),
                "name": row.name if row else str(key),
            }
        return row

    def add_service(value):
        key = identifier(value)
        row = asset(Application, key)
        if key:
            services[str(key)] = {"id": str(key), "name": row.name if row else str(key)}

    for record in evidence.get("records", []):
        data = record["data"]
        if record["kind"] == "repair_handoff":
            add_service(data.get("application_id"))
        elif record["kind"] in ("approval", "execution"):
            container = add_resource("container", Container, data.get("container_id"))
            if container:
                add_resource("server", Server, container.server_id)
                add_service(container.application_id)
        elif record["kind"] == "analysis":
            for binding in (data.get("source_context") or {}).get("bindings", []):
                add_service(binding.get("application_id"))
        elif record["kind"] == "alert":
            server = add_resource("server", Server, data.get("server_id"))
            endpoint = add_resource("llm_endpoint", LLMEndpoint, data.get("llm_endpoint_id"))
            labels = data.get("labels") or {}
            config = (data.get("rule_snapshot") or {}).get("prediction_config") or {}
            candidate = labels.get("container_id") or config.get("container_id")
            container = None
            if server:
                if server.id not in inventory:
                    inventory[server.id] = _repository.resource_context_query(db, server).all()
                containers = inventory[server.id]
                native = docker_identifier(labels.get("id") or labels.get("docker_id") or "")
                matches = [
                    c
                    for c in containers
                    if (candidate and c.id == identifier(candidate))
                    or (not candidate and native and docker_identifier(c.docker_id) == native)
                    or (not candidate and not native and labels.get("name") == c.name)
                ]
                container = matches[0] if len(matches) == 1 else None
            if container:
                add_resource("container", Container, container.id)
                add_service(container.application_id)
            target = endpoint or container
            if target and data.get("starts_at"):
                column = (
                    ApplicationDeployment.llm_endpoint_id
                    if endpoint
                    else ApplicationDeployment.container_id
                )
                started = datetime.fromisoformat(data["starts_at"])
                key = (column.key, target.id)
                if key not in deployments:
                    deployments[key] = _repository.resource_context_query_2(
                        column, db, target
                    ).all()
                for deployment in deployments[key]:
                    if deployment.started_at <= started and (
                        deployment.ended_at is None or deployment.ended_at > started
                    ):
                        add_service(deployment.application_id)
    return {
        "services": sorted(services.values(), key=lambda v: (v["name"], v["id"])),
        "resources": sorted(resources.values(), key=lambda v: (v["kind"], v["name"], v["id"])),
        "source": "current_assets",
    }
