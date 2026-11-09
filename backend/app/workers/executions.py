import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import paramiko
from cryptography.fernet import InvalidToken

import app.repositories.worker_executions as _repository
from app.db.session import SessionLocal, engine
from app.integrations.action_ssh import ActionSSH
from app.services.executions.actions import ACTIVE, audit, invalid_reason
from app.services.incidents.logic import lock_incidents
from app.services.operations.platform import pulse

logger = logging.getLogger(__name__)
HEARTBEAT = Path("/tmp/execution-worker.heartbeat")


def stage(db, row, status, error=None, **values):
    row.status, row.error_code = status, error
    for name, value in values.items():
        setattr(row, name, value)
    if status not in ACTIVE:
        row.finished_at = datetime.now(UTC)
    approval = _repository.stage_query(db, row)
    audit(db, approval, "execution_" + status, execution=row, detail={"error_code": error})


def authorize(db, row):
    approval = _repository.authorize_query(db, row)
    if approval.deleted_at or approval.status != "consumed" or not approval.approver_id:
        return "approval_unavailable"
    return invalid_reason(db, approval, row)


def interrupt_runs(db):
    lock_incidents(db)
    for row in _repository.interrupt_runs_query(ACTIVE, db):
        stage(db, row, "unknown" if row.dispatched_at else "interrupted", "worker_interrupted")
    db.commit()


def error_code(exc):
    if isinstance(exc, paramiko.BadHostKeyException):
        return "host_key_mismatch"
    if isinstance(exc, paramiko.AuthenticationException):
        return "authentication_failed"
    if isinstance(exc, TimeoutError):
        return "ssh_timeout"
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return "invalid_container_state"
    return "ssh_unavailable"


def finish(identifier, status, error=None, **values):
    with SessionLocal() as db:
        lock_incidents(db)
        row = _repository.finish_query(db, identifier)
        # A recovered worker cannot overwrite a terminal interruption record.
        if row.status not in ACTIVE:
            return
        stage(db, row, status, error, **values)
        db.commit()


def verify(ssh):
    deadline = time.monotonic() + 60
    samples, stable = [], 0
    while time.monotonic() < deadline:
        state = ssh.inspect(timeout=min(10, max(0.1, deadline - time.monotonic())))
        samples.append(state)
        good = (
            state["running"]
            and not state["paused"]
            and not state["restarting"]
            and state["status"] == "running"
            and state["health"] in (None, "healthy")
        )
        stable = stable + 1 if good else 0
        if stable >= 2:
            return {
                "passed": True,
                "samples": samples,
                "application_health": "healthy" if state["health"] else "not_configured",
                "note": "Docker HEALTHCHECK healthy"
                if state["health"]
                else "仅验证容器运行，应用健康检查未配置",
            }
        time.sleep(min(2, max(0, deadline - time.monotonic())))
    return {"passed": False, "samples": samples, "application_health": "not_recovered"}


def execute_once(owner=None):
    with SessionLocal() as db:
        lock_incidents(db)
        row = _repository.execute_once_query(db)
        if not row:
            return False
        identifier = row.id
        reason = authorize(db, row)
        if reason:
            stage(db, row, "failed", reason)
            db.commit()
            return True
        approval, credential, server = _repository.execute_once_query_2(db, row)
        docker_id = approval.binding["docker_id"]
        stage(db, row, "prechecking", started_at=datetime.now(UTC))
        db.commit()
    ssh, dispatched = None, False
    try:
        ssh = ActionSSH(server, credential, docker_id)
        precheck = ssh.inspect()
        if (
            precheck["paused"]
            or precheck["restarting"]
            or precheck["status"] in ("removing", "dead")
        ):
            finish(identifier, "failed", "precheck_failed", precheck=precheck)
            return True
        if owner is not None:
            _repository.execute_once_query_3(owner)
            owner.commit()
        with SessionLocal() as db:
            lock_incidents(db)
            row = _repository.execute_once_query_4(db, identifier)
            if row.status != "prechecking" or row.dispatched_at:
                return True
            reason = authorize(db, row)
            if reason:
                stage(db, row, "failed", reason, precheck=precheck)
                db.commit()
                return True
            # This committed transition is the authorization boundary. No retry thereafter.
            stage(db, row, "executing", precheck=precheck, dispatched_at=datetime.now(UTC))
            db.commit()
        dispatched = True
        output = ssh.restart()
        if output["exit_code"] != 0:
            finish(identifier, "failed", "action_failed", output=output)
            return True
        with SessionLocal() as db:
            lock_incidents(db)
            row = _repository.execute_once_query_5(db, identifier)
            if row.status != "executing":
                return True
            stage(db, row, "verifying", output=output)
            db.commit()
        verification = verify(ssh)
        # Existing webhooks, not the executor, decide incident recovery.
        with SessionLocal() as db:
            from app.services.incidents.logic import linked_alerts

            row = _repository.execute_once_query_6(db, identifier)
            verification["monitoring"] = [
                {"alert_id": str(event.id), "status": event.status}
                for event in linked_alerts(db, row.incident_id)
            ]
        finish(
            identifier,
            "succeeded" if verification["passed"] else "failed",
            None if verification["passed"] else "verification_failed",
            verification=verification,
        )
    except (
        paramiko.SSHException,
        OSError,
        EOFError,
        InvalidToken,
        ValueError,
        KeyError,
        TypeError,
    ) as exc:
        finish(identifier, "unknown" if dispatched else "failed", error_code(exc))
    except Exception as exc:
        logger.error("Execution failed (%s)", type(exc).__name__)
        finish(identifier, "unknown" if dispatched else "failed", "execution_failed")
    finally:
        if ssh:
            ssh.close()
    return True


def main():
    logging.basicConfig(level=logging.INFO)
    with engine.connect() as owner:
        if not _repository.main_query(owner):
            raise SystemExit("Another execution worker is active")
        owner.commit()
        with SessionLocal() as db:
            interrupt_runs(db)
        logger.info("Execution worker ready; bound approvals only; no automatic retry")
        while True:
            pulse("execution")
            HEARTBEAT.write_text(str(time.time()), encoding="utf-8")
            _repository.main_query_2(owner)
            owner.commit()
            if not execute_once(owner):
                time.sleep(2)


if __name__ == "__main__":
    main()
