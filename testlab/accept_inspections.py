"""Inspect captured real work responses and labelled replays through the deployed API."""

import argparse
import copy
import hashlib
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".local/testlab/inspection-acceptance"
APPLICATION = "9501216c-a795-4220-b06e-69ab00fe5ac2"
API = "http://127.0.0.1:18088/api/v1"
SOURCE = "http://testlab-inspection-source:8090"
ACTIVE = {"queued", "collecting", "evaluating"}


def write(name, value):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def capture(client):
    responses = {}
    for path in ("status", "health", "ready", "work"):
        response = client.get("http://127.0.0.1:18080/" + path)
        responses[path] = {"http_status": response.status_code, "body": response.json()}
    observed = datetime.now(UTC).isoformat()
    inspected = {path: responses[path] for path in ("health", "ready", "work")}
    record = {
        "id": "real-work-response",
        "version": hashlib.sha256(json.dumps(inspected, sort_keys=True).encode()).hexdigest(),
        "updated_at": observed,
        "original_verdict": "pass" if responses["work"]["http_status"] == 200 else "fail",
        "facts": {
            "sample_kind": "real_response_snapshot",
            "scope": "Only work response assertions; no question-answer semantic assessment",
            "baseline": "HTTP status alone; not an existing business Agent verdict",
            **{key + "_http_status": value["http_status"] for key, value in inspected.items()},
            **{key: value["body"] for key, value in inspected.items()},
        },
        "evidence": [
            {"source": "GET http://testlab-business:8080/" + path, "observed_at": observed}
            for path in inspected
        ],
    }
    replays = []
    for identifier, original, ok in (
        ("replay-correct", "pass", True),
        ("replay-false-accept", "pass", False),
        ("replay-false-reject", "fail", True),
    ):
        row = copy.deepcopy(record)
        row.update(id=identifier, version="acceptance-v1", original_verdict=original)
        row["facts"]["sample_kind"] = "synthetic_acceptance_replay"
        row["facts"]["work"]["ok"] = ok
        row["evidence"] = [{"source": "labelled_acceptance_replay", "not_a_live_fault": True}]
        replays.append(row)
    # A healthy real observation is required to construct these known-answer controls.
    assert all(responses[path]["http_status"] == 200 for path in responses), "service_not_healthy"
    assert responses["work"]["body"].get("ok") is True, "business_response_not_successful"
    write("snapshot.json", {"observed": [record], "replays": replays})
    return responses["status"]["body"]


def rules():
    values = [
        ("process", "进程健康返回成功", "health_http_status", 200),
        ("ready", "业务就绪返回成功", "ready_http_status", 200),
        ("http", "工作请求返回成功", "work_http_status", 200),
        ("business", "业务成功标志为真", "work.ok", True),
    ]
    return [
        {
            "code": code,
            "title": title,
            "path": path,
            "kind": "equals",
            "expected": expected,
            "basis": "testlab/service/service.py：健康与就绪为 200，工作请求还须 ok=true。",
        }
        for code, title, path, expected in values
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reuse", action="store_true", help="Reuse this acceptance run's plans")
    execution = parser.add_mutually_exclusive_group()
    execution.add_argument(
        "--prepare-only", action="store_true", help="Save paused plans only (default)"
    )
    execution.add_argument(
        "--run-agent", action="store_true", help="Run one real Agent review after outbound approval"
    )
    args = parser.parse_args()
    args.prepare_only = not args.run_agent
    previous = {}
    if args.reuse:
        saved = json.loads((OUT / "progress.json").read_text(encoding="utf-8"))
        previous = {row["kind"]: row["id"] for row in saved["plans"]}
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        for filename in ("result.json", "snapshot.json"):
            write(
                "previous-" + stamp + "-" + filename,
                json.loads((OUT / filename).read_text(encoding="utf-8")),
            )
    created = []
    result = {"application_id": APPLICATION, "business_methods": ["GET"], "checks": {}}
    with httpx.Client(timeout=20, trust_env=False) as client:
        credentials = dict(
            line.split("=", 1)
            for line in (ROOT / ".local/admin-credentials.txt").read_text().splitlines()
            if "=" in line
        )

        def api(method, path, body=None):
            response = client.request(method, API + path, json=body)
            if response.status_code >= 400:
                raise RuntimeError(
                    f"platform_request_failed:{method}:{path}:{response.status_code}"
                )
            return response.json() if response.content else None

        token = api(
            "POST", "/auth/login", {key: credentials[key] for key in ("username", "password")}
        )
        client.headers["Authorization"] = "Bearer " + token["access_token"]
        app = api("GET", "/applications/" + APPLICATION)
        assert app["name"] == "实测问答服务" and app["enabled"], "unexpected_application"
        deployments = api("GET", f"/applications/{APPLICATION}/deployments?limit=100")
        environments = {
            row["environment"]
            for row in deployments
            if row["ended_at"] is None and row["compose_service"] == "business"
        }
        assert len(environments) == 1, "service_environment_not_known"
        environment = environments.pop()
        # Do not send the platform bearer token to the business service.
        with httpx.Client(timeout=10, trust_env=False) as business:
            before = capture(business)
        result["business_before"] = before
        subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(ROOT / "testlab/compose.inspections.yaml"),
                "up",
                "-d",
                "--wait",
                "--wait-timeout",
                "45",
            ],
            check=True,
            cwd=ROOT,
        )
        content = (
            (ROOT / "testlab/skills/qa-work-response-review/SKILL.md")
            .read_text(encoding="utf-8")
            .strip()
        )
        skill = next(
            (
                row
                for row in api("GET", "/skills?limit=100")
                if row["code"] == "testlab_qa_work_response_review"
            ),
            None,
        )
        if skill is None:
            skill = api(
                "POST",
                "/skills",
                {
                    "code": "testlab_qa_work_response_review",
                    "title": "实测问答：工作响应契约巡检",
                    "content": content,
                    "tags": ["inspection", "testlab"],
                    "enabled": True,
                },
            )
            skill = api(
                "PUT",
                f"/skills/{skill['id']}/validity",
                {
                    "expected_version": skill["version"],
                    "application_ids": [APPLICATION],
                },
            )
        assert skill["content"].strip() == content, "test_skill_changed"
        assert skill["application_ids"] == [APPLICATION], "test_skill_scope_changed"
        if not skill["enabled"]:
            skill = api(
                "PUT",
                f"/skills/{skill['id']}",
                {
                    **{key: skill[key] for key in ("code", "title", "content", "tags")},
                    "enabled": True,
                },
            )
        result["skill"] = {key: skill[key] for key in ("id", "code", "version", "application_ids")}
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        try:
            for kind, name, agent, interval, path in (
                ("real", "真实响应快照（Agent验收）", True, 3600, "/records"),
                ("replay", "误判回放（验收样例）", False, 60, "/acceptance-records"),
            ):
                body = {
                    "application_id": APPLICATION,
                    "name": f"实测问答·{name}·{stamp}",
                    "environment": environment,
                    "enabled": not args.prepare_only,
                    "interval_seconds": interval,
                    "lookback_seconds": 3600,
                    "max_records": 3,
                    "agent_enabled": agent,
                    "max_agent_items": 1,
                    "skill_id": skill["id"] if agent else None,
                    "source_kind": "api",
                    "source_config": {"url": SOURCE + path},
                    "rules": rules(),
                }
                identifier = previous.get(kind)
                if identifier:
                    current = next(
                        row
                        for row in api(
                            "GET", f"/inspection-plans?application_id={APPLICATION}&limit=100"
                        )
                        if row["id"] == identifier
                    )
                    assert not current["enabled"], "previous_plan_still_active"
                    body["expected_version"] = current["version"]
                plan = api(
                    "PUT" if identifier else "POST",
                    "/inspection-plans" + ("/" + identifier if identifier else ""),
                    body,
                )
                created.append(
                    {
                        "kind": kind,
                        "id": plan["id"],
                        "name": plan["name"],
                        "version": plan["version"],
                    }
                )
            result["plans"] = created
            write("progress.json", result)
            if args.prepare_only:
                result["prepared_only"] = True
                return
            print(
                json.dumps(
                    {"phase": "automatic_plans_enabled", "plans": created}, ensure_ascii=False
                ),
                flush=True,
            )
            deadline, last = time.monotonic() + 155, None
            while time.monotonic() < deadline:
                histories = {
                    row["kind"]: [
                        run
                        for run in api("GET", f"/inspection-runs?plan_id={row['id']}&limit=20")
                        if run["plan_version"] == row["version"]
                    ]
                    for row in created
                }
                progress = {
                    kind: [(row["status"], row["error_code"]) for row in runs]
                    for kind, runs in histories.items()
                }
                if progress != last:
                    print(
                        json.dumps({"phase": "running", "batches": progress}, ensure_ascii=False),
                        flush=True,
                    )
                    last = progress
                finished = {
                    kind: [row for row in runs if row["status"] not in ACTIVE]
                    for kind, runs in histories.items()
                }
                if finished["real"] and len(finished["replay"]) >= 2:
                    break
                time.sleep(5)
            result["runs"] = histories
            result["items"] = {
                row["id"]: api("GET", f"/inspection-runs/{row['id']}/items?limit=100")
                for runs in histories.values()
                for row in runs
            }
            real_runs = sorted(histories["real"], key=lambda row: row["created_at"])
            replay_runs = sorted(histories["replay"], key=lambda row: row["created_at"])
            assert real_runs and len(replay_runs) >= 2, "automatic_batches_not_observed"
            real_items = result["items"][real_runs[0]["id"]]
            replay_items = result["items"][replay_runs[0]["id"]]
            by_record = {row["record_id"]: row for row in replay_items}
            checks = result["checks"]
            checks["automatic_trigger"] = all(
                row["trigger"] == "automatic" for runs in histories.values() for row in runs
            )
            checks["real_agent_completed"] = (
                len(real_items) == 1 and real_items[0]["agent_status"] == "completed"
            )
            checks["real_response_supported"] = (
                len(real_items) == 1 and real_items[0]["verdict"] == "supported"
            )
            checks["control_supported"] = by_record["replay-correct"]["verdict"] == "supported"
            checks["false_accept_detected"] = (
                by_record["replay-false-accept"]["verdict"] == "contradicted"
            )
            checks["false_reject_detected"] = (
                by_record["replay-false-reject"]["verdict"] == "contradicted"
            )
            checks["second_batch_deduplicated"] = (
                replay_runs[1]["status"] == "completed"
                and replay_runs[1]["summary"]["unique_records"] == 0
                and replay_runs[1]["summary"]["duplicate_records"] == 3
            )
            for record_id, verdict in (
                ("replay-false-accept", "false_accept"),
                ("replay-false-reject", "false_reject"),
            ):
                item = by_record[record_id]
                api(
                    "POST",
                    f"/inspection-items/{item['id']}/reviews",
                    {
                        "verdict": verdict,
                        "comment": "验收脚本标注：已知构造的回放样例，非线上真实故障。",
                    },
                )
                reviews = api("GET", f"/inspection-items/{item['id']}/reviews")
                checks[verdict + "_review_saved"] = any(
                    row["verdict"] == verdict for row in reviews
                )
            with httpx.Client(timeout=10, trust_env=False) as business:
                after = business.get("http://127.0.0.1:18080/status").json()
            result["business_after"] = after
            checks["business_scenario_preserved"] = (
                before["scenario"] == after["scenario"] == "healthy"
            )
            checks["business_version_preserved"] = before["version"] == after["version"]
            checks["no_business_llm_calls_added"] = before["llm_calls"] == after["llm_calls"]
            assert all(checks.values()), "acceptance_checks_failed"
        finally:
            for plan in created:
                current = next(
                    row
                    for row in api("GET", "/inspection-plans?limit=100")
                    if row["id"] == plan["id"]
                )
                if current["enabled"]:
                    current = api(
                        "PUT",
                        f"/inspection-plans/{plan['id']}/state",
                        {
                            "expected_version": current["version"],
                            "enabled": False,
                        },
                    )
                plan["paused"] = not current["enabled"]
                paused = client.post(API + f"/inspection-plans/{plan['id']}/runs")
                plan["paused_manual_run_blocked"] = paused.status_code == 409
            write("prepared.json" if args.prepare_only else "result.json", result)
            print(
                json.dumps(
                    {
                        "phase": "prepared" if args.prepare_only else "finished",
                        "checks": result["checks"],
                        "plans": created,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )


if __name__ == "__main__":
    main()
