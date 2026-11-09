"""Validate the fixed query catalog and notification-end behavior with real promtool."""

import json
import subprocess
import sys
from pathlib import Path
from typing import get_args

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    from app.schemas.resources import Metric
    from app.services.monitoring.metrics import llm_metric_expression, metric_expression

    directory = ROOT / ".local/promtool-completion"
    directory.mkdir(parents=True, exist_ok=True)
    rules = [
        {"alert": "Catalog_" + metric, "expr": metric_expression(metric) + " > 0"}
        for metric in get_args(Metric)
    ]
    cases = [
        {"alert": "LLMFailure", "expr": "(" + llm_metric_expression("llm_up", "e") + ") < 1"},
        {"alert": "PredictionRisk", "expr": 'prediction_risk{task_id="p",horizon_hours="1"} > 0'},
        {"alert": "HTTPDelay", "expr": 'probe_duration_seconds{server_id="s"} > 0.2'},
        {"alert": "DisabledRule", "expr": 'probe_duration_seconds{server_id="disabled"} > 0.2'},
        {"alert": "RetiredResource", "expr": 'probe_duration_seconds{server_id="retired"} > 0.2'},
    ]
    (directory / "catalog.yml").write_text(
        yaml.safe_dump({"groups": [{"name": "catalog", "rules": rules}]}), encoding="utf-8"
    )
    (directory / "recovery.yml").write_text(
        yaml.safe_dump({"groups": [{"name": "recovery", "rules": cases}]}), encoding="utf-8"
    )
    inputs = [
        {
            "series": 'llm_probe_success{job="llm-probe",endpoint_id="e",source_epoch="epoch"}',
            "values": "0+0x19 1+0x4",
        },
        {
            "series": 'llm_probe_last_run_timestamp_seconds{job="llm-probe",'
            'endpoint_id="e",source_epoch="epoch"}',
            "values": "0+0x19 300+0x4",
        },
        {
            "series": 'llm_probe_interval_seconds{job="llm-probe",'
            'endpoint_id="e",source_epoch="epoch"}',
            "values": "60+0x24",
        },
        {
            "series": 'llm_probe_has_result{job="llm-probe",endpoint_id="e",source_epoch="epoch"}',
            "values": "1+0x24",
        },
        {
            "series": 'prediction_risk{task_id="p",horizon_hours="1"}',
            "values": "2+0x7 stale _ _ _ _ _ _ _ _ _ _ _ 0+0x4",
        },
        {
            "series": 'probe_duration_seconds{server_id="s"}',
            "values": "0.4+0x7 stale _ _ _ _ _ _ _ _ _ _ _ 0.01+0x4",
        },
        {"series": 'probe_duration_seconds{server_id="disabled"}', "values": "0.4+0x7 stale"},
        {"series": 'probe_duration_seconds{server_id="retired"}', "values": "0.4+0x7 stale"},
    ]
    labels = {
        "LLMFailure": {"job": "llm-probe", "endpoint_id": "e", "source_epoch": "epoch"},
        "PredictionRisk": {"task_id": "p", "horizon_hours": "1"},
        "HTTPDelay": {"server_id": "s"},
        "DisabledRule": {"server_id": "disabled"},
        "RetiredResource": {"server_id": "retired"},
    }
    tests = []
    for at in ("1m", "3m", "5m"):
        for case in cases:
            name = case["alert"]
            tests.append(
                {
                    "eval_time": at,
                    "alertname": name,
                    "exp_alerts": [{"exp_labels": labels[name], "exp_annotations": {}}]
                    if at == "1m"
                    else [],
                }
            )
    (directory / "test.yml").write_text(
        yaml.safe_dump(
            {
                "rule_files": ["recovery.yml"],
                "evaluation_interval": "15s",
                "tests": [{"interval": "15s", "input_series": inputs, "alert_rule_test": tests}],
            }
        ),
        encoding="utf-8",
    )
    commands = [
        ["cp", str(directory), "prometheus:/tmp/promtool-completion"],
        [
            "exec",
            "-T",
            "prometheus",
            "promtool",
            "check",
            "rules",
            "/tmp/promtool-completion/catalog.yml",
            "/etc/prometheus/platform-rules/platform.yml",
        ],
        [
            "exec",
            "-T",
            "prometheus",
            "promtool",
            "test",
            "rules",
            "/tmp/promtool-completion/test.yml",
        ],
    ]
    for command in commands:
        subprocess.run(["docker", "compose", *command], cwd=ROOT, check=True)
    (directory / "result.json").write_text(
        json.dumps(
            {
                "passed": True,
                "catalog_metrics": len(rules),
                "notification_scenarios": list(labels),
                "note": "Missing/disabled/retired signals end notification; resource recovery "
                "is separately checked by backend regression and local acceptance.",
            },
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
