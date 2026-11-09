"""Batch 9 real fault/action path followed by reviewed report and local SMTP acceptance."""

import hashlib
import json
from urllib.request import Request, urlopen
from uuid import uuid4

from accept_ai import scenario
from accept_executions import compose, ready
from accept_executions import main as execution_acceptance
from accept_monitoring import ROOT, request, wait_for


def mailpit(path):
    with urlopen("http://127.0.0.1:8025/api/v1/" + path, timeout=15) as reply:
        return json.load(reply)


def report_steps(evidence, token):
    compose("up", "-d", "--no-build", "--wait", "report-worker", test=True)
    base = f"/api/v1/incidents/{evidence['incident_id']}"
    request(
        base + "/status",
        "POST",
        {"status": "closed", "comment": "本地恢复及健康验证完成，进入复盘"},
        token,
    )
    row = request(base + "/reports", "POST", {"force": True}, token)
    path = f"/api/v1/reports/{row['id']}"

    def generated():
        report = request(path, token=token)
        return report if report["status"] not in ("queued", "running") else None

    row = wait_for("real Harness snapshot-only postmortem", generated, seconds=90)
    assert row["status"] == "draft", row["error_code"]
    assert row["tool_calls"][-1]["runtime_tools"] == ["mcp__ops__get_postmortem_context"]
    assert {r["kind"] for r in row["snapshot"]["records"]} >= {
        "incident",
        "alert",
        "analysis",
        "approval",
        "execution",
        "audit",
        "timeline",
    }
    row = request(
        path,
        "PUT",
        {
            "expected_revision": 1,
            "content": {
                **row["content"],
                "summary": row["content"]["summary"] + "；管理员已核对本地证据。",
            },
        },
        token,
    )
    request(
        path + "/review",
        "POST",
        {
            "expected_revision": 2,
            "decision": "request_changes",
            "comment": "核对业务影响与告警区间的区别",
        },
        token,
    )
    row = request(
        path + "/review",
        "POST",
        {"expected_revision": 2, "decision": "approve", "comment": "证据和版本核对完成"},
        token,
    )
    directory = ROOT / ".local/batch10-exports"
    directory.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for format in ("pdf", "docx"):
        with urlopen(
            Request(
                "http://127.0.0.1:8000" + path + "/export?format=" + format,
                headers={"Authorization": "Bearer " + token},
            ),
            timeout=30,
        ) as reply:
            payload = reply.read()
        (directory / f"postmortem.{format}").write_bytes(payload)
        hashes[format] = hashlib.sha256(payload).hexdigest()
    body = {
        "expected_revision": 2,
        "recipients": ["batch10-reviewer@example.test"],
        "comment": "显式授权仅发送到本地 Mailpit",
        "idempotency_key": str(uuid4()),
    }
    delivery = request(path + "/send", "POST", body, token)
    assert request(path + "/send", "POST", body, token)["id"] == delivery["id"]

    def sent():
        result = request(path + "/deliveries", token=token)[0]
        return result if result["status"] not in ("queued", "sending") else None

    delivery = wait_for("explicit report SMTP delivery", sent, seconds=60)
    assert delivery["status"] == "sent", delivery["error_code"]

    def messages():
        result = mailpit("search?query=to:batch10-reviewer@example.test")
        return [m for m in result["messages"] if m["Subject"] == delivery["subject"]]

    received = wait_for("Mailpit receives reviewed PDF", messages, seconds=30)
    assert len(received) == 1
    message = mailpit("message/" + received[0]["ID"])
    attachment = message["Attachments"][0]
    with urlopen(
        "http://127.0.0.1:8025/api/v1/message/"
        + message["ID"]
        + "/part/"
        + str(attachment["PartID"]),
        timeout=15,
    ) as reply:
        attachment_bytes = reply.read()
    assert hashlib.sha256(attachment_bytes).hexdigest() == delivery["attachment_hash"]
    assert delivery["attachment_hash"] == hashes["pdf"]
    scenario("401")
    failed = request(base + "/reports", "POST", {"force": True}, token)
    failed_path = f"/api/v1/reports/{failed['id']}"

    def provider_failed():
        result = request(failed_path, token=token)
        return result if result["status"] not in ("queued", "running") else None

    failure = wait_for("report Provider rejection", provider_failed, seconds=60)
    assert failure["status"] == "failed" and failure["error_code"] == "authentication_failed"
    scenario("normal")
    compose("restart", "backend", "report-worker", test=True)
    wait_for("report API ready after restart", ready, seconds=90)
    assert request(path, token=token) == row
    assert len(request(path + "/revisions", token=token)) == 2
    assert request(path + "/send", "POST", body, token)["id"] == delivery["id"]
    assert request(path + "/deliveries", token=token)[0]["status"] == "sent"
    assert len(messages()) == 1
    logs = request(f"/api/v1/audit-logs?report_id={row['id']}&limit=100", token=token)
    assert {r["action"] for r in logs} >= {
        "report_generated",
        "report_edited",
        "report_changes_requested",
        "report_approved",
        "report_mail_queued",
        "report_mail_sent",
    }
    evidence.update(
        {
            "postmortem": row,
            "delivery": delivery,
            "report_audit": logs,
            "exports": hashes,
            "mailpit_attachment_matches": True,
            "report_restart_persistence": True,
            "mail_delivered_once": True,
            "provider_failure": failure["error_code"],
        }
    )
    print(
        "Postmortem generation, edit, review, exports, SMTP and restart persistence passed",
        flush=True,
    )


if __name__ == "__main__":
    execution_acceptance(after_execution=report_steps, batch=10)
