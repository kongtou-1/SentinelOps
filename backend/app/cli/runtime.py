"""Run the API and fixed background consumers in one Docker container."""

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from threading import Event
from urllib.request import urlopen

logger = logging.getLogger(__name__)
STATE = Path("/tmp/backend-runtime.json")
WORKERS = {
    "operations": ("operations", 150),
    "prediction": ("predictions", 150),
    "source": ("sources", 630),
    "inspection": ("inspections", 150),
    "ai": ("ai", 210),
    "report": ("reports", 240),
    "askdata": ("askdata", 150),
}
DEFAULT_WORKERS = ",".join(WORKERS)


def selected_workers():
    names = [name.strip() for name in os.environ.get("BACKEND_WORKERS", DEFAULT_WORKERS).split(",")]
    names = [name for name in names if name]
    if len(set(names)) != len(names) or set(names) - WORKERS.keys():
        raise ValueError("BACKEND_WORKERS must contain unique supported worker names")
    return names


def commands(names):
    result = {
        "api": [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
            "--workers",
            "1",
            "--timeout-graceful-shutdown",
            "20",
        ]
    }
    for name in names:
        result[name] = [sys.executable, "-m", f"app.workers.{WORKERS[name][0]}"]
    return result


def signal_child(child, sig):
    # Signal the whole session, including prediction/Harness/Git descendants.
    try:
        os.killpg(child.pid, sig)
    except ProcessLookupError:
        pass


def supervise(process_commands, state=STATE, grace_seconds=25):
    """Fail the container if any child exits; Docker applies the restart policy."""
    children = {}
    stopping = Event()
    previous = {}
    state.unlink(missing_ok=True)
    for sig in (signal.SIGTERM, signal.SIGINT):
        previous[sig] = signal.signal(sig, lambda *_: stopping.set())
    try:
        for name, command in process_commands.items():
            if stopping.is_set():
                return 0
            env = os.environ.copy()
            if name == "prediction":
                env.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1")
            children[name] = subprocess.Popen(command, env=env, start_new_session=True)
            logger.info("Started %s pid=%s", name, children[name].pid)
        state.write_text(
            json.dumps({name: child.pid for name, child in children.items()}), encoding="utf-8"
        )
        while not stopping.wait(0.5):
            for name, child in children.items():
                if child.poll() is not None:
                    logger.error("%s exited with code %s; stopping backend", name, child.returncode)
                    return 1
        return 0
    finally:
        state.unlink(missing_ok=True)
        for child in children.values():
            signal_child(child, signal.SIGTERM)
        deadline = time.monotonic() + grace_seconds
        for child in children.values():
            try:
                child.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
        # Kill remaining process groups even when their immediate parent already exited.
        for child in children.values():
            signal_child(child, signal.SIGKILL)
        for child in children.values():
            child.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def check(state=STATE, heartbeat_directory=Path("/tmp")):
    names = selected_workers()
    processes = json.loads(state.read_text(encoding="utf-8"))
    if set(processes) != {"api", *names}:
        raise RuntimeError("Backend process inventory is incomplete")
    for pid in processes.values():
        os.kill(pid, 0)
    now = time.time()
    for name in names:
        heartbeat = heartbeat_directory / f"{name}-worker.heartbeat"
        if now - float(heartbeat.read_text(encoding="utf-8")) > WORKERS[name][1]:
            raise RuntimeError(f"{name} heartbeat is stale")
    with urlopen("http://127.0.0.1:8000/health/ready", timeout=5) as response:
        if response.status != 200:
            raise RuntimeError("API is not ready")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.check:
        try:
            check()
        except (OSError, ValueError, RuntimeError) as exc:
            logger.error("Backend health check failed (%s)", type(exc).__name__)
            return 1
        return 0
    names = selected_workers()
    for name in names:
        Path(f"/tmp/{name}-worker.heartbeat").unlink(missing_ok=True)
    return supervise(commands(names))


if __name__ == "__main__":
    raise SystemExit(main())
