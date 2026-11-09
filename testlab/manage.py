"""Local test resources. Run with backend/.venv/Scripts/python.exe testlab/manage.py."""

import argparse
import json
import re
import secrets
import shutil
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import httpx
import paramiko
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local/testlab"
COMPOSE = ["docker", "compose", "-f", str(ROOT / "compose.testlab.yaml")]
MODES = (
    "healthy",
    "slow",
    "business_error",
    "stuck",
    "cpu",
    "memory",
    "llm_401",
    "llm_429",
    "llm_503",
    "llm_timeout",
)
LOADS = ("paused", "steady", "ramp", "cycle", "burst")


def run(args, **kwargs):
    return subprocess.run(
        args,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **kwargs,
    ).stdout.strip()


def write_json(path, data):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def api_client(base_url="http://127.0.0.1:8000"):
    credentials = {}
    for line in (ROOT / ".local/admin-credentials.txt").read_text().splitlines():
        m = re.match(r"(username|password)\s*[:=]\s*(.+)", line)
        if m:
            credentials[m[1]] = m[2]
    client = httpx.Client(
        base_url=base_url + "/api/v1", timeout=45, trust_env=False
    )
    r = client.post("/auth/login", json=credentials)
    r.raise_for_status()
    client.headers["Authorization"] = "Bearer " + r.json()["access_token"]
    return client


def api(client, method, path, body=None):
    r = client.request(method, path, json=body)
    if not r.is_success:
        # Never echo request bodies, authorization, or credentials.
        raise RuntimeError(f"{method} {path}: HTTP {r.status_code}: {r.text[:500]}")
    return r.json() if r.content else None


def prepare():
    for name in ("state", "ssh"):
        (LOCAL / name).mkdir(parents=True, exist_ok=True)
    for name, value in (
        ("scenario.json", {"mode": "healthy"}),
        ("traffic.json", {"mode": "steady"}),
    ):
        if not (LOCAL / "state" / name).exists():
            write_json(LOCAL / "state" / name, value)
    if not (LOCAL / "gateway.key").exists():
        (LOCAL / "gateway.key").write_text(secrets.token_urlsafe(32), encoding="utf-8")
    client_key = LOCAL / "ssh/client.key"
    if not client_key.exists():
        key = paramiko.RSAKey.generate(2048)
        key.write_private_key_file(str(client_key))
        (LOCAL / "ssh/authorized_keys").write_text(
            key.get_name() + " " + key.get_base64() + "\n"
        )
    host_path = LOCAL / "ssh/ssh_host_ed25519_key"
    if not host_path.exists():
        key = Ed25519PrivateKey.generate()
        host_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.OpenSSH,
                serialization.NoEncryption(),
            )
        )
        (LOCAL / "ssh/host.pub").write_bytes(
            key.public_key().public_bytes(
                serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
            )
        )
    (LOCAL / "ssh/allowed_ids").touch(exist_ok=True)
    if not (LOCAL / "provider.key").exists():
        raise RuntimeError(
            "Save the provided LLM key to .local/testlab/provider.key first"
        )
    key = (LOCAL / "provider.key").read_text().strip()
    r = httpx.post(
        "https://api.deepseek.com/chat/completions",
        headers={"Authorization": "Bearer " + key},
        json={
            "model": "deepseek-flash",
            "messages": [{"role": "user", "content": "Return only OK."}],
            "max_tokens": 5,
            "stream": False,
            "thinking": {"type": "disabled"},
        },
        timeout=45,
    )
    if r.status_code != 200 or not r.json().get("choices", [{}])[0].get(
        "message", {}
    ).get("content"):
        raise RuntimeError(f"Real LLM check failed: HTTP {r.status_code}")
    write_json(
        LOCAL / "llm-check.json",
        {
            "checked_at": datetime.now(UTC).isoformat(),
            "http_status": r.status_code,
            "model": r.json().get("model"),
            "usage": r.json().get("usage"),
            "real_provider": True,
        },
    )
    (LOCAL / "runtime.env").write_text(
        "LAB_LLM_ENABLED=true\nLAB_LLM_BASE_URL=https://api.deepseek.com\nLAB_LLM_MODEL=deepseek-flash\n"
    )
    shutil.copyfile(LOCAL / "provider.key", ROOT / ".local/ai-secrets/deepseek.key")
    env = ROOT / ".env"
    if not (LOCAL / "platform-before.env").exists():
        shutil.copyfile(env, LOCAL / "platform-before.env")
    lines = env.read_text().splitlines()
    updates = {
        "AI_ENABLED": "true",
        "AI_BASE_URL": "https://api.deepseek.com",
        "AI_MODEL": "deepseek-flash",
        "ALERTMANAGER_PORT": "19093",
    }
    lines = [line for line in lines if line.split("=", 1)[0] not in updates]
    env.write_text(
        "\n".join([*lines, *(f"{k}={v}" for k, v in updates.items())]) + "\n"
    )
    repo = ROOT / "testlab/service"
    if not (repo / ".git").exists():
        run(["git", "-C", str(repo), "init", "-b", "main"])
        run(["git", "-C", str(repo), "add", "service.py", "Dockerfile", ".gitignore"])
        run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.name=Test Lab",
                "-c",
                "user.email=testlab@example.test",
                "commit",
                "-m",
                "Initial disposable business service",
            ]
        )
        run(["git", "-C", str(repo), "tag", "v1.0.0"])
    print(
        "Credentials, persistent SSH identity, real LLM and business source repository prepared"
    )


def up():
    revision = run(["git", "-C", str(ROOT / "testlab/service"), "rev-parse", "HEAD"])
    print(run([*COMPOSE, "build", "--build-arg", "REVISION=" + revision, "business"]))
    print(run([*COMPOSE, "up", "-d", "--no-build", "--wait"]))
    identifier = run([*COMPOSE, "ps", "-q", "business"])
    docker_id = run(["docker", "inspect", "--format", "{{.Id}}", identifier])
    (LOCAL / "ssh/allowed_ids").write_text(docker_id + "\n")
    # Synchronize existing credential volumes before starting the AI workers.
    print(run(["docker", "compose", "run", "--rm", "--no-deps", "prepare-volumes"]))
    print(
        run(
            [
                "docker",
                "compose",
                "--profile",
                "monitoring",
                "--profile",
                "ai",
                "--profile",
                "execution",
                "--profile",
                "reports",
                "up",
                "-d",
                "--no-deps",
                "--no-build",
                "--wait",
                "backend",
                "blackbox",
                "mailpit",
                "alertmanager",
                "prediction-worker",
                "ai-worker",
                "execution-worker",
                "report-worker",
            ]
        )
    )
    print("Test resources started; run register next")


def register():
    with closing(api_client()) as client:
        credentials = api(client, "GET", "/ssh-credentials?limit=100")
        credential = next(
            (x for x in credentials if x["name"] == "testlab-local-ssh"), None
        )
        if credential is None:
            credential = api(
                client,
                "POST",
                "/ssh-credentials",
                {
                    "name": "testlab-local-ssh",
                    "auth_type": "private_key",
                    "secret": (LOCAL / "ssh/client.key").read_text(),
                },
            )
        servers = api(client, "GET", "/servers?limit=100")
        server = next((x for x in servers if x["name"] == "local-docker-test"), None)
        if server is None:
            raise RuntimeError("Expected the existing local-docker-test resource")
        server = api(
            client,
            "PUT",
            "/servers/" + server["id"],
            {
                "name": server["name"],
                "host": "testlab-ssh",
                "ssh_port": 2222,
                "ssh_user": "root",
                "credential_id": credential["id"],
                "ssh_host_key": (LOCAL / "ssh/host.pub").read_text(),
                "environment": server["environment"],
                "node_exporter_target": server["node_exporter_target"],
                "cadvisor_target": server["cadvisor_target"],
                "docker_state_target": "docker-state:9400",
                "health_url": "http://testlab-business:8080/work",
                "monitoring_enabled": True,
            },
        )
        ssh = api(client, "POST", f"/servers/{server['id']}/test-ssh")
        if not ssh["success"]:
            raise RuntimeError("Platform SSH authentication failed: " + ssh["status"])
        rows = api(client, "POST", f"/servers/{server['id']}/sync-containers")
        current_id = (LOCAL / "ssh/allowed_ids").read_text().strip()
        container = next(
            (
                x
                for x in rows
                if x["name"] == "sentinelops-testlab-business-1"
                and x["docker_id"].endswith(current_id)
            ),
            None,
        )
        if container is None:
            raise RuntimeError(
                "Business container not yet observed; rerun register after a scrape"
            )
        endpoints = api(client, "GET", "/llm-endpoints?limit=100")
        endpoint = next(
            (x for x in endpoints if x["name"] == "testlab-deepseek-flash"), None
        )
        if endpoint is None:
            endpoint = api(
                client,
                "POST",
                "/llm-endpoints",
                {
                    "name": "testlab-deepseek-flash",
                    "base_url": "http://testlab-business:8080/v1",
                    "model": "deepseek-flash",
                    "probe_enabled": True,
                    "probe_interval_seconds": 3600,
                    "api_key": (LOCAL / "gateway.key").read_text().strip(),
                },
            )
        probe = api(client, "POST", f"/llm-endpoints/{endpoint['id']}/probe")
        if not probe["success"]:
            raise RuntimeError("Platform LLM probe failed: " + str(probe["error_type"]))
        apps = api(client, "GET", "/applications?limit=100")
        app = next((x for x in apps if x["code"] == "testlab-qa"), None)
        if app is None:
            app = api(
                client,
                "POST",
                "/applications",
                {
                    "code": "testlab-qa",
                    "name": "实测问答服务",
                    "owner": "local-test",
                    "entrypoint": "/lab/service.py",
                    "local_path": "testlab/service",
                    "path_mappings": [
                        {"runtime_prefix": "/lab", "repository_prefix": "."}
                    ],
                },
            )
        snapshots = api(client, "GET", f"/applications/{app['id']}/snapshots")
        snapshot = next((x for x in snapshots if x["status"] == "ready"), None)
        if snapshot is None:
            snapshot = api(
                client,
                "POST",
                f"/applications/{app['id']}/source-syncs",
                {"source": "local"},
            )
        documents = api(client, "GET", "/knowledge?limit=100")
        title = "实测环境：业务服务故障与恢复 SOP"
        doc = next((x for x in documents if x["title"] == title), None)
        if doc is None:
            doc = api(
                client,
                "POST",
                "/knowledge",
                {
                    "title": title,
                    "tags": ["testlab", "docker", "llm"],
                    "source": "testlab/README.md",
                    "content": (
                        "实测目标为 sentinelops-testlab-business-1，HTTP业务入口 /work，进程健康 /health，业务就绪 /ready。"
                        "stuck 表示运行时卡死，/ready 与 /work 返回503；审批重启后恢复。"
                        "business_error 表示业务逻辑故障，进程存活但/work返回503，重启不能修复；必须核对源码和部署版本。"
                        "slow增加3秒延迟，cpu增加每次请求的CPU计算，memory持有64MiB内存。"
                        "llm_401/429/503/timeout是可控上游故障，故障分支不调用真实供应商。"
                        "背景流量仅请求/work，不调用真实LLM。恢复后核对HTTP、Docker HEALTHCHECK和新鲜监控证据。"
                        "SSH仅允许目标容器的固定inspect、logs和restart；任何重启须经平台人工审批。"
                    ),
                },
            )
        tasks = api(client, "GET", "/prediction-tasks?limit=100")
        task = next((x for x in tasks if x["name"] == "testlab-business-memory"), None)
        if task is None:
            task = api(
                client,
                "POST",
                "/prediction-tasks",
                {
                    "name": "testlab-business-memory",
                    "container_id": container["id"],
                    "metric": "container_memory_bytes",
                    "threshold": 192 * 1024 * 1024,
                    "enabled": True,
                    "interval_seconds": 1800,
                },
            )
        elif task["container_id"] != container["id"]:
            task = api(
                client,
                "PUT",
                "/prediction-tasks/" + task["id"],
                {
                    "name": task["name"],
                    "container_id": container["id"],
                    "metric": "container_memory_bytes",
                    "threshold": 192 * 1024 * 1024,
                    "enabled": True,
                    "interval_seconds": 1800,
                },
            )
        rules = api(client, "GET", "/alert-rules?limit=100")
        for code, metric, threshold, target in (
            ("TestlabBusinessUnavailable", "http_up", 1, {"server_id": server["id"]}),
            (
                "TestlabBusinessSlow",
                "http_latency_seconds",
                2,
                {"server_id": server["id"]},
            ),
            ("TestlabLLMUnavailable", "llm_up", 1, {"llm_endpoint_id": endpoint["id"]}),
            (
                "TestlabLLMRateLimited",
                "llm_rate_limited",
                0,
                {"llm_endpoint_id": endpoint["id"]},
            ),
            (
                "TestlabLLMServiceError",
                "llm_service_error",
                0,
                {"llm_endpoint_id": endpoint["id"]},
            ),
        ):
            if not any(x["code"] == code for x in rules):
                api(
                    client,
                    "POST",
                    "/alert-rules",
                    {
                        "code": code,
                        "metric": metric,
                        "threshold": threshold,
                        "for_seconds": 30,
                        "severity": "warning",
                        **target,
                    },
                )
        api(client, "POST", "/monitoring/apply-rules")
        write_json(
            LOCAL / "resources.json",
            {
                "prepared_at": datetime.now(UTC).isoformat(),
                "server_id": server["id"],
                "container_id": container["id"],
                "docker_id": container["docker_id"],
                "credential_id": credential["id"],
                "llm_endpoint_id": endpoint["id"],
                "application_id": app["id"],
                "snapshot_id": snapshot["id"],
                "document_id": doc["id"],
                "prediction_task_id": task["id"],
                "ssh_check": ssh,
                "llm_probe": probe,
            },
        )
        print(
            "Platform assets, LLM probe, source snapshot, SOP, prediction and alert rules registered"
        )


def bind_release():
    saved = json.loads((LOCAL / "resources.json").read_text())
    with closing(api_client()) as client:
        snapshot = api(client, "GET", "/source-snapshots/" + saved["snapshot_id"])
        if snapshot["status"] != "ready":
            raise RuntimeError("Source snapshot is not ready: " + snapshot["status"])
        image = run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.Image}}",
                run([*COMPOSE, "ps", "-q", "business"]),
            ]
        )
        releases = api(
            client, "GET", f"/applications/{saved['application_id']}/releases"
        )
        release = next((x for x in releases if x["version"] == "1.0.0"), None)
        if release is None:
            release = api(
                client,
                "POST",
                f"/applications/{saved['application_id']}/releases",
                {
                    "version": "1.0.0",
                    "snapshot_id": snapshot["id"],
                    "image_ref": "sentinelops-testlab-business:v1",
                    "image_id": image,
                    "build_source_status": "local_snapshot",
                    "change_summary": "可控故障、真实DeepSeek问答与本地流量的实测业务服务",
                    "test_result": "见 .local/testlab/verification.json；本地源码快照，非远程Git构建证明",
                },
            )
        deployments = api(
            client, "GET", f"/applications/{saved['application_id']}/deployments"
        )
        for previous in deployments:
            if (
                previous["compose_project"] == "sentinelops-testlab"
                and previous["compose_service"] == "business"
                and previous["container_id"] != saved["container_id"]
                and previous["ended_at"] is None
            ):
                api(
                    client,
                    "POST",
                    "/application-deployments/" + previous["id"] + "/end",
                    {"ended_at": datetime.now(UTC).isoformat()},
                )
        if not any(
            x["container_id"] == saved["container_id"] and x["ended_at"] is None
            for x in deployments
        ):
            started = run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.State.StartedAt}}",
                    run([*COMPOSE, "ps", "-q", "business"]),
                ]
            )
            api(
                client,
                "POST",
                f"/applications/{saved['application_id']}/deployments",
                {
                    "release_id": release["id"],
                    "container_id": saved["container_id"],
                    "environment": "local-test",
                    "compose_project": "sentinelops-testlab",
                    "compose_service": "business",
                    "config_version": "testlab-v1",
                    "started_at": started,
                },
            )
        print("Business release and deployment linked to immutable source snapshot")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "prepare",
            "up",
            "register",
            "bind-release",
            "status",
            "fault",
            "load",
            "stop",
            "start",
        ),
    )
    parser.add_argument("mode", nargs="?")
    args = parser.parse_args()
    if args.action == "prepare":
        prepare()
    elif args.action == "up":
        up()
    elif args.action == "register":
        register()
    elif args.action == "bind-release":
        bind_release()
    elif args.action in ("fault", "load"):
        choices = MODES if args.action == "fault" else LOADS
        if args.mode not in choices:
            parser.error("mode must be one of: " + ", ".join(choices))
        filename = "scenario.json" if args.action == "fault" else "traffic.json"
        write_json(LOCAL / "state" / filename, {"mode": args.mode})
        print(f"{args.action}: {args.mode}")
    elif args.action == "status":
        print(run([*COMPOSE, "ps"]))
        print(httpx.get("http://127.0.0.1:18080/status", trust_env=False).text)
        print((LOCAL / "state/traffic-status.json").read_text())
    elif args.action == "stop":
        print(run([*COMPOSE, "stop"]))
    elif args.action == "start":
        print(run([*COMPOSE, "start"]))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(exc.stderr or exc.stdout, file=sys.stderr)
        raise SystemExit(exc.returncode)
