"""Run a bounded live QA experiment using the platform's interval scheduler.

Uses the existing local admin credential file without logging credentials.
Exactly five /ask requests are attempted; only the inspection Agent writes checks.
"""

import argparse
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
PLAN = "7fc88a6d-bee9-45f2-914a-f1455f53e291"
ACTIVE = {"queued", "collecting", "evaluating"}
QUESTIONS = [
    "请计算：一件商品原价 240 元，先打八折，再减 30 元，最终支付多少？请写出计算步骤。",
    "请严格按要求回答：只输出一个 JSON 对象，包含 city 和 country 两个字段，说明巴黎所在的国家，不要加 Markdown。",
    "有人说所有质数都是奇数。这个说法对吗？请给出反例，并说明什么是质数。",
    "商店规则是购买七天内可无理由退货，超过七天仅质量问题可退。顾客购买十天后因不喜欢颜色要求退货，商品没有质量问题。按规则能否退货？请解释。",
    "一台服务器 CPU 使用率很高，但暂时没有进程、日志或历史指标。能否直接认定为内存泄漏？请说明依据，以及应先收集的三类信息。",
]


def now():
    return datetime.now(UTC).isoformat()


class Experiment:
    def __init__(self, args):
        self.args = args
        self.out = args.output.resolve()
        self.out.mkdir(parents=True, exist_ok=False)
        self.platform = httpx.Client(base_url=args.api, timeout=20, trust_env=False)
        self.qa = httpx.Client(base_url=args.qa, timeout=180, trust_env=False)
        self.state = {
            "pid": os.getpid(),
            "plan_id": PLAN,
            "status": "preparing",
            "questions": [],
            "errors": [],
        }
        self.before = None
        self.version = None
        self.baseline_runs = set()

    def save(self, name, value):
        target = self.out / name
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(target)

    def event(self, kind, **values):
        entry = {"at": now(), "event": kind, **values}
        with (self.out / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(json.dumps(entry, ensure_ascii=False), flush=True)
        self.save("progress.json", self.state)

    def login(self):
        credentials = dict(
            line.split("=", 1)
            for line in (ROOT / ".local/admin-credentials.txt").read_text().splitlines()
            if "=" in line
        )
        reply = self.platform.post(
            "/auth/login", json={k: credentials[k] for k in ("username", "password")}
        )
        reply.raise_for_status()
        self.platform.headers["Authorization"] = (
            "Bearer " + reply.json()["access_token"]
        )

    def api(self, method, path, body=None):
        reply = self.platform.request(method, path, json=body)
        if reply.status_code == 401:
            self.login()
            reply = self.platform.request(method, path, json=body)
        if reply.is_error:
            raise RuntimeError(f"platform:{method}:{path}:{reply.status_code}")
        return reply.json() if reply.content else None

    def runs(self):
        return self.api("GET", f"/inspection-runs?plan_id={PLAN}&limit=100")

    def body(self, plan):
        # Filter response-only fields using the repository's API contract.
        schema = json.loads(
            (ROOT / "docs/backend/openapi.json").read_text(encoding="utf-8")
        )
        fields = schema["components"]["schemas"]["InspectionPlanInput"]["properties"]
        return {
            key: value
            for key, value in plan.items()
            if key in fields and key != "headers"
        }

    def prepare(self):
        self.login()
        self.qa.get("/health").raise_for_status()
        self.before = self.api("GET", f"/inspection-plans/{PLAN}")
        assert not self.before["enabled"], "existing_task_must_be_paused"
        assert self.before["execution_mode"] == "checks", "unexpected_task_mode"
        assert (
            self.before["source_config"]["url"]
            == "http://testlab-llm-chat:8080/records"
        )
        runs = self.runs()
        assert not any(run["status"] in ACTIVE for run in runs), "existing_run_active"
        self.baseline_runs = {run["id"] for run in runs}
        self.save("plan-before.json", self.before)
        self.save("runs-before.json", runs)
        reply = self.qa.get("/records", params={"checked": "false", "limit": 50})
        reply.raise_for_status()
        self.save("pending-before.json", reply.json())
        # Refuse an unrelated backlog rather than silently adding model calls.
        assert not reply.json()["items"], "existing_unchecked_backlog"
        self.original_body = self.body(self.before)
        body = dict(
            self.original_body,
            enabled=True,
            schedule_kind="interval",
            interval_seconds=self.args.interval,
            expected_version=self.before["version"],
        )
        self.version = self.before["version"] + 1
        configured = self.api("PUT", f"/inspection-plans/{PLAN}", body)
        self.version = configured["version"]
        self.save("plan-running.json", configured)
        self.started = time.monotonic()
        self.deadline = self.started + self.args.duration
        self.state.update(
            status="running",
            started_at=now(),
            ends_at=(
                datetime.now(UTC) + timedelta(seconds=self.args.duration)
            ).isoformat(),
            interval_seconds=self.args.interval,
            question_offsets_seconds=[i * self.args.duration / 5 for i in range(5)],
        )
        self.event("started", next_run_at=configured["next_run_at"])

    def observe(self):
        current = [run for run in self.runs() if run["id"] not in self.baseline_runs]
        self.save("runs-latest.json", current)
        self.state["runs"] = [
            {k: run[k] for k in ("id", "status", "trigger", "collected", "error_code")}
            for run in current
        ]
        self.event("scheduler_observed", count=len(current))

    def feed(self):
        next_observation = self.started + 90
        index = 0
        while time.monotonic() < self.deadline:
            elapsed = time.monotonic() - self.started
            if index < 5 and elapsed >= index * self.args.duration / 5:
                sample = {
                    "number": index + 1,
                    "question": QUESTIONS[index],
                    "sent_at": now(),
                    "offset_seconds": round(elapsed, 2),
                }
                self.state["questions"].append(sample)
                self.event("question_sending", number=index + 1)
                try:
                    reply = self.qa.post("/ask", json={"question": QUESTIONS[index]})
                    reply.raise_for_status()
                    sample.update(response=reply.json(), received_at=now())
                    self.event(
                        "question_saved",
                        number=index + 1,
                        record_id=sample["response"]["id"],
                    )
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    sample["error"] = type(exc).__name__
                    self.state["errors"].append(
                        f"question_{index + 1}:{type(exc).__name__}"
                    )
                    self.event(
                        "question_failed", number=index + 1, error=type(exc).__name__
                    )
                index += 1
            if time.monotonic() >= next_observation:
                self.observe()
                next_observation = time.monotonic() + self.args.interval
            next_question = (
                self.started + index * self.args.duration / 5
                if index < 5
                else self.deadline
            )
            time.sleep(
                max(
                    0,
                    min(self.deadline, next_question, next_observation)
                    - time.monotonic(),
                )
            )

    def restore(self):
        if self.version is None:
            return
        current = self.api("GET", f"/inspection-plans/{PLAN}")
        if current["version"] != self.version:
            raise RuntimeError("task_changed_by_another_user_restore_skipped")
        paused = self.api(
            "PUT",
            f"/inspection-plans/{PLAN}/state",
            {"enabled": False, "expected_version": self.version},
        )
        self.version = paused["version"]
        self.event("polling_paused")
        # Pausing prevents dispatch. Preserve a last in-flight check until it finishes.
        drain_deadline = time.monotonic() + self.before["timeout_seconds"] + 30
        while any(run["status"] in ACTIVE for run in self.runs()):
            if time.monotonic() >= drain_deadline:
                raise RuntimeError("active_run_did_not_finish_restore_skipped")
            time.sleep(15)
        restored = self.api(
            "PUT",
            f"/inspection-plans/{PLAN}",
            dict(self.original_body, expected_version=self.version),
        )
        self.save("plan-restored.json", restored)
        self.state["restored"] = not restored["enabled"] and all(
            restored[k] == self.before[k]
            for k in (
                "schedule_kind",
                "interval_seconds",
                "check_items",
                "data_source_id",
            )
        )
        self.event("configuration_restored")

    def report(self):
        runs = [run for run in self.runs() if run["id"] not in self.baseline_runs]
        self.save("runs-final.json", runs)
        results = []
        for run in runs:
            checks = self.api(
                "GET", f"/inspection-runs/{run['id']}/check-results?limit=100"
            )
            items = self.api("GET", f"/inspection-runs/{run['id']}/items?limit=100")
            traces = (
                self.api("GET", f"/inspection-runs/{run['id']}/traces?limit=100")
                if checks
                else []
            )
            results.append(
                {
                    "run_id": run["id"],
                    "checks": checks,
                    "items": items,
                    "traces": traces,
                }
            )
        self.save("inspection-evidence.json", results)
        records = []
        for sample in self.state["questions"]:
            if "response" in sample:
                reply = self.qa.get(
                    "/records", params={"record_id": sample["response"]["id"]}
                )
                reply.raise_for_status()
                records.extend(reply.json()["items"])
        self.save("qa-final.json", records)
        self.state.update(
            finished_at=now(),
            automatic_runs=sum(run["trigger"] == "automatic" for run in runs),
            run_count=len(runs),
            checked_count=sum(bool(row["facts"].get("checked")) for row in records),
        )
        checks = [check for result in results for check in result["checks"]]
        self.state["check_count"] = len(checks)
        self.state["passed"] = (
            len(records) == 5
            and self.state["checked_count"] == 5
            and self.state["automatic_runs"] >= 2
            and len(checks) == 5
            and all(
                run["status"] == "completed" and not run["error_code"] for run in runs
            )
            and all(check["execution_status"] == "completed" for check in checks)
            and self.state.get("restored") is True
            and not self.state["errors"]
        )
        self.state["status"] = (
            "completed" if self.state["passed"] else "needs_attention"
        )
        self.save("summary.json", self.state)
        self.event(
            "finished",
            passed=self.state["passed"],
            checked=self.state["checked_count"],
            automatic_runs=self.state["automatic_runs"],
        )

    def run(self):
        try:
            self.prepare()
            self.feed()
        except (
            httpx.HTTPError,
            OSError,
            ValueError,
            KeyError,
            RuntimeError,
            AssertionError,
        ) as exc:
            self.state["errors"].append(
                str(exc)
                if isinstance(exc, (RuntimeError, AssertionError))
                else type(exc).__name__
            )
            self.event("experiment_error", error=self.state["errors"][-1])
        finally:
            try:
                self.restore()
                self.report()
            except (
                httpx.HTTPError,
                OSError,
                ValueError,
                KeyError,
                RuntimeError,
            ) as exc:
                self.state.update(
                    status="needs_attention",
                    cleanup_error=str(exc)
                    if isinstance(exc, RuntimeError)
                    else type(exc).__name__,
                )
                self.save("summary.json", self.state)
                self.event("cleanup_or_report_error", error=self.state["cleanup_error"])
            self.platform.close()
            self.qa.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:18088/api/v1")
    parser.add_argument("--qa", default="http://127.0.0.1:18081")
    parser.add_argument("--duration", type=int, default=3600)
    parser.add_argument("--interval", type=int, default=120)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 60 <= args.interval <= args.duration or args.duration > 3600:
        parser.error("Require 60 <= interval <= duration <= 3600")
    Experiment(args).run()
