"""Persistent read-only inspections, isolated from incident diagnosis capacity."""

import logging
import time
from pathlib import Path

from app.db.session import SessionLocal, engine
from app.repositories.inspections import worker_lock
from app.services.inspections.runtime import run_once
from app.services.operations.platform import pulse

HEARTBEAT = Path("/tmp/inspection-worker.heartbeat")


def main():
    logging.basicConfig(level=logging.INFO)
    with engine.connect() as owner:
        if not worker_lock(owner):
            raise SystemExit("Another inspection worker is active")
        owner.commit()
        from app.services.inspections.recovery import recover_interrupted

        with SessionLocal() as db:
            recover_interrupted(db)
        while True:
            owner.exec_driver_sql("SELECT 1")
            owner.commit()
            pulse("inspection")
            HEARTBEAT.write_text(str(time.time()), encoding="utf-8")
            if not run_once():
                time.sleep(5)


if __name__ == "__main__":
    main()
