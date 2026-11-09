"""Verify live test resources; always restore healthy service and steady traffic."""

import json
import time
from contextlib import closing
from datetime import UTC, datetime

import httpx
import paramiko
from manage import COMPOSE, LOCAL, api, api_client, run, write_json

saved = json.loads((LOCAL / "resources.json").read_text())
report = {"checked_at": datetime.now(UTC).isoformat(), "checks": {}, "passed": False}


def check(name, value):
    report["checks"][name] = bool(value)
    if not value:
        raise AssertionError(name)
    print(name + ": passed", flush=True)


def fault(mode):
    write_json(LOCAL / "state/scenario.json", {"mode": mode})


try:
    write_json(LOCAL / "state/traffic.json", {"mode": "paused"})
    fault("healthy")
    with httpx.Client(
        base_url="http://127.0.0.1:18080", timeout=50, trust_env=False
    ) as web:
        check("business_page", web.get("/").status_code == 200)
        check("business_work", web.get("/work").json()["ok"])
        check(
            "invalid_request_rejected",
            web.post("/ask", json={"question": ""}).status_code == 400,
        )
        check(
            "gateway_authentication",
            web.post("/v1/chat/completions", json={"messages": []}).status_code == 401,
        )
        before_llm = web.get("/status").json()["llm_calls"]
        for mode in ("business_error", "stuck"):
            fault(mode)
            check(
                mode,
                web.get("/work").status_code == 503
                and web.get("/ready").status_code == 503,
            )
            if mode == "business_error":
                check(
                    "live_process_with_failed_business",
                    web.get("/health").status_code == 200,
                )
        fault("slow")
        start = time.monotonic()
        r = web.get("/work")
        check("slow_response", r.status_code == 200 and time.monotonic() - start >= 2.8)
        fault("cpu")
        start = time.monotonic()
        r = web.get("/work")
        check("cpu_work", r.status_code == 200 and time.monotonic() - start >= 0.7)
        fault("memory")
        web.get("/work").raise_for_status()
        memory = int(
            run(
                [
                    *COMPOSE,
                    "exec",
                    "-T",
                    "business",
                    "cat",
                    "/sys/fs/cgroup/memory/memory.usage_in_bytes",
                ]
            )
        )
        check(
            "bounded_memory_allocation", 64 * 1024 * 1024 < memory < 256 * 1024 * 1024
        )
        gateway_headers = {
            "Authorization": "Bearer " + (LOCAL / "gateway.key").read_text().strip()
        }
        for code in (401, 429, 503):
            fault(f"llm_{code}")
            r = web.post(
                "/v1/chat/completions",
                headers=gateway_headers,
                json={"messages": [{"role": "user", "content": "Return only OK."}]},
            )
            check("upstream_" + str(code), r.status_code == code)
        fault("llm_timeout")
        timed_out = False
        try:
            web.post(
                "/v1/chat/completions",
                headers=gateway_headers,
                json={"messages": [{"role": "user", "content": "Return only OK."}]},
                timeout=2,
            )
        except httpx.TimeoutException:
            timed_out = True
        check("upstream_timeout", timed_out)
        check(
            "injected_failures_do_not_call_provider",
            web.get("/status").json()["llm_calls"] == before_llm,
        )
        fault("healthy")
        web.get("/work").raise_for_status()
        r = web.post("/ask", json={"question": "请用一句话说明容器健康检查的作用。"})
        check(
            "real_llm_business_answer",
            r.status_code == 200 and bool(r.json().get("answer")),
        )
        report["real_llm_usage"] = r.json().get("usage")
        report["memory_bytes_during_fault"] = memory

    # Read the pinned host key from the locally generated host identity.
    public = (LOCAL / "ssh/host.pub").read_text().split()
    import base64

    host_key = paramiko.PKey.from_type_string(public[0], base64.b64decode(public[1]))
    with paramiko.SSHClient() as ssh:
        ssh.get_host_keys().add("[127.0.0.1]:12222", host_key.get_name(), host_key)
        ssh.connect(
            "127.0.0.1",
            port=12222,
            username="root",
            pkey=paramiko.RSAKey.from_private_key_file(str(LOCAL / "ssh/client.key")),
            look_for_keys=False,
            allow_agent=False,
            timeout=10,
        )
        docker_id = (LOCAL / "ssh/allowed_ids").read_text().strip()
        for name, command, expected in (
            (
                "ssh_allowed_inspect",
                "docker inspect --format '{{json .Id}} {{json .State}}' " + docker_id,
                0,
            ),
            (
                "ssh_allowed_logs",
                "docker logs --since 15m --tail 200 --timestamps " + docker_id,
                0,
            ),
            ("ssh_arbitrary_command_denied", "id", 1),
            ("ssh_other_target_denied", "docker restart -t 10 " + "0" * 64, 1),
        ):
            _, stdout, stderr = ssh.exec_command(command, timeout=10)
            stdout.read()
            stderr.read()
            check(name, stdout.channel.recv_exit_status() == expected)

    with closing(api_client("http://127.0.0.1:8080")) as client:
        check(
            "platform_ssh",
            api(client, "POST", f"/servers/{saved['server_id']}/test-ssh")["success"],
        )
        for kind in ("state", "logs"):
            data = api(
                client,
                "GET",
                f"/containers/{saved['container_id']}/diagnostics?kind={kind}",
            )
            check("platform_diagnostics_" + kind, not data.get("error_code"))
        for metric, route in (
            ("host_cpu_percent", f"/servers/{saved['server_id']}/metrics"),
            ("http_up", f"/servers/{saved['server_id']}/metrics"),
            ("container_cpu_percent", f"/containers/{saved['container_id']}/metrics"),
            ("container_memory_bytes", f"/containers/{saved['container_id']}/metrics"),
        ):
            data = api(client, "GET", route + "?metric=" + metric)
            check("monitoring_" + metric, bool(data.get("result")))
        snapshot = api(client, "GET", "/source-snapshots/" + saved["snapshot_id"])
        check("source_snapshot", snapshot["status"] == "ready")
        source = api(
            client,
            "GET",
            f"/source-snapshots/{saved['snapshot_id']}/file?path=service.py",
        )
        check(
            "source_content",
            source.get("availability") == "available"
            and "def complete" in source.get("content", ""),
        )
        doc = api(client, "GET", "/knowledge/" + saved["document_id"])
        check("knowledge_index", doc["index_status"] == "ready")
        check(
            "prediction_task_enabled",
            api(client, "GET", "/prediction-tasks/" + saved["prediction_task_id"])[
                "enabled"
            ],
        )
    for url in (
        "http://127.0.0.1:19090/-/ready",
        "http://127.0.0.1:19093/-/ready",
        "http://127.0.0.1:8025/api/v1/info",
        "http://127.0.0.1:8000/health/ready",
        "http://127.0.0.1:8080/health/ready",
    ):
        check(
            "ready_" + url.split(":")[2].split("/")[0],
            httpx.get(url, timeout=10, trust_env=False).status_code == 200,
        )
    report["passed"] = True
finally:
    fault("healthy")
    write_json(LOCAL / "state/traffic.json", {"mode": "steady"})
    write_json(LOCAL / "verification.json", report)
    print("Restored healthy business and steady local traffic", flush=True)
