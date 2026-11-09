"""One durable-queue worker with bounded, isolated CPU calculations."""

import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from threading import Thread

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

import app.repositories.worker_predictions as _repository
from app.core.config import BACKEND_ROOT, get_settings
from app.db.session import SessionLocal, engine
from app.services.operations.platform import pulse
from app.services.predictions.tasks import (
    claim,
    complete,
    interrupt_runs,
    latest,
    load_history,
    schedule_due,
)

logger = logging.getLogger(__name__)
HEARTBEAT = Path("/tmp/prediction-worker.heartbeat")


def recovery_loop():
    from app.services.monitoring.recovery import reconcile

    while True:
        started = time.monotonic()
        try:
            with SessionLocal() as db:
                reconcile(db)
        except SQLAlchemyError as exc:
            logger.error("Recovery check database failed (%s)", type(exc).__name__)
        time.sleep(max(1, 60 - (time.monotonic() - started)))


def calculate(payload, timeout=None):
    try:
        process = subprocess.run(
            [sys.executable, "-m", "app.workers.predictions", "--calculate"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=timeout or get_settings().prediction_calculation_timeout_seconds,
            cwd=BACKEND_ROOT,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except subprocess.TimeoutExpired:
        return None, "calculation_timeout"
    if process.returncode:
        return None, "calculation_failed"
    try:
        result = json.loads(process.stdout)
        if "error_code" in result:
            return None, result["error_code"]
        return result, None
    except (ValueError, TypeError):
        return None, "calculation_failed"


def run_once():
    with SessionLocal() as db:
        schedule_due(db)
        run = claim(db)
        if run is None:
            return False
        run_id = run.id
        previous = latest(db, run.task_id, completed=True)
        saved = {}
        if previous and previous.config == run.config:
            saved = {
                key: getattr(previous, key)
                for key in ("horizons", "detector_state", "backtest", "anomalies")
            }
        config = run.config
    try:
        points, end = load_history(config)
        answer, error = calculate(
            {"points": points, "end": end, "config": config, "previous": saved}
        )
    except HTTPException as exc:
        answer, error = (
            None,
            "ambiguous_series" if exc.status_code == 422 else "prometheus_unavailable",
        )
    with SessionLocal() as db:
        complete(db, run_id, answer, error)
    return True


def main():
    logging.basicConfig(level=logging.INFO)
    # Session-scoped lock prevents two workers and unsafe restart recovery.
    with engine.connect() as owner:
        if not _repository.main_query(owner):
            raise SystemExit("Another prediction worker is active")
        owner.commit()
        with SessionLocal() as db:
            interrupt_runs(db)
        logger.info(
            "Prediction worker ready; single concurrency, CPU timeout %ss",
            get_settings().prediction_calculation_timeout_seconds,
        )
        Thread(target=recovery_loop, daemon=True).start()
        while True:
            pulse("prediction")
            HEARTBEAT.write_text(str(time.time()), encoding="utf-8")
            # Verify ownership connection is alive before claiming another job.
            _repository.main_query_2(owner)
            owner.commit()
            worked = run_once()
            if not worked:
                time.sleep(5)


if __name__ == "__main__":
    if "--calculate" in sys.argv:
        try:
            from app.services.predictions.engine import compute

            payload = json.load(sys.stdin)
            print(json.dumps(compute(**payload), allow_nan=False))
        except Exception as exc:
            # Never include query data, credentials or exception text in logs.
            print(json.dumps({"error_code": "calculation_failed"}))
            print(type(exc).__name__, file=sys.stderr)
    else:
        main()
