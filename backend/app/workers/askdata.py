"""Dedicated consumer for serialized semantic builds and bounded query runs."""

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.db.session import SessionLocal, engine
from app.repositories import askdata as repository
from app.repositories import askdata_runs as query_repository
from app.services import askdata as settings
from app.services.askdata_build import recover, run_once
from app.services.operations.platform import pulse

HEARTBEAT = Path("/tmp/askdata-worker.heartbeat")
HEARTBEAT_SECONDS = 10
BUILD_CONCURRENCY = 1

logger = logging.getLogger(__name__)


def query_concurrency():
    try:
        value = int(os.environ.get("ASKDATA_QUERY_CONCURRENCY", "2"))
    except ValueError:
        value = 2
    return max(1, min(value, 8))


def start_heartbeat(stop):
    def beat():
        while not stop.wait(HEARTBEAT_SECONDS):
            try:
                pulse("askdata")
                HEARTBEAT.write_text(str(time.time()), encoding="utf-8")
            except Exception:
                logger.exception("Ask-data heartbeat failed")

    thread = threading.Thread(target=beat, daemon=True, name="askdata-heartbeat")
    thread.start()
    return thread


def build_loop(stop):
    """Builds stay serialized on one connection lock.

    ``ASKDATA_PAUSED`` stops claiming new work without touching existing rows;
    queued builds simply stay queued until the switch is turned back.
    """
    while not stop.is_set():
        if settings.paused():
            stop.wait(5)
            continue
        try:
            if not run_once():
                stop.wait(5)
        except Exception:
            logger.exception("Ask-data build loop iteration failed")
            stop.wait(5)


def query_once():
    with SessionLocal() as db:
        row = query_repository.claim_run(db)
        if row is None:
            db.rollback()
            return None
        identifier = row.id
        db.commit()
    return identifier


def query_loop(stop, pool):
    """Query runs have their own concurrency budget; they never delay builds."""
    pending = set()
    limit = query_concurrency()
    while not stop.is_set():
        if settings.paused():
            stop.wait(1)
            continue
        try:
            pending = {future for future in pending if not future.done()}
            while len(pending) < limit:
                identifier = query_once()
                if identifier is None:
                    break
                pending.add(pool.submit(_execute_query, identifier))
        except Exception:
            logger.exception("Ask-data query loop iteration failed")
        stop.wait(1)
    for future in pending:
        future.cancel()


def _execute_query(identifier):
    from app.services.askdata_query import run

    try:
        run(identifier)
    except Exception:
        logger.exception("Ask-data query worker failed (run=%s)", identifier)


def main():
    logging.basicConfig(level=logging.INFO)
    with engine.connect() as owner:
        if not repository.worker_lock(owner):
            raise SystemExit("Another ask-data worker is active")
        owner.commit()
        recovered_builds = recover()
        with SessionLocal() as db:
            recovered_runs = len(query_repository.recover_interrupted_runs(db))
            db.commit()
        logger.info(
            "Ask-data worker ready; recovered builds=%d runs=%d query_concurrency=%d",
            recovered_builds,
            recovered_runs,
            query_concurrency(),
        )
        stop = threading.Event()
        start_heartbeat(stop)
        with ThreadPoolExecutor(
            max_workers=query_concurrency(), thread_name_prefix="askdata-query"
        ) as pool:
            builders = [
                threading.Thread(
                    target=build_loop,
                    args=(stop,),
                    daemon=True,
                    name=f"askdata-build-{index}",
                )
                for index in range(BUILD_CONCURRENCY)
            ]
            consumers = threading.Thread(
                target=query_loop,
                args=(stop, pool),
                daemon=True,
                name="askdata-query-consumer",
            )
            for thread in builders:
                thread.start()
            consumers.start()
            try:
                while True:
                    owner.exec_driver_sql("SELECT 1")
                    owner.commit()
                    pulse("askdata")
                    HEARTBEAT.write_text(str(time.time()), encoding="utf-8")
                    if stop.wait(5):
                        break
            finally:
                stop.set()
                for thread in builders:
                    thread.join(timeout=10)
                consumers.join(timeout=10)


if __name__ == "__main__":
    main()
