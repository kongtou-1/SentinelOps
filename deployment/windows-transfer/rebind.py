"""Runs inside the restored backend. Mutates metadata only through existing APIs."""

import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path("/tmp/sentinel-transfer")
original = json.loads((ROOT / "metadata.json").read_text(encoding="utf-8-sig"))
runtime = json.loads((ROOT / "new-runtime.json").read_text(encoding="utf-8-sig"))
credentials = dict(
    re.findall(
        r"(username|password)\s*[:=]\s*(.+)",
        (ROOT / "admin-credentials.txt").read_text(encoding="utf-8-sig"),
    )
)
token = None


def api(path, method="GET", body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = Request(
        "http://127.0.0.1:8000" + path,
        headers=headers,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
    )
    try:
        with urlopen(req, timeout=45) as response:
            content = response.read()
            return json.loads(content) if content else None
    except HTTPError as exc:
        raise RuntimeError(f"{method} {path}: HTTP {exc.code}") from None


token = api("/api/v1/auth/login", "POST", credentials)["access_token"]


def all_rows(path):
    rows = []
    while True:
        batch = api(f"{path}?limit=100&offset={len(rows)}")
        rows.extend(batch)
        if len(batch) < 100:
            return rows


SERVER_FIELDS = (
    "name",
    "host",
    "ssh_port",
    "ssh_user",
    "credential_id",
    "ssh_host_key",
    "environment",
    "node_exporter_target",
    "cadvisor_target",
    "docker_state_target",
    "health_url",
    "monitoring_enabled",
)
TASK_FIELDS = (
    "name",
    "server_id",
    "container_id",
    "llm_endpoint_id",
    "metric",
    "selector",
    "threshold",
    "enabled",
    "interval_seconds",
)
RULE_FIELDS = (
    "code",
    "server_id",
    "llm_endpoint_id",
    "prediction_task_id",
    "prediction_horizon_hours",
    "metric",
    "threshold",
    "for_seconds",
    "severity",
    "enabled",
)


def native(row):
    return row["docker_id"].rsplit("/", 1)[-1]


old_runtime = {(row["project"], row["service"]): row for row in original["runtime"]}
new_runtime = {(row["origin"], row["service"]): row for row in runtime["containers"]}
managed = {row["id"] for row in original["runtime"]}
old_host_ids = {
    row["server_id"] for row in original["containers"] if native(row) in managed
}
if not old_host_ids:
    raise RuntimeError("No original local computer asset found")


def prepare():
    servers = all_rows("/api/v1/servers")
    by_name = {row["name"]: row for row in servers}
    mapping = {}
    for row in original["servers"]:
        body = {key: row[key] for key in SERVER_FIELDS}
        body["monitoring_enabled"] = False
        api("/api/v1/servers/" + row["id"], "PUT", body)
        if row["id"] in old_host_ids:
            name = f"{row['name']}-{runtime['computer']}-{runtime['project']}"[:100]
            body["name"], body["monitoring_enabled"] = name, True
            new = by_name.get(name) or api("/api/v1/servers", "POST", body)
            mapping[row["id"]] = new["id"]
    result = {"servers": mapping, "resources": original["resources"]}
    (ROOT / "rebind-result.json").write_text(json.dumps(result), encoding="utf-8")


def finish():
    result = json.loads((ROOT / "rebind-result.json").read_text())
    physical = {
        old_runtime[key]["id"]: row
        for key, row in new_runtime.items()
        if key in old_runtime
    }
    deadline = time.monotonic() + 180
    while True:
        rows = []
        for server in result["servers"].values():
            rows.extend(api(f"/api/v1/servers/{server}/sync-containers", "POST"))
        found = {(row["server_id"], native(row)): row for row in rows}
        wanted = [
            (result["servers"][row["server_id"]], physical[native(row)]["id"])
            for row in original["containers"]
            if native(row) in physical and row["server_id"] in result["servers"]
        ]
        if all(key in found for key in wanted):
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("New container inventory not yet available")
        time.sleep(3)
    mapping = {
        row["id"]: found[
            (result["servers"][row["server_id"]], physical[native(row)]["id"])
        ]["id"]
        for row in original["containers"]
        if native(row) in physical and row["server_id"] in result["servers"]
    }
    for row in original["containers"]:
        if row["id"] in mapping and (
            row["service_name"] or row["purpose"] or row["depends_on"]
        ):
            api(
                "/api/v1/containers/" + mapping[row["id"]] + "/service",
                "PUT",
                {
                    "service_name": row["service_name"],
                    "purpose": row["purpose"],
                    "depends_on": [mapping.get(key, key) for key in row["depends_on"]],
                },
            )
    groups = {
        (row["server_id"], row["application_id"])
        for row in original["containers"]
        if row["id"] in mapping and row["application_id"]
    }
    for host, application in groups:
        members = [
            mapping[row["id"]]
            for row in original["containers"]
            if row["server_id"] == host
            and row["application_id"] == application
            and row["id"] in mapping
        ]
        api(
            f"/api/v1/servers/{result['servers'][host]}/business-services/{application}/containers",
            "PUT",
            {"container_ids": members},
        )
        # Membership is current metadata. Frozen incidents, approvals and deployment evidence are unchanged.
        api(
            f"/api/v1/servers/{host}/business-services/{application}/containers",
            "PUT",
            {"container_ids": []},
        )
    for row in original["tasks"]:
        if row["server_id"] in result["servers"] or row["container_id"] in mapping:
            body = {key: row[key] for key in TASK_FIELDS}
            body["server_id"] = result["servers"].get(
                row["server_id"], row["server_id"]
            )
            body["container_id"] = mapping.get(row["container_id"], row["container_id"])
            api("/api/v1/prediction-tasks/" + row["id"], "PUT", body)
    for row in original["rules"]:
        if row["server_id"] in result["servers"]:
            body = {key: row[key] for key in RULE_FIELDS}
            body["server_id"] = result["servers"][row["server_id"]]
            api("/api/v1/alert-rules/" + row["id"], "PUT", body)
    api("/api/v1/monitoring/apply-rules", "POST")
    for app in original["applications"]:
        deployments = all_rows("/api/v1/applications/" + app["id"] + "/deployments")
        for row in deployments:
            if row["container_id"] not in mapping or row["ended_at"]:
                continue
            new_id = mapping[row["container_id"]]
            api(
                "/api/v1/application-deployments/" + row["id"] + "/end",
                "POST",
                {"ended_at": datetime.now(UTC).isoformat()},
            )
            old = next(
                c for c in original["containers"] if c["id"] == row["container_id"]
            )
            current = physical[native(old)]
            if not any(
                d["container_id"] == new_id and not d["ended_at"] for d in deployments
            ):
                api(
                    "/api/v1/applications/" + app["id"] + "/deployments",
                    "POST",
                    {
                        "release_id": row["release_id"],
                        "container_id": new_id,
                        "environment": row["environment"],
                        "compose_project": current["project"],
                        "compose_service": row["compose_service"],
                        "config_version": row["config_version"],
                        "started_at": current["started_at"],
                    },
                )
    resources = dict(original["resources"])
    resources["server_id"] = result["servers"][resources["server_id"]]
    resources["container_id"] = mapping[resources["container_id"]]
    resources["docker_id"] = found[
        (
            resources["server_id"],
            physical[
                native(
                    next(
                        row
                        for row in original["containers"]
                        if row["id"] == original["resources"]["container_id"]
                    )
                )
            ]["id"],
        )
    ]["docker_id"]
    resources["prepared_at"] = datetime.now(UTC).isoformat()
    resources.pop("ssh_check", None)
    resources.pop("llm_probe", None)
    result.update(
        {
            "resources": resources,
            "containers": mapping,
            "historical_approvals_untouched": True,
        }
    )
    assert api("/openapi.json") == json.loads(
        (ROOT / "openapi.json").read_text(encoding="utf-8-sig")
    )
    assert (
        api("/api/v1/source-snapshots/" + resources["snapshot_id"])["status"] == "ready"
    )
    if not api("/api/v1/servers/" + resources["server_id"] + "/test-ssh", "POST")[
        "success"
    ]:
        raise RuntimeError("Restored SSH identity did not verify")
    (ROOT / "rebind-result.json").write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    {"prepare": prepare, "finish": finish}[sys.argv[1]]()
    print("Portable metadata " + sys.argv[1] + " passed")
