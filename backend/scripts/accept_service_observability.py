"""Isolated Docker/Prometheus/Alertmanager acceptance; never uses a business database."""

import json
import secrets
import shutil
import subprocess
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import mkdtemp
from uuid import UUID

import httpx2
import yaml
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text

from app.core.config import ROOT, get_settings
from app.core.security import password_hasher
from app.db.session import SessionLocal, engine
from app.main import app
from app.models import AgentAnalysis, AlertEvent, Role, Server, User
from app.services.diagnosis.analyses import complete
from app.services.diagnosis.tools import DiagnosticTools
from app.services.monitoring.recovery import reconcile

PROJECT = "sentinelops-observability-acceptance"
DATABASE = "ops_service_observability_acceptance_test"


def wait_until(check, message, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = check()
        except httpx2.HTTPError:
            value = None
        if value:
            return value
        time.sleep(0.5)
    raise AssertionError(message)


def main():
    settings = get_settings()
    assert settings.postgres_db == DATABASE and DATABASE.endswith("_test")
    directory = Path(mkdtemp(prefix="service-observability-", dir=ROOT / ".local"))
    report = {
        "passed": False,
        "database": DATABASE,
        "project": PROJECT,
        "stages": [],
        "cleanup": False,
    }
    created = False
    bridge = client = None
    bridge_thread = None
    traffic_stop = threading.Event()
    admin_engine = create_engine(
        settings.database_url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        hide_parameters=True,
    )

    def docker(*args):
        result = subprocess.run(
            ["docker", "compose", "-p", PROJECT, "-f", str(directory / "compose.yaml"), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode:
            raise RuntimeError("Docker acceptance command failed: " + result.stderr[-2000:])
        return result.stdout.strip()

    def stage(name, **facts):
        report["stages"].append({"name": name, "at": datetime.now(UTC).isoformat(), **facts})
        print(name, flush=True)

    try:
        with admin_engine.connect() as connection:
            if connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname=:name"), {"name": DATABASE}
            ):
                raise RuntimeError("The dedicated acceptance database already exists")
            connection.execute(text(f'CREATE DATABASE "{DATABASE}"'))
            created = True
        command.upgrade(Config(str(ROOT / "backend/alembic.ini")), "head")
        for name, value in (
            ("credential.key", Fernet.generate_key().decode()),
            ("monitoring.token", secrets.token_urlsafe(32)),
            ("alertmanager.token", secrets.token_urlsafe(32)),
            ("ai.key", "acceptance-fake-key-" + secrets.token_hex(24)),
        ):
            (directory / name).write_text(value, encoding="utf-8")
        settings.credential_key_file = directory / "credential.key"
        settings.monitoring_token_file = directory / "monitoring.token"
        settings.alertmanager_token_file = directory / "alertmanager.token"
        settings.ai_key_file = directory / "ai.key"
        settings.ai_enabled = True
        settings.service_probe_scheduler_enabled = False
        settings.llm_probe_scheduler_enabled = False
        settings.monitoring_rules_dir = directory / "rules"
        settings.monitoring_rules_dir.mkdir()
        monitoring_token = settings.monitoring_token_file.read_text()
        alertmanager_token = settings.alertmanager_token_file.read_text()
        password = secrets.token_urlsafe(24)
        with SessionLocal() as db:
            role = db.scalar(select(Role).where(Role.code == "admin"))
            db.add(
                User(
                    username="accept-admin",
                    password_hash=password_hasher.hash(password),
                    role_id=role.id,
                )
            )
            db.commit()
        client = TestClient(app)
        client.__enter__()
        headers = {
            "Authorization": "Bearer "
            + client.post(
                "/api/v1/auth/login", json={"username": "accept-admin", "password": password}
            ).json()["access_token"]
        }
        service_headers = {"Authorization": "Bearer " + monitoring_token}
        metrics_available = {"value": True}
        errors = []

        class Bridge(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.headers.get("Authorization") != "Bearer " + monitoring_token:
                    return self.reply(401, b"")
                if self.path == "/probe-metrics" and not metrics_available["value"]:
                    return self.reply(503, b"")
                endpoint = (
                    "/api/v1/monitoring/service-probe-metrics"
                    if self.path == "/probe-metrics"
                    else "/api/v1/monitoring/targets"
                )
                response = client.get(endpoint, headers=service_headers)
                self.reply(
                    response.status_code,
                    response.content,
                    response.headers.get("content-type", "text/plain"),
                )

            def do_POST(self):
                if self.headers.get("Authorization") != "Bearer " + alertmanager_token:
                    return self.reply(401, b"")
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                response = client.post(
                    "/api/v1/hooks/alertmanager",
                    json=body,
                    headers={"Authorization": "Bearer " + alertmanager_token},
                )
                if response.status_code != 200:
                    errors.append(response.status_code)
                self.reply(response.status_code, response.content, "application/json")

            def reply(self, code, body, content_type="text/plain"):
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        bridge = ThreadingHTTPServer(("0.0.0.0", 0), Bridge)
        bridge_thread = threading.Thread(target=bridge.serve_forever, daemon=True)
        bridge_thread.start()
        bridge_url = f"http://host.docker.internal:{bridge.server_port}"
        target_directory = directory / "target"
        target_directory.mkdir()
        shutil.copyfile(
            ROOT / "backend/tests/fixtures/observability_target.py", target_directory / "target.py"
        )
        (target_directory / "Dockerfile").write_text(
            "FROM sentinelops-backend:local\nWORKDIR /fixture\nCOPY target.py ./target.py\n"
            'CMD ["/app/.venv/bin/python","target.py"]\n',
            encoding="utf-8",
        )
        (directory / "blackbox.yml").write_text(
            yaml.safe_dump({"modules": {"http_2xx": {"prober": "http", "timeout": "5s"}}}),
            encoding="utf-8",
        )
        auth = {"type": "Bearer", "credentials_file": "/etc/acceptance/monitoring.token"}
        prometheus = {
            "global": {"scrape_interval": "1s", "evaluation_interval": "1s"},
            "rule_files": ["/etc/acceptance/rules/*.yml"],
            "alerting": {
                "alertmanagers": [{"static_configs": [{"targets": ["alertmanager:9093"]}]}]
            },
            "scrape_configs": [
                {
                    "job_name": "service-probe",
                    "metrics_path": "/probe-metrics",
                    "authorization": auth,
                    "static_configs": [{"targets": [f"host.docker.internal:{bridge.server_port}"]}],
                },
                {
                    "job_name": "service-traffic",
                    "http_sd_configs": [
                        {
                            "url": bridge_url + "/targets",
                            "refresh_interval": "1s",
                            "authorization": auth,
                        }
                    ],
                    "relabel_configs": [
                        {
                            "source_labels": ["collector"],
                            "regex": "service-traffic",
                            "action": "keep",
                        }
                    ],
                },
            ],
        }
        (directory / "prometheus.yml").write_text(yaml.safe_dump(prometheus), encoding="utf-8")
        alertmanager = {
            "route": {
                "receiver": "platform",
                "group_by": ["alertname", "monitor_id"],
                "group_wait": "0s",
                "group_interval": "1s",
                "repeat_interval": "5m",
            },
            "receivers": [
                {
                    "name": "platform",
                    "webhook_configs": [
                        {
                            "url": bridge_url + "/webhook",
                            "send_resolved": True,
                            "http_config": {
                                "authorization": {
                                    "type": "Bearer",
                                    "credentials_file": "/etc/acceptance/alertmanager.token",
                                }
                            },
                        }
                    ],
                }
            ],
        }
        (directory / "alertmanager.yml").write_text(yaml.safe_dump(alertmanager), encoding="utf-8")
        volume = str(directory).replace("\\", "/") + ":/etc/acceptance:ro"
        compose = {
            "services": {
                "target": {
                    "build": {"context": str(target_directory)},
                    "ports": ["127.0.0.1::8080"],
                    "healthcheck": {
                        "test": [
                            "CMD",
                            "/app/.venv/bin/python",
                            "-c",
                            "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')",
                        ],
                        "interval": "1s",
                        "timeout": "2s",
                        "retries": 10,
                    },
                },
                "blackbox": {
                    "image": "quay.io/prometheus/blackbox-exporter:v0.28.0",
                    "ports": ["127.0.0.1::9115"],
                    "command": ["--config.file=/etc/acceptance/blackbox.yml"],
                    "volumes": [volume],
                    "cap_add": ["NET_RAW"],
                },
                "prometheus": {
                    "image": "quay.io/prometheus/prometheus:v3.5.0",
                    "ports": ["127.0.0.1::9090"],
                    "command": [
                        "--config.file=/etc/acceptance/prometheus.yml",
                        "--web.enable-lifecycle",
                    ],
                    "volumes": [volume],
                },
                "alertmanager": {
                    "image": "quay.io/prometheus/alertmanager:v0.34.1",
                    "command": [
                        "--config.file=/etc/acceptance/alertmanager.yml",
                        "--cluster.listen-address=",
                    ],
                    "volumes": [volume],
                },
            }
        }
        (directory / "compose.yaml").write_text(yaml.safe_dump(compose), encoding="utf-8")
        docker("up", "-d", "--build")
        address = docker("port", "target", "8080").splitlines()[-1]
        blackbox = docker("port", "blackbox", "9115").splitlines()[-1]
        settings.prometheus_url = "http://" + docker("port", "prometheus", "9090").splitlines()[-1]
        settings.blackbox_url = "http://" + blackbox
        network = httpx2.Client(timeout=5, trust_env=False)
        wait_until(
            lambda: network.get("http://" + address + "/health").status_code == 200,
            "Target did not start",
        )
        application = client.post(
            "/api/v1/applications",
            headers=headers,
            json={"code": "accept-service", "name": "隔离验收业务"},
        ).json()["id"]
        response = client.post(
            f"/api/v1/business-services/{application}/monitors",
            headers=headers,
            json={
                "name": "关键业务 API",
                "target": "http://" + address + "/work",
                "route": "/work",
                "assertions": {"ok": True},
                "interval_seconds": 15,
            },
        )
        assert response.status_code == 201, response.text
        monitor = response.json()["id"]
        client.put(
            f"/api/v1/business-services/{application}/traffic",
            headers=headers,
            json={"traffic_target": "target:8080"},
        )
        with SessionLocal() as db:
            row = Server(
                name="isolated-source",
                host="127.0.0.1",
                node_exporter_target="unused:9100",
                cadvisor_target="unused:8080",
                blackbox_exporter_target=blackbox,
            )
            db.add(row)
            db.commit()
            server_id = str(row.id)
        host_monitors = []
        for protocol, target, dns in (
            ("http", "http://target:8080/work", ""),
            ("tcp", "target:8080", ""),
            ("dns", "target", "127.0.0.11:53"),
            ("icmp", "127.0.0.1", ""),
        ):
            response = client.post(
                f"/api/v1/business-services/{application}/monitors",
                headers=headers,
                json={
                    "name": "采集器视角 " + protocol,
                    "protocol": protocol,
                    "target": target,
                    "probe_server_id": server_id,
                    "dns_server": dns,
                    "importance": "optional",
                },
            )
            assert response.status_code == 201, response.text
            host_monitors.append(response.json()["id"])
        (directory / "blackbox.yml").write_text(
            client.get(f"/api/v1/servers/{server_id}/probe-config", headers=headers).text,
            encoding="utf-8",
        )
        docker("restart", "blackbox")
        # Docker can assign a new ephemeral host port when the collector restarts.
        blackbox = docker("port", "blackbox", "9115").splitlines()[-1]
        with SessionLocal() as db:
            db.get(Server, UUID(server_id)).blackbox_exporter_target = blackbox
            db.commit()
        wait_until(
            lambda: network.get("http://" + blackbox + "/metrics").status_code == 200,
            "Blackbox collector did not start",
        )
        for identifier in [monitor, *host_monitors]:
            response = client.post(f"/api/v1/service-monitors/{identifier}/probe", headers=headers)
            assert response.status_code == 200 and response.json()["status"] == "healthy", (
                response.text
            )
        stage("real_protocols_healthy", protocols=["http", "tcp", "dns", "icmp"])

        def traffic():
            while not traffic_stop.wait(0.15):
                try:
                    network.get("http://" + address + "/work")
                except httpx2.HTTPError:
                    pass

        traffic_thread = threading.Thread(target=traffic, daemon=True)
        traffic_thread.start()

        def request_metric(metric):
            response = client.get(
                f"/api/v1/service-monitors/{monitor}/metrics",
                headers=headers,
                params={"metric": metric},
            )
            return response.json().get("result")

        wait_until(
            lambda: request_metric("service_latency_p95_seconds"), "Real traffic P95 missing"
        )
        stage(
            "real_traffic_available",
            request_rate=request_metric("service_request_rate"),
            p95=request_metric("service_latency_p95_seconds"),
        )
        response = client.post(
            "/api/v1/alert-rules",
            headers=headers,
            json={
                "code": "AcceptanceBusinessDown",
                "metric": "service_probe_up",
                "threshold": 1,
                "for_seconds": 0,
                "service_monitor_id": monitor,
                "auto_diagnose": True,
            },
        )
        assert response.status_code == 201, response.text
        response = client.post("/api/v1/monitoring/apply-rules", headers=headers)
        assert response.status_code == 200, response.text
        network.post("http://" + address + "/scenario", json={"mode": "logic_error"})
        assert (
            client.post(f"/api/v1/service-monitors/{monitor}/probe", headers=headers).json()[
                "last_error_code"
            ]
            == "assertion_failed"
        )
        target_health = subprocess.check_output(
            [
                "docker",
                "inspect",
                docker("ps", "-q", "target"),
                "--format",
                "{{.State.Health.Status}}",
            ],
            text=True,
        ).strip()
        assert target_health == "healthy"

        def firing_event():
            with SessionLocal() as db:
                event = db.scalar(select(AlertEvent).where(AlertEvent.status == "firing"))
                return str(event.id) if event else None

        event_id = wait_until(firing_event, "Prometheus/Alertmanager did not create an event")
        with SessionLocal() as db:
            analysis = db.scalar(select(AgentAnalysis))
            assert analysis and analysis.trigger == "automatic"
            analysis.status = "running"
            analysis_id = analysis.id
            db.commit()
        tools = DiagnosticTools(analysis_id)
        context = tools.call("get_alert_context", {}, internal=True)
        assert context["data"]["service_monitoring"][0]["monitor"]["status"] == "failed"
        with SessionLocal() as db:
            complete(
                db,
                analysis_id,
                {
                    "summary": "模拟 Provider：关键 API 业务断言失败",
                    "possible_causes": [
                        {"description": "业务返回值不满足约定", "evidence_ids": [context["id"]]}
                    ],
                    "impact": "关键业务 API 异常",
                    "evidence_ids": [context["id"]],
                    "knowledge_ids": [],
                    "suggested_checks": [],
                    "data_gaps": [],
                    "requires_human_approval": True,
                },
                tools.evidence,
                [],
                [],
            )
        stage(
            "healthy_container_business_failure",
            container_health=target_health,
            event_id=event_id,
            automatic_analysis=True,
        )
        metrics_available["value"] = False

        def resolved_event():
            with SessionLocal() as db:
                event = db.get(AlertEvent, UUID(event_id))
                return event.status == "resolved"

        wait_until(resolved_event, "Missing metrics did not end the alert notification")
        with SessionLocal() as db:
            reconcile(db)
            assert db.get(AlertEvent, UUID(event_id)).recovery_status == "pending"
        network.post("http://" + address + "/scenario", json={"mode": "healthy"})
        client.post(f"/api/v1/service-monitors/{monitor}/probe", headers=headers)
        with SessionLocal() as db:
            reconcile(db)
            assert db.get(AlertEvent, UUID(event_id)).recovery_status == "pending"
        stage("collector_loss_does_not_prove_recovery")
        metrics_available["value"] = True

        def recovered():
            with SessionLocal() as db:
                reconcile(db)
                event = db.get(AlertEvent, UUID(event_id))
                if event.recovery_status == "verified":
                    return {
                        "ended_at": event.ends_at.isoformat(),
                        "evidence_at": event.recovery_evidence_at.isoformat(),
                    }

        proof = wait_until(recovered, "Fresh normal evidence did not verify recovery")
        assert proof["evidence_at"] > proof["ended_at"]
        stage("fresh_recovery_verified", **proof)
        assert not errors
        report["passed"] = True
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)[:2000]
        raise
    finally:
        traffic_stop.set()
        if bridge:
            bridge.shutdown()
            bridge.server_close()
        if bridge_thread:
            bridge_thread.join(timeout=2)
        if client:
            client.__exit__(None, None, None)
        try:
            if (directory / "compose.yaml").exists():
                docker("down", "--volumes", "--remove-orphans")
            engine.dispose()
            if created:
                with admin_engine.connect() as connection:
                    connection.execute(text(f'DROP DATABASE "{DATABASE}" WITH (FORCE)'))
            report["cleanup"] = True
        finally:
            admin_engine.dispose()
            (ROOT / ".local/service-observability-acceptance.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(
                json.dumps({"passed": report["passed"], "cleanup": report["cleanup"]}), flush=True
            )


if __name__ == "__main__":
    main()
