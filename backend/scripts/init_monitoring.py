"""Generate local secrets and monitoring config; never print or replace encryption keys."""

import secrets
from pathlib import Path

import yaml
from cryptography.fernet import Fernet
from dotenv import dotenv_values

root = Path(__file__).resolve().parents[2]
local = root / ".local"
config = local / "monitoring"
rules = config / "rules"
rules.mkdir(parents=True, exist_ok=True)

for name, value in (
    ("credential.key", Fernet.generate_key().decode()),
    ("monitoring.token", secrets.token_urlsafe(48)),
    ("alertmanager.token", secrets.token_urlsafe(48)),
):
    path = local / name
    if not path.exists():
        with path.open("x", encoding="utf-8") as file:
            file.write(value + "\n")
    path.chmod(0o600)

sd = [
    {
        "url": "http://backend:8000/api/v1/monitoring/targets",
        "refresh_interval": "15s",
        "authorization": {"type": "Bearer", "credentials_file": "/run/secrets/monitoring_token"},
    }
]
scrapes = []
scrapes.append(
    {
        "job_name": "platform",
        "metrics_path": "/metrics",
        "scrape_interval": "15s",
        "authorization": {"type": "Bearer", "credentials_file": "/run/secrets/monitoring_token"},
        "static_configs": [{"targets": ["backend:8000"]}],
    }
)
for job in ("node", "cadvisor", "blackbox", "docker-state", "service-traffic"):
    scrape = {
        "job_name": job,
        "http_sd_configs": sd,
        "relabel_configs": [{"source_labels": ["collector"], "regex": job, "action": "keep"}],
    }
    if job == "blackbox":
        scrape.update({"metrics_path": "/probe", "params": {"module": ["http_2xx"]}})
        scrape["relabel_configs"] += [
            {"source_labels": ["__address__"], "target_label": "__param_target"},
            {"source_labels": ["__param_target"], "target_label": "instance"},
            {"target_label": "__address__", "replacement": "blackbox:9115"},
        ]
    scrapes.append(scrape)

scrapes.append(
    {
        "job_name": "service-probe",
        "metrics_path": "/api/v1/monitoring/service-probe-metrics",
        "scrape_interval": "15s",
        "authorization": {"type": "Bearer", "credentials_file": "/run/secrets/monitoring_token"},
        "static_configs": [{"targets": ["backend:8000"]}],
    }
)

scrapes.append(
    {
        "job_name": "predictions",
        "metrics_path": "/api/v1/monitoring/prediction-metrics",
        "scrape_interval": "15s",
        "authorization": {"type": "Bearer", "credentials_file": "/run/secrets/monitoring_token"},
        "static_configs": [{"targets": ["backend:8000"]}],
    }
)

scrapes.append(
    {
        "job_name": "llm-probe",
        "metrics_path": "/api/v1/monitoring/llm-metrics",
        "scrape_interval": "15s",
        "authorization": {"type": "Bearer", "credentials_file": "/run/secrets/monitoring_token"},
        "static_configs": [{"targets": ["backend:8000"]}],
    }
)

scrapes.append(
    {
        "job_name": "inventory",
        "metrics_path": "/api/v1/monitoring/container-metrics",
        "scrape_timeout": "30s",
        "scrape_interval": "30s",
        "authorization": {"type": "Bearer", "credentials_file": "/run/secrets/monitoring_token"},
        "static_configs": [{"targets": ["backend:8000"]}],
    }
)

prometheus = {
    "global": {"scrape_interval": "15s", "evaluation_interval": "15s"},
    "rule_files": ["/etc/prometheus/platform-rules/*.yml"],
    "alerting": {"alertmanagers": [{"static_configs": [{"targets": ["alertmanager:9093"]}]}]},
    "scrape_configs": scrapes,
}
env = dotenv_values(root / ".env")
smtp_password = env.get("SMTP_PASSWORD")
smtp = {
    "to": env.get("SMTP_TO") or "operator@example.test",
    "from": env.get("SMTP_FROM") or "alerts@example.test",
    "smarthost": env.get("SMTP_ADDRESS") or "mailpit:1025",
    "require_tls": (env.get("SMTP_REQUIRE_TLS") or "false").lower() == "true",
    "send_resolved": True,
    "html": (
        "{{ range .Alerts }}<p>{{ .Status }} {{ .Labels.alertname }} "
        "server={{ .Labels.server_id }} container={{ .Labels.container_id }} "
        "endpoint={{ .Labels.endpoint_id }} error={{ .Labels.error_type }} "
        "prediction={{ .Labels.task_id }} metric={{ .Annotations.metric }} "
        "horizon={{ .Labels.horizon_hours }} breach={{ .Annotations.first_breach }} "
        "quality={{ .Annotations.model_quality }} model={{ .Annotations.model }} "
        "event-start={{ .StartsAt.Unix }}</p>{{ end }}"
        "<p>resolved 仅表示告警通知结束；资源恢复以平台恢复核验结果为准。</p>"
    ),
}
if env.get("SMTP_USERNAME"):
    smtp["auth_username"] = env["SMTP_USERNAME"]
if smtp_password:
    (config / "smtp.password").write_text(smtp_password, encoding="utf-8")
    (config / "smtp.password").chmod(0o600)
    smtp["auth_password_file"] = "/run/secrets/smtp_password"
else:
    (config / "smtp.password").unlink(missing_ok=True)
alertmanager = {
    "route": {
        "receiver": "platform",
        "group_by": [
            "alertname",
            "server_id",
            "container_id",
            "endpoint_id",
            "task_id",
            "horizon_hours",
        ],
        "group_wait": "5s",
        "group_interval": "15s",
        "repeat_interval": "4h",
    },
    "receivers": [
        {
            "name": "platform",
            "webhook_configs": [
                {
                    "url": "http://backend:8000/api/v1/hooks/alertmanager",
                    "send_resolved": True,
                    "max_alerts": 1000,
                    "http_config": {
                        "authorization": {
                            "type": "Bearer",
                            "credentials_file": "/run/secrets/alertmanager_token",
                        }
                    },
                }
            ],
            "email_configs": [smtp],
        }
    ],
}
for name, data in (("prometheus.yml", prometheus), ("alertmanager.yml", alertmanager)):
    (config / name).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    (config / name).chmod(0o644)
if not (rules / "platform.yml").exists():
    (rules / "platform.yml").write_text("groups: []\n", encoding="utf-8")
print("Local monitoring configuration initialized; existing keys and rules preserved")
