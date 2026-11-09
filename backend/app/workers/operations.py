"""Bounded governance checks; no server actions, backup or automatic data deletion."""

import logging
import time
from datetime import UTC, datetime
from pathlib import Path

from app.db.session import SessionLocal, engine
from app.repositories import operations as repository
from app.services.inspections.plans import schedule_due
from app.services.operations.improvements import remind_due
from app.services.operations.notifications import deliver_once, interrupt_deliveries
from app.services.operations.objectives import evaluate_due
from app.services.operations.platform import remind_backup
from app.services.operations.prediction_outcomes import evaluate_due as evaluate_prediction_outcomes
from app.services.operations.response import reconcile
from app.services.operations.retention import cleanup_once
from app.services.operations.usage import collect_applications

logger = logging.getLogger(__name__)
HEARTBEAT = Path("/tmp/operations-worker.heartbeat")


def run_once():
    with SessionLocal() as db:
        schedule_due(db)
        reconcile(db)
        remind_due(db)
        remind_backup(db)
        evaluate_due(db)
        evaluate_prediction_outcomes(db)
        collect_applications(db)
    deliver_once()
    cleanup_once()
    with SessionLocal() as db:
        repository.heartbeat(db, "operations", datetime.now(UTC))
        db.commit()


def main():
    logging.basicConfig(level=logging.INFO)
    with engine.connect() as owner:
        if not repository.worker_lock(owner):
            raise SystemExit("Another operations worker is active")
        owner.commit()
        with SessionLocal() as db:
            interrupt_deliveries(db)
        while True:
            repository.worker_alive(owner)
            owner.commit()
            HEARTBEAT.write_text(str(time.time()), encoding="utf-8")
            try:
                run_once()
            except Exception as exc:
                logger.error("Operations check failed (%s)", type(exc).__name__)
                try:
                    with SessionLocal() as db:
                        repository.heartbeat(
                            db,
                            "operations",
                            datetime.now(UTC),
                            state="error",
                            detail={"error_code": type(exc).__name__},
                        )
                        db.commit()
                except Exception:
                    logger.error("Operations heartbeat could not be stored")
            time.sleep(15)


if __name__ == "__main__":
    main()
