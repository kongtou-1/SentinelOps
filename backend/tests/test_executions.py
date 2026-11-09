import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import paramiko
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, text

from app.services.executions.skills import docker_identifier
from app.integrations.action_ssh import INSPECT, ActionSSH
from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.workers.executions import execute_once, interrupt_runs
from app.workers.executions import verify as verify_container
from app.models import (
    AgentAnalysis,
    AlertEvent,
    Approval,
    Container,
    Incident,
    IncidentAlert,
    Role,
    Server,
    SkillExecution,
    SSHCredential,
    User,
)


@pytest.fixture
def proposal(client, admin_headers, monkeypatch, tmp_path):
    key = Fernet.generate_key()
    path = tmp_path / "credential.key"
    path.write_bytes(key)
    monkeypatch.setattr(get_settings(), "credential_key_file", path)
    with SessionLocal() as db:
        now = datetime.now(UTC)
        credential = SSHCredential(
            name="exec-secret",
            auth_type="password",
            encrypted_secret=Fernet(key)
            .encrypt(json.dumps({"secret": "test-password", "passphrase": None}).encode())
            .decode(),
        )
        db.add(credential)
        db.flush()
        host_key = paramiko.RSAKey.generate(2048)
        server = Server(
            name="execution-server",
            host="localhost",
            credential_id=credential.id,
            ssh_host_key=host_key.get_name() + " " + host_key.get_base64(),
            node_exporter_target="node:9100",
            cadvisor_target="cadvisor:8080",
        )
        db.add(server)
        db.flush()
        container = Container(
            server_id=server.id,
            docker_id="/docker/" + "a" * 64,
            name="target",
            image="local",
            status="stopped",
            labels={},
            last_seen_at=now,
        )
        incident = Incident(
            title="Stopped", severity="warning", started_at=now, last_alert_started_at=now
        )
        db.add_all([container, incident])
        db.flush()
        event = AlertEvent(
            fingerprint=uuid4().hex,
            server_id=server.id,
            severity="warning",
            status="firing",
            starts_at=now,
            labels={},
            annotations={},
        )
        db.add(event)
        db.flush()
        db.add(IncidentAlert(incident_id=incident.id, alert_event_id=event.id))
        user = db.scalar(select(User).where(User.username == "admin"))
        analysis = AgentAnalysis(
            incident_id=incident.id,
            requested_by=user.id,
            status="completed",
            config={},
            input_hash="x" * 64,
            result={
                "proposal": {
                    "skill": "restart_container",
                    "version": "1",
                    "parameters": {"container_id": str(container.id)},
                    "rationale": "恢复容器",
                    "impact": "短暂中断",
                }
            },
        )
        db.add(analysis)
        db.commit()
        return SimpleNamespace(
            incident=incident.id,
            container=container.id,
            server=server.id,
            credential=credential.id,
            analysis=analysis.id,
            user=user.id,
        )


def request_approval(client, auth, proposal):
    result = client.post(
        f"/api/v1/incidents/{proposal.incident}/approvals",
        headers=auth,
        json={"analysis_id": str(proposal.analysis)},
    )
    assert result.status_code == 201, result.text
    return result.json()


def approved(client, auth, proposal):
    row = request_approval(client, auth, proposal)
    result = client.post(
        f"/api/v1/approvals/{row['id']}/approve", headers=auth, json={"comment": "确认恢复"}
    )
    assert result.status_code == 200, result.text
    return result.json()


def enqueue(client, auth, row):
    result = client.post(f"/api/v1/approvals/{row['id']}/execute", headers=auth)
    assert result.status_code == 202, result.text
    return result.json()


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
def test_execution_rbac(client, admin_headers, make_user, login, proposal, role):
    make_user("role-user", role)
    auth = {"Authorization": "Bearer " + login("role-user")["access_token"]}
    assert client.get("/api/v1/action-skills", headers=auth).status_code == 200
    row = request_approval(client, admin_headers, proposal)
    for decision in ("approve", "reject"):
        response = client.post(
            f"/api/v1/approvals/{row['id']}/{decision}", headers=auth, json={"comment": "决定"}
        )
        assert response.status_code == (
            200 if role == "admin" and decision == "approve" else 409 if role == "admin" else 403
        )
    response = client.post(f"/api/v1/approvals/{row['id']}/execute", headers=auth)
    assert response.status_code == (403 if role == "viewer" else 409 if role == "operator" else 202)
    response = client.post(
        f"/api/v1/incidents/{proposal.incident}/approvals",
        headers=auth,
        json={"analysis_id": str(proposal.analysis)},
    )
    assert response.status_code == (403 if role == "viewer" else 201)
    assert client.get("/api/v1/audit-logs", headers=auth).status_code == (
        200 if role == "admin" else 403
    )
    assert client.get(f"/api/v1/approvals/{row['id']}", headers=auth).status_code == 200


def test_no_authorization_no_action(client, admin_headers, proposal, monkeypatch):
    monkeypatch.setattr("app.workers.executions.ActionSSH", lambda *_: pytest.fail("SSH opened"))
    row = request_approval(client, admin_headers, proposal)
    assert client.get("/api/v1/action-skills").status_code == 401
    assert (
        client.post(f"/api/v1/approvals/{row['id']}/execute", headers=admin_headers).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/approvals/{row['id']}/reject",
            headers=admin_headers,
            json={"comment": "不执行"},
        ).status_code
        == 200
    )
    assert (
        client.post(f"/api/v1/approvals/{row['id']}/execute", headers=admin_headers).status_code
        == 409
    )
    assert execute_once() is False
    logs = client.get("/api/v1/audit-logs", headers=admin_headers).json()
    assert sum(log["action"] == "execution_denied" for log in logs) == 2


def test_bound_and_idempotent_concurrency(client, admin_headers, proposal):
    def create(_):
        return request_approval(client, admin_headers, proposal)["id"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(create, range(4)))
    assert len(set(ids)) == 1
    row = approved(client, admin_headers, proposal)
    expires = row["expires_at"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        executions = list(pool.map(lambda _: enqueue(client, admin_headers, row), range(4)))
    assert len({r["id"] for r in executions}) == 1
    assert (
        client.get(f"/api/v1/approvals/{row['id']}", headers=admin_headers).json()["expires_at"]
        == expires
    )
    assert (
        client.post(
            f"/api/v1/approvals/{row['id']}/execute",
            headers=admin_headers,
            json={"container_id": str(uuid4())},
        ).status_code
        == 422
    )
    with SessionLocal() as db:
        assert (
            db.scalar(select(Approval).where(Approval.id == UUID(row["id"]))).status == "consumed"
        )
        assert len(db.scalars(select(SkillExecution)).all()) == 1


def test_concurrent_approve_reject_has_one_decision(client, admin_headers, proposal):
    approval = request_approval(client, admin_headers, proposal)

    def decide(action):
        return client.post(
            f"/api/v1/approvals/{approval['id']}/{action}",
            headers=admin_headers,
            json={"comment": "并发审批"},
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(decide, ("approve", "reject", "approve", "reject")))
    assert sorted(r.status_code for r in results) == [200, 409, 409, 409]
    success = next(r.json() for r in results if r.status_code == 200)
    current = client.get(f"/api/v1/approvals/{approval['id']}", headers=admin_headers).json()
    assert current["status"] == success["status"]
    assert current["decided_at"] == success["decided_at"]


def test_expiry_while_queued_never_opens_ssh(client, admin_headers, proposal, monkeypatch):
    approval = approved(client, admin_headers, proposal)
    execution = enqueue(client, admin_headers, approval)
    with SessionLocal() as db:
        db.get(Approval, UUID(approval["id"])).expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    monkeypatch.setattr("app.workers.executions.ActionSSH", lambda *_: pytest.fail("SSH opened"))
    assert execute_once()
    result = client.get(f"/api/v1/executions/{execution['id']}", headers=admin_headers).json()
    assert result["status"] == "failed"
    assert result["error_code"] == "approval_expired"
    assert result["dispatched_at"] is None


@pytest.mark.parametrize(
    "change",
    [
        "expiry",
        "server",
        "credential",
        "container",
        "deleted",
        "requester",
        "approver",
        "incident",
        "binding",
        "skill",
    ],
)
def test_invalid_binding_denied(client, admin_headers, proposal, change, monkeypatch):
    row = approved(client, admin_headers, proposal)
    with SessionLocal() as db:
        approval = db.get(Approval, UUID(row["id"]))
        if change == "expiry":
            approval.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        elif change == "server":
            db.get(Server, proposal.server).host = "elsewhere"
        elif change == "credential":
            db.get(SSHCredential, proposal.credential).name = "new-version"
        elif change == "container":
            db.get(Container, proposal.container).docker_id = "b" * 64
        elif change == "deleted":
            db.get(Container, proposal.container).deleted_at = datetime.now(UTC)
        elif change == "requester":
            role_id = db.get(User, proposal.user).role_id
            identifier = uuid4()
            user = User(
                id=identifier,
                username="disabled",
                password_hash="x",
                role_id=role_id,
                is_active=False,
            )
            db.add(user)
            db.flush()
            approval.requested_by = identifier
        elif change == "approver":
            user = db.get(User, proposal.user)
            user.role_id = db.scalar(select(Role.id).where(Role.code == "operator"))
        elif change == "incident":
            db.get(Incident, proposal.incident).status = "recovered"
        elif change == "binding":
            approval.binding = {**approval.binding, "docker_id": "b" * 64}
        else:
            monkeypatch.setitem(
                __import__("app.services.executions.skills", fromlist=["RESTART"]).RESTART, "version", "2"
            )
        db.commit()
    assert (
        client.post(f"/api/v1/approvals/{row['id']}/execute", headers=admin_headers).status_code
        == 409
    )
    with SessionLocal() as db:
        assert db.get(Approval, UUID(row["id"])).status in ("invalidated", "expired")
        assert db.scalar(select(SkillExecution.id)) is None


@pytest.mark.parametrize("mutation", ["version", "parameters", "outside", "analysis"])
def test_invalid_proposal(client, admin_headers, proposal, mutation):
    with SessionLocal() as db:
        row = db.get(AgentAnalysis, proposal.analysis)
        value = row.result["proposal"].copy()
        if mutation == "version":
            value["version"] = "2"
        elif mutation == "parameters":
            value["parameters"] = {"container_id": str(proposal.container), "command": "id"}
        elif mutation == "outside":
            value["parameters"] = {"container_id": str(uuid4())}
        else:
            row.status = "failed"
        row.result = {"proposal": value}
        db.commit()
    assert (
        client.post(
            f"/api/v1/incidents/{proposal.incident}/approvals",
            headers=admin_headers,
            json={"analysis_id": str(proposal.analysis)},
        ).status_code
        == 409
    )


class FakeSSH:
    calls = 0
    change = None
    error = None
    paused = False
    exit_code = 0

    def __init__(self, *_):
        pass

    def inspect(self, **_):
        if self.change:
            self.change()
        return {
            "id": "a" * 64,
            "running": True,
            "paused": self.paused,
            "restarting": False,
            "status": "running",
            "health": "healthy",
        }

    def restart(self):
        type(self).calls += 1
        if self.error:
            raise self.error
        return {"exit_code": self.exit_code, "stdout": "a" * 64, "stderr": ""}

    def close(self):
        pass


@pytest.fixture
def ssh(monkeypatch):
    class SSH(FakeSSH):
        pass

    monkeypatch.setattr("app.workers.executions.ActionSSH", SSH)
    monkeypatch.setattr("app.workers.executions.time.sleep", lambda _: None)
    return SSH


def test_worker_success_persistence_audit(client, admin_headers, proposal, ssh):
    row = approved(client, admin_headers, proposal)
    execution = enqueue(client, admin_headers, row)
    assert execute_once()
    assert not execute_once()
    with SessionLocal() as db:
        interrupt_runs(db)
    result = client.get(f"/api/v1/executions/{execution['id']}", headers=admin_headers).json()
    assert result["status"] == "succeeded"
    assert result["verification"]["passed"] is True
    assert result["dispatched_at"]
    assert ssh.calls == 1
    assert enqueue(client, admin_headers, row)["id"] == execution["id"]
    logs = client.get(
        f"/api/v1/audit-logs?execution_id={execution['id']}", headers=admin_headers
    ).json()
    assert {log["action"] for log in logs} >= {
        "execution_prechecking",
        "execution_executing",
        "execution_verifying",
        "execution_succeeded",
    }
    assert "test-password" not in json.dumps(logs)


@pytest.mark.parametrize("phase", ["queued", "prechecking"])
def test_worker_rechecks_authorization(client, admin_headers, proposal, ssh, phase):
    row = approved(client, admin_headers, proposal)
    result = enqueue(client, admin_headers, row)

    def revoke():
        with SessionLocal() as db:
            db.get(Server, proposal.server).host = "changed"
            db.commit()

    if phase == "queued":
        revoke()
    else:
        ssh.change = staticmethod(revoke)
    assert execute_once()
    assert ssh.calls == 0
    assert (
        client.get(f"/api/v1/executions/{result['id']}", headers=admin_headers).json()["error_code"]
        == "binding_changed"
    )


@pytest.mark.parametrize("failure", ["paused", "command", "disconnect", "timeout", "verify"])
def test_worker_failures_never_retry(client, admin_headers, proposal, ssh, monkeypatch, failure):
    row = approved(client, admin_headers, proposal)
    result = enqueue(client, admin_headers, row)
    ssh.paused = failure == "paused"
    ssh.exit_code = 1 if failure == "command" else 0
    ssh.error = (
        EOFError() if failure == "disconnect" else TimeoutError() if failure == "timeout" else None
    )
    if failure == "verify":
        monkeypatch.setattr("app.workers.executions.verify", lambda _: {"passed": False})
    assert execute_once()
    record = client.get(f"/api/v1/executions/{result['id']}", headers=admin_headers).json()
    assert record["status"] == ("unknown" if failure in ("disconnect", "timeout") else "failed")
    assert ssh.calls == (0 if failure == "paused" else 1)
    assert not execute_once()
    assert enqueue(client, admin_headers, row)["id"] == result["id"]


@pytest.mark.parametrize("dispatched", [False, True])
def test_restart_interruption_is_terminal(client, admin_headers, proposal, dispatched):
    row = approved(client, admin_headers, proposal)
    result = enqueue(client, admin_headers, row)
    with SessionLocal() as db:
        run = db.get(SkillExecution, UUID(result["id"]))
        run.status = "executing" if dispatched else "prechecking"
        run.dispatched_at = datetime.now(UTC) if dispatched else None
        db.commit()
        interrupt_runs(db)
        db.refresh(run)
        assert run.status == ("unknown" if dispatched else "interrupted")
    assert not execute_once()


def test_same_container_busy(client, admin_headers, proposal):
    first = approved(client, admin_headers, proposal)
    enqueue(client, admin_headers, first)
    second = approved(client, admin_headers, proposal)
    assert (
        client.post(f"/api/v1/approvals/{second['id']}/execute", headers=admin_headers).status_code
        == 409
    )


@pytest.mark.parametrize("value", ["worker; id", "a" * 12, "-" + "a" * 64, "a" * 64 + "\n"])
def test_untrusted_identifier(value):
    with pytest.raises(ValueError):
        docker_identifier(value)


def test_timestamps_and_triggers(client, admin_headers, proposal):
    row = request_approval(client, admin_headers, proposal)
    with engine.begin() as connection:
        for table in ("approvals", "skill_executions", "audit_logs"):
            result = connection.execute(
                text(
                    "SELECT column_name, data_type, is_nullable "
                    "FROM information_schema.columns WHERE "
                    "table_name=:table AND column_name IN "
                    "('created_at','updated_at','deleted_at')"
                ),
                {"table": table},
            ).all()
            assert len(result) == 3
            assert all(r.data_type == "timestamp with time zone" for r in result)
        previous = connection.scalar(
            text("SELECT updated_at FROM approvals WHERE id=:id"), {"id": row["id"]}
        )
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE approvals SET decision_comment='note' WHERE id=:id"), {"id": row["id"]}
        )
        assert (
            connection.scalar(
                text("SELECT updated_at FROM approvals WHERE id=:id"), {"id": row["id"]}
            )
            > previous
        )


@pytest.mark.parametrize("failure", ["auth", "host_key", "precheck", "identity"])
def test_ssh_failures_before_dispatch(client, admin_headers, proposal, ssh, monkeypatch, failure):
    row = approved(client, admin_headers, proposal)
    execution = enqueue(client, admin_headers, row)

    def failed_connect(*_):
        if failure == "auth":
            raise paramiko.AuthenticationException()
        key = paramiko.RSAKey.generate(2048)
        raise paramiko.BadHostKeyException("localhost", key, key)

    if failure in ("auth", "host_key"):
        monkeypatch.setattr("app.workers.executions.ActionSSH", failed_connect)
    else:

        def failed_inspect(*_, **__):
            raise ValueError("identity" if failure == "identity" else "inspect_failed")

        ssh.inspect = failed_inspect
    assert execute_once()
    result = client.get(f"/api/v1/executions/{execution['id']}", headers=admin_headers).json()
    assert result["status"] == "failed"
    assert result["dispatched_at"] is None
    assert ssh.calls == 0
    assert result["error_code"] == (
        "authentication_failed"
        if failure == "auth"
        else "host_key_mismatch"
        if failure == "host_key"
        else "invalid_container_state"
    )


@pytest.mark.parametrize("person", ["requester", "approver", "initiator"])
def test_worker_rechecks_each_actor(client, admin_headers, proposal, make_user, login, ssh, person):
    make_user("requester", "operator")
    make_user("initiator", "operator")
    requester = {"Authorization": "Bearer " + login("requester")["access_token"]}
    initiator = {"Authorization": "Bearer " + login("initiator")["access_token"]}
    approval = request_approval(client, requester, proposal)
    client.post(
        f"/api/v1/approvals/{approval['id']}/approve",
        headers=admin_headers,
        json={"comment": "恢复"},
    ).raise_for_status()
    execution = enqueue(client, initiator, approval)
    with SessionLocal() as db:
        name = "admin" if person == "approver" else person
        user = db.scalar(select(User).where(User.username == name))
        user.is_active = False
        db.commit()
    assert execute_once()
    assert ssh.calls == 0
    with SessionLocal() as db:
        assert (
            db.get(SkillExecution, UUID(execution["id"])).error_code
            == {
                "requester": "requester_unavailable",
                "approver": "approver_unavailable",
                "initiator": "initiator_unavailable",
            }[person]
        )


@pytest.mark.parametrize("health", [None, "healthy"])
def test_verify_stability_and_explicit_missing_healthcheck(monkeypatch, health):
    calls = []
    state = {
        "running": True,
        "paused": False,
        "restarting": False,
        "status": "running",
        "health": health,
    }
    ssh = SimpleNamespace(inspect=lambda **_: calls.append(1) or state)
    monkeypatch.setattr("app.workers.executions.time.sleep", lambda _: None)
    result = verify_container(ssh)
    assert len(calls) == 2
    assert result["passed"]
    assert result["application_health"] == ("healthy" if health else "not_configured")


def test_fixed_ssh_command_pinning_and_output_limits(proposal, monkeypatch):
    client = MagicMock()
    channel = MagicMock()
    raw = b"password=must-redact\n" + b"x" * 20000
    streams = [bytearray(raw), bytearray(b"y" * 20000)]
    channel.recv_ready.side_effect = lambda: bool(streams[0])
    channel.recv_stderr_ready.side_effect = lambda: bool(streams[1])

    def receive(index, size):
        chunk = bytes(streams[index][:size])
        del streams[index][:size]
        return chunk

    channel.recv.side_effect = lambda size: receive(0, size)
    channel.recv_stderr.side_effect = lambda size: receive(1, size)
    channel.exit_status_ready.return_value = True
    channel.recv_exit_status.return_value = 0
    client.exec_command.return_value = (None, SimpleNamespace(channel=channel), None)
    monkeypatch.setattr("app.integrations.action_ssh.paramiko.SSHClient", lambda: client)
    with SessionLocal() as db:
        ssh = ActionSSH(
            db.get(Server, proposal.server), db.get(SSHCredential, proposal.credential), "a" * 64
        )
    result = ssh.restart()
    client.exec_command.assert_called_once_with("docker restart -t 10 " + "a" * 64, timeout=30)
    assert len(result["stdout"].encode()) <= 16384
    assert len(result["stderr"].encode()) <= 16384
    assert "must-redact" not in result["stdout"]
    assert result["truncated"]
    assert client.connect.call_args.kwargs["look_for_keys"] is False
    assert client.connect.call_args.kwargs["allow_agent"] is False
    assert client.get_host_keys().add.call_count == 1
    ssh.close()


def test_ssh_inspect_only_retains_fixed_fields(proposal, monkeypatch):
    monkeypatch.setattr("app.integrations.action_ssh.paramiko.SSHClient", MagicMock)
    with SessionLocal() as db:
        ssh = ActionSSH(
            db.get(Server, proposal.server), db.get(SSHCredential, proposal.credential), "a" * 64
        )
    runner = MagicMock(
        return_value={
            "exit_code": 0,
            "truncated": False,
            "stdout": json.dumps("a" * 64)
            + " "
            + json.dumps(
                {
                    "Running": True,
                    "Paused": False,
                    "Restarting": False,
                    "Status": "running",
                    "Health": {"Status": "healthy", "Log": [{"Output": "secret-in-health-log"}]},
                }
            ),
        }
    )
    monkeypatch.setattr(ssh, "_run", runner)
    result = ssh.inspect()
    runner.assert_called_once_with(INSPECT + "a" * 64, 10)
    assert result["health"] == "healthy"
    assert "secret-in-health-log" not in json.dumps(result)
    runner.return_value["stdout"] = json.dumps("b" * 64) + " {}"
    with pytest.raises(ValueError):
        ssh.inspect()
