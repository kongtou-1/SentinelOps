"""Batch 5 end-to-end acceptance against the Compose local-test provider."""

import json
import re
from datetime import UTC, datetime

from accept_monitoring import ROOT, request, wait_for

MOCK = "http://127.0.0.1:18002"
MAIL = "http://127.0.0.1:8025"


def email(messages, baseline, event, status):
    start = int(datetime.fromisoformat(event["starts_at"]).timestamp())
    for message in messages:
        if message["ID"] in baseline or status.upper() not in message["Subject"]:
            continue
        if event["labels"]["alertname"] not in message["Subject"]:
            continue
        detail = request(f"/api/v1/message/{message['ID']}", base=MAIL)
        body = detail.get("HTML", "") + detail.get("Text", "")
        if all(
            value in body
            for value in (
                event["llm_endpoint_id"],
                f"event-start={start}",
                event["labels"]["error_type"],
            )
        ):
            return {"id": message["ID"], "subject": message["Subject"]}
    return None


def main():
    credentials = {}
    for line in (ROOT / ".local/admin-credentials.txt").read_text(encoding="utf-8").splitlines():
        match = re.match(r"(username|password)\s*[:=]\s*(.+)", line)
        if match:
            credentials[match[1]] = match[2]
    token = request("/api/v1/auth/login", "POST", credentials)["access_token"]
    service_token = (ROOT / ".local/alertmanager.token").read_text().strip()
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "provider": "local HTTP simulation",
        "external_deepseek_verified": False,
        "cases": [],
    }
    body = {
        "name": "local-llm-test",
        "base_url": "http://test-llm:8002",
        "model": "local-test-model",
        "api_key": "dummy-key-for-compose-acceptance",
        "probe_enabled": True,
    }
    existing = next(
        (e for e in request("/api/v1/llm-endpoints", token=token) if e["name"] == body["name"]),
        None,
    )
    endpoint = request(
        "/api/v1/llm-endpoints" + (f"/{existing['id']}" if existing else ""),
        "PUT" if existing else "POST",
        body,
        token,
    )
    endpoint_id = endpoint["id"]
    endpoint_path = f"/api/v1/llm-endpoints/{endpoint_id}"
    report["endpoint_id"] = endpoint_id
    cases = [
        (401, "authentication_failed"),
        (402, "insufficient_balance"),
        (429, "rate_limited"),
        (503, "service_error"),
    ]
    try:
        request("/scenario", "POST", {"status": 200}, base=MOCK)
        report["scheduled_probe"] = wait_for(
            "automatic real HTTP inference",
            lambda: e if (e := request(endpoint_path, token=token))["last_probe_success"] else None,
            90,
        )["last_probe_at"]
        rules = request("/api/v1/alert-rules", token=token)
        for _, error in cases:
            code = "LocalLLM_" + error
            rule_body = {
                "code": code,
                "llm_endpoint_id": endpoint_id,
                "metric": "llm_" + error,
                "threshold": 0,
                "for_seconds": 0,
                "severity": "warning",
                "enabled": True,
            }
            existing_rule = next((r for r in rules if r["code"] == code), None)
            request(
                "/api/v1/alert-rules" + (f"/{existing_rule['id']}" if existing_rule else ""),
                "PUT" if existing_rule else "POST",
                rule_body,
                token,
            )
        report["applied"] = request("/api/v1/monitoring/apply-rules", "POST", token=token)
        for status, error in cases:
            previous_ids = {
                e["id"]
                for e in request(f"/api/v1/alerts?llm_endpoint_id={endpoint_id}", token=token)
            }
            baseline = {m["ID"] for m in request("/api/v1/messages", base=MAIL)["messages"]}
            request("/scenario", "POST", {"status": status}, base=MOCK)
            result = request(endpoint_path + "/probe", "POST", token=token)
            assert (
                not result["success"]
                and result["error_type"] == error
                and result["http_status"] == status
            )

            def event():
                return next(
                    (
                        e
                        for e in request(
                            f"/api/v1/alerts?llm_endpoint_id={endpoint_id}", token=token
                        )
                        if e["id"] not in previous_ids
                        and e["status"] == "firing"
                        and e["labels"].get("error_type") == error
                    ),
                    None,
                )

            firing = wait_for(f"{error} event", event, 90)
            firing_mail = wait_for(
                f"{error} new firing email",
                lambda: email(
                    request("/api/v1/messages", base=MAIL)["messages"], baseline, firing, "firing"
                ),
                90,
            )
            metric = request(endpoint_path + "/metrics?metric=llm_" + error, token=token)
            assert any(float(r["value"][1]) == 1 for r in metric["result"])
            payload = {
                "alerts": [
                    {
                        "fingerprint": firing["fingerprint"],
                        "status": "firing",
                        "startsAt": firing["starts_at"],
                        "endsAt": "0001-01-01T00:00:00Z",
                        "labels": firing["labels"],
                        "annotations": firing["annotations"],
                    }
                ]
            }
            assert (
                request("/api/v1/hooks/alertmanager", "POST", payload, service_token)["changed"]
                == 0
            )
            request("/scenario", "POST", {"status": 200}, base=MOCK)
            assert request(endpoint_path + "/probe", "POST", token=token)["success"]
            resolved = wait_for(
                f"{error} recovery",
                lambda: (
                    e
                    if (e := request(f"/api/v1/alerts/{firing['id']}", token=token))["status"]
                    == "resolved"
                    else None
                ),
                90,
            )
            recovery_mail = wait_for(
                f"{error} new recovery email",
                lambda: email(
                    request("/api/v1/messages", base=MAIL)["messages"],
                    baseline,
                    resolved,
                    "resolved",
                ),
                90,
            )
            report["cases"].append(
                {
                    "http_status": status,
                    "error_type": error,
                    "event_id": firing["id"],
                    "firing_email": firing_mail,
                    "recovery_email": recovery_mail,
                    "duplicate_changed": 0,
                    "recovered": True,
                }
            )
            print(
                f"Validated HTTP {status}: {error}, alert, duplicate, recovery, new emails",
                flush=True,
            )
        report["metrics"] = {}
        for metric in (
            "llm_success_rate",
            "llm_error_rate",
            "llm_latency_avg_seconds",
            "llm_latency_p95_seconds",
        ):
            result = request(endpoint_path + "/metrics?metric=" + metric, token=token)
            assert result["result"] and any(r["value"][1] is not None for r in result["result"])
            report["metrics"][metric] = result
        report["passed"] = True
        print(
            "Batch 5 local end-to-end acceptance passed; external DeepSeek not tested", flush=True
        )
    finally:
        request("/scenario", "POST", {"status": 200}, base=MOCK)
        body.pop("api_key")
        body["probe_enabled"] = False
        request(endpoint_path, "PUT", body, token)
        request("/api/v1/monitoring/apply-rules", "POST", token=token)
        (ROOT / ".local/batch5-acceptance.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
