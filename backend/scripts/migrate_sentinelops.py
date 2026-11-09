"""One-time, local SentinelOps directory/Compose migration with retained rollback data."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SOURCE = Path(r"D:\research\contest")
TARGET = Path(r"D:\research\sentinelops")
STATE = SOURCE / ".local/sentinelops-refactor"
VOLUMES = (
    "postgres_data",
    "prometheus_data",
    "alertmanager_data",
    "mailpit_data",
    "backend_secrets",
    "prometheus_secrets",
    "alertmanager_secrets",
    "platform_rules",
    "source_store",
)
PROFILES = ("monitoring", "ai", "execution", "reports", "sources", "local-test")


def run(args, *, cwd=SOURCE, env=None, output=None):
    result = subprocess.run(
        [str(x) for x in args],
        cwd=cwd,
        env=env,
        stdout=output or subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        message = result.stderr.decode("utf-8", errors="replace")[-3000:]
        raise RuntimeError(f"{args[0]} {args[1]} failed ({result.returncode}): {message}")
    return (result.stdout or b"").decode("utf-8", errors="replace")


def docker(*args, **kwargs):
    return run(["docker", *args], **kwargs)


def compose(*args):
    env = {**os.environ, "SOURCE_INPUT_ROOT": str(TARGET)}
    command = [
        "docker",
        "compose",
        "-f",
        str(TARGET / "compose.yaml"),
        "-f",
        str(TARGET / "compose.sources-local.yaml"),
    ]
    for profile in PROFILES:
        command += ["--profile", profile]
    return run([*command, *args], cwd=TARGET, env=env)


def lab(*args):
    return run(["docker", "compose", "-f", str(TARGET / "compose.testlab.yaml"), *args], cwd=TARGET)


def wait(label, check, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except (HTTPError, URLError, RuntimeError, OSError, KeyError):
            pass
        time.sleep(3)
    raise RuntimeError("Timed out: " + label)


def psql(container, sql):
    return docker(
        "exec",
        container,
        "psql",
        "-X",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        "ops_platform",
        "-d",
        "ops_platform",
        "-At",
        "-c",
        sql,
    )


def database_fingerprint(container):
    names = psql(
        container, "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    ).splitlines()
    assert all(re.fullmatch(r"[a-z_]+", name) for name in names)
    sql = " UNION ALL ".join(
        f"SELECT '{name}', count(*)::text, "
        f"md5(COALESCE(string_agg(md5(t::text), '' ORDER BY t::text), '')) FROM {name} t"
        for name in names
    )
    return sorted(psql(container, sql).splitlines())


def api(path, method="GET", body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = Request(
        "http://127.0.0.1:8000" + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method=method,
    )
    with urlopen(request, timeout=45) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def login():
    values = dict(
        re.findall(
            r"(username|password)\s*[:=]\s*(.+)",
            (TARGET / ".local/admin-credentials.txt").read_text(encoding="utf-8"),
        )
    )
    return api("/api/v1/auth/login", "POST", values)["access_token"]


def containers(token):
    rows = []
    while True:
        batch = api(f"/api/v1/containers?limit=100&offset={len(rows)}", token=token)
        rows.extend(batch)
        if len(batch) < 100:
            return rows


def rebind_container_configuration(token, original, manifest):
    """Preserve the existing groups, notes and dependency graph for renamed local containers."""
    names = set(docker("ps", "-a", "--format", "{{.Names}}").splitlines())
    pairs = {}
    for item in manifest:
        old = item["name"].lstrip("/")
        new = old.replace("contest-ops-", "sentinelops-", 1).replace(
            "contest-testlab-", "sentinelops-testlab-", 1
        )
        if item["state"] == "running" and old in names and new in names:
            pairs[old] = new
    inspected = docker("inspect", "--format", "{{.Name}}|{{.Id}}", *pairs, *pairs.values())
    native = dict(line.lstrip("/").split("|", 1) for line in inspected.splitlines())
    physical = {native[old]: native[new] for old, new in pairs.items()}
    original_by_id = {row["id"]: row for row in original}
    hosts = {
        row["server_id"] for row in original if row["docker_id"].rsplit("/", 1)[-1] in physical
    }
    current = []

    def fresh_inventory():
        nonlocal current
        for host in hosts:
            api(f"/api/v1/servers/{host}/sync-containers", "POST", token=token)
        current = containers(token)
        found = {(row["server_id"], row["docker_id"].rsplit("/", 1)[-1]) for row in current}
        return all(
            (row["server_id"], physical[row["docker_id"].rsplit("/", 1)[-1]]) in found
            for row in original
            if row["docker_id"].rsplit("/", 1)[-1] in physical
        )

    wait("renamed local container inventory", fresh_inventory, seconds=150)
    current_by_native = {
        (row["server_id"], row["docker_id"].rsplit("/", 1)[-1]): row for row in current
    }
    mapping = {
        row["id"]: current_by_native[(row["server_id"], physical[native_id])]["id"]
        for row in original
        if (native_id := row["docker_id"].rsplit("/", 1)[-1]) in physical
    }

    # Existing UI membership may be inherited from an earlier instance in the
    # same Compose slot. Resolve those memberships before changing project names.
    def slot(row):
        identity = row["identity"] or {}
        return (
            row["server_id"],
            identity.get("compose_project"),
            identity.get("compose_service"),
            row["name"],
        )

    replacements = {slot(original_by_id[old]): new for old, new in mapping.items()}
    owners = {}
    for row in original:
        if row["application_id"] and row["status"] != "retired":
            owners.setdefault(slot(row), set()).add(row["application_id"])
    membership_mapping = dict(mapping)
    for row in original:
        key = slot(row)
        if key in replacements and len(owners.get(key, ())) == 1:
            membership_mapping[row["id"]] = replacements[key]
    for old_id, new_id in mapping.items():
        row = original_by_id[old_id]
        if row["service_name"] or row["purpose"] or row["depends_on"]:
            api(
                f"/api/v1/containers/{new_id}/service",
                "PUT",
                {
                    "service_name": row["service_name"],
                    "purpose": row["purpose"],
                    "depends_on": [
                        mapping.get(identifier, identifier) for identifier in row["depends_on"]
                    ],
                },
                token,
            )
    groups = {
        (row["server_id"], row["application_id"])
        for row in original
        if row["id"] in membership_mapping and row["application_id"]
    }
    for host, application in groups:
        desired = list(
            dict.fromkeys(
                membership_mapping.get(row["id"], row["id"])
                for row in original
                if row["server_id"] == host
                and row["application_id"] == application
                and row["status"] != "retired"
            )
        )
        api(
            f"/api/v1/servers/{host}/business-services/{application}/containers",
            "PUT",
            {"container_ids": desired},
            token,
        )
    return mapping


def prepare():
    if TARGET.exists():
        raise RuntimeError("Target already exists; refusing to merge or overwrite it")
    ignored = {
        ".venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".uv-cache",
        ".pnpm-store",
        "dist",
    }

    def ignore(directory, names):
        return [
            name
            for name in names
            if name in ignored
            or (Path(directory).name == ".local" and name.startswith(("pytest", "tmp-")))
        ]

    shutil.copytree(SOURCE, TARGET, ignore=ignore)
    config = TARGET / ".env"
    text = (
        config.read_text(encoding="utf-8")
        .replace(str(SOURCE), str(TARGET))
        .replace(SOURCE.as_posix(), TARGET.as_posix())
    )
    config.write_text(text, encoding="utf-8")
    python = TARGET / Path(sys.base_prefix).relative_to(SOURCE) / "python.exe"
    env = {
        **os.environ,
        "UV_CACHE_DIR": str(SOURCE / ".uv-cache"),
        "UV_PYTHON_INSTALL_DIR": str(TARGET / ".python"),
    }
    run(
        [
            TARGET / ".tools/uv.exe",
            "sync",
            "--project",
            TARGET / "backend",
            "--frozen",
            "--python",
            python,
        ],
        cwd=TARGET,
        env=env,
    )
    pnpm = shutil.which("pnpm")
    assert pnpm
    run(
        [
            pnpm,
            "--dir",
            TARGET / "frontend",
            "install",
            "--frozen-lockfile",
            "--store-dir",
            TARGET / ".pnpm-store",
        ],
        cwd=TARGET,
    )
    # The old testlab artifacts remain valid by immutable image ID.
    for old, new in (
        ("contest-testlab-business:v1", "sentinelops-testlab-business:v1"),
        ("contest-testlab-business:v1-bug", "sentinelops-testlab-business:v1-bug"),
        ("contest-ops-execution-ssh:local", "sentinelops-execution-ssh:local"),
    ):
        docker("image", "tag", old, new)
    compose("config", "--quiet")
    compose("build", "backend", "frontend", "docker-state")
    print(
        "New directory, locked dependencies and images prepared; old platform is running.",
        flush=True,
    )


def rollback():
    manifest = json.loads((STATE / "runtime-before.json").read_text(encoding="utf-8"))
    names = [c["name"].lstrip("/") for c in manifest if c["state"] == "running"]
    existing = set(docker("ps", "-a", "--format", "{{.Names}}").splitlines())
    if not set(names) <= existing:
        raise RuntimeError(
            "Original containers have been removed; automatic restart rollback is unavailable. "
            "Restore from retained images, volumes and removed-containers-inspect.json first. "
            "The current environment has not been stopped."
        )
    if TARGET.exists():
        lab("stop")
        compose("stop")
    if "contest-ops-postgres-1" in names:
        docker("start", "contest-ops-postgres-1")
        wait("old PostgreSQL", lambda: psql("contest-ops-postgres-1", "SELECT 1").strip() == "1")
    docker("start", *[name for name in names if name != "contest-ops-postgres-1"])
    print("Old containers restarted with their original images and volumes.", flush=True)


def cutover():
    if not (TARGET / "backend/.venv/Scripts/python.exe").exists():
        raise RuntimeError("Run prepare first")
    active = psql(
        "contest-ops-postgres-1",
        "SELECT count(*) FROM skill_executions "
        "WHERE status IN ('queued','prechecking','executing','verifying') AND deleted_at IS NULL",
    ).strip()
    if active != "0":
        raise RuntimeError(
            "An operation is executing; leave the platform running and retry after it finishes"
        )
    existing = set(docker("volume", "ls", "--format", "{{.Name}}").splitlines())
    if any("sentinelops_" + name in existing for name in VOLUMES):
        raise RuntimeError("New volumes already exist; refusing to overwrite them")
    manifest = json.loads((STATE / "runtime-before.json").read_text(encoding="utf-8"))
    assert all(
        c["labels"].get("com.docker.compose.project") in ("contest-ops", "contest-testlab")
        for c in manifest
    )
    names = [c["name"].lstrip("/") for c in manifest if c["state"] == "running"]
    image = next(c["image"] for c in manifest if c["name"] == "/contest-ops-backend-1")
    result = {
        "started_at": datetime.now(UTC).isoformat(),
        "source": str(SOURCE),
        "target": str(TARGET),
        "volumes": {},
    }
    try:
        print("Maintenance started: stopping entrypoints, traffic and writers.", flush=True)
        docker("stop", "contest-ops-frontend-1", "contest-testlab-traffic-1")
        docker(
            "stop",
            *[
                n
                for n in names
                if n
                not in (
                    "contest-ops-postgres-1",
                    "contest-ops-frontend-1",
                    "contest-testlab-traffic-1",
                )
            ],
        )
        result["database_before"] = database_fingerprint("contest-ops-postgres-1")
        with (STATE / "database-maintenance.dump").open("wb") as stream:
            docker(
                "exec",
                "contest-ops-postgres-1",
                "pg_dump",
                "-U",
                "ops_platform",
                "-d",
                "ops_platform",
                "-Fc",
                output=stream,
            )
        docker("stop", "contest-ops-postgres-1")
        for name in VOLUMES:
            old, new = "contest-ops_" + name, "sentinelops_" + name
            docker(
                "volume",
                "create",
                "--label",
                "com.docker.compose.project=sentinelops",
                "--label",
                "com.docker.compose.volume=" + name,
                new,
            )
            docker(
                "run",
                "--rm",
                "--user",
                "0:0",
                "--entrypoint",
                "/bin/bash",
                "--mount",
                f"type=volume,source={old},target=/from,readonly",
                "--mount",
                f"type=volume,source={new},target=/to",
                image,
                "-euc",
                "cp -a /from/. /to/",
            )
            digests = []
            for volume in (old, new):
                digests.append(
                    docker(
                        "run",
                        "--rm",
                        "--user",
                        "0:0",
                        "--entrypoint",
                        "/bin/bash",
                        "--mount",
                        f"type=volume,source={volume},target=/data,readonly",
                        image,
                        "-euc",
                        "set -o pipefail; cd /data; tar --sort=name -cf - . | sha256sum",
                    ).split()[0]
                )
            if digests[0] != digests[1]:
                raise RuntimeError("Volume content/ownership comparison failed: " + name)
            result["volumes"][name] = {"old": old, "new": new, "sha256": digests[0]}
            print("Copied and verified " + name, flush=True)
        compose("up", "-d", "--no-build", "--wait", "postgres")
        result["database_after"] = database_fingerprint("sentinelops-postgres-1")
        assert result["database_before"] == result["database_after"], (
            "Database rows differ before starting workers"
        )
        compose("run", "--rm", "migrate")
        compose("run", "--rm", "prepare-volumes")
        lab("up", "-d", "--no-build", "--wait", "business", "ssh")
        business = lab("ps", "-q", "business").strip()
        identity = json.loads(docker("inspect", business))[0]
        (TARGET / ".local/testlab/ssh/allowed_ids").write_text(
            identity["Id"] + "\n", encoding="utf-8"
        )
        compose(
            "up",
            "-d",
            "--no-build",
            "--wait",
            "backend",
            "prometheus",
            "blackbox",
            "mailpit",
            "node-exporter",
            "cadvisor",
            "docker-state",
            "source-worker",
        )
        token = login()
        original_containers = containers(token)
        saved = json.loads((TARGET / ".local/testlab/resources.json").read_text(encoding="utf-8"))
        # End only this local project's previous active deployment, through the existing API.
        deployments = api(
            f"/api/v1/applications/{saved['application_id']}/deployments", token=token
        )
        for item in deployments:
            if item["compose_project"] == "contest-testlab" and item["ended_at"] is None:
                api(
                    "/api/v1/application-deployments/" + item["id"] + "/end",
                    "POST",
                    {"ended_at": datetime.now(UTC).isoformat()},
                    token,
                )

        def register():
            try:
                run(
                    [
                        TARGET / "backend/.venv/Scripts/python.exe",
                        TARGET / "testlab/manage.py",
                        "register",
                    ],
                    cwd=TARGET,
                )
                return True
            except RuntimeError:
                return False

        wait("fresh container inventory and testlab registration", register, seconds=150)
        new_saved = json.loads(
            (TARGET / ".local/testlab/resources.json").read_text(encoding="utf-8")
        )
        run(
            [
                TARGET / "backend/.venv/Scripts/python.exe",
                TARGET / "testlab/manage.py",
                "bind-release",
            ],
            cwd=TARGET,
        )
        document = api("/api/v1/knowledge/" + new_saved["document_id"], token=token)
        if "contest-testlab" in document["content"]:
            body = {k: document[k] for k in ("title", "content", "source", "tags", "enabled")}
            body["content"] = body["content"].replace("contest-testlab", "sentinelops-testlab")
            api("/api/v1/knowledge/" + document["id"], "PUT", body, token)
        compose(
            "up",
            "-d",
            "--no-build",
            "--wait",
            "prediction-worker",
            "ai-worker",
            "report-worker",
            "execution-worker",
            "alertmanager",
        )
        rebind_container_configuration(token, original_containers, manifest)
        expected = json.loads((STATE / "openapi-before.json").read_text(encoding="utf-8"))
        expected["info"]["title"] = "SentinelOps"
        assert api("/openapi.json") == expected
        assert api("/health/ready")["status"] == "ok"
        assert api("/api/v1/me", token=token)["role"] == "admin"
        current = api(f"/api/v1/applications/{saved['application_id']}/deployments", token=token)
        assert any(
            x["container_id"] == new_saved["container_id"]
            and x["ended_at"] is None
            and x["compose_project"] == "sentinelops-testlab"
            for x in current
        )
        assert (
            api("/api/v1/source-snapshots/" + saved["snapshot_id"], token=token)["status"]
            == "ready"
        )
        result.update(
            {
                "new_testlab_container": identity["Id"],
                "new_resources": new_saved,
                "openapi_equal": True,
                "historical_snapshot_preserved": True,
                "database_equal_before_workers": True,
            }
        )
        lab("up", "-d", "--no-build", "--wait", "traffic")
        compose("up", "-d", "--no-build", "--wait", "frontend")
        result["container_configuration_mapping"] = rebind_container_configuration(
            token, original_containers, manifest
        )
        with urlopen("http://127.0.0.1:8080", timeout=15) as response:
            assert response.status == 200
        result["completed_at"] = datetime.now(UTC).isoformat()
        for directory in (STATE, TARGET / ".local/sentinelops-refactor"):
            (directory / "cutover.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        print(
            "Cutover passed: volumes, database, API, historical snapshot "
            "and current deployment verified.",
            flush=True,
        )
    except BaseException:
        (STATE / "cutover-failed.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        rollback()
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("prepare", "cutover", "rollback"))
    stage = parser.parse_args().stage
    {"prepare": prepare, "cutover": cutover, "rollback": rollback}[stage]()
