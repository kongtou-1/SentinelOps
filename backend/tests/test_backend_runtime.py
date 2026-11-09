"""Real process lifecycle tests, also runnable in the production image with unittest."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.cli import runtime


class RuntimeTests(unittest.TestCase):
    def test_rejects_execution_and_duplicate_consumers(self):
        for value in ("execution", "ai,ai", "ai,unknown"):
            with self.subTest(value=value), patch.dict(os.environ, BACKEND_WORKERS=value):
                with self.assertRaises(ValueError):
                    runtime.selected_workers()

    def test_partial_inventory_is_unhealthy(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "state.json"
            state.write_text(json.dumps({"api": os.getpid()}), encoding="utf-8")
            with patch.dict(os.environ, BACKEND_WORKERS="ai"):
                with self.assertRaises(RuntimeError):
                    runtime.check(state)

    @unittest.skipUnless(sys.platform == "linux", "Docker uses Linux process probes")
    def test_stale_consumer_is_unhealthy_while_processes_are_alive(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / "state.json"
            state.write_text(json.dumps({"api": os.getpid(), "ai": os.getpid()}))
            heartbeat = root / "ai-worker.heartbeat"
            heartbeat.write_text(str(time.time() - 1000))
            with patch.dict(os.environ, BACKEND_WORKERS="ai"):
                with self.assertRaisesRegex(RuntimeError, "heartbeat is stale"):
                    runtime.check(state, heartbeat_directory=root)

    @unittest.skipUnless(sys.platform == "linux", "Docker uses Linux process probes")
    def test_dead_process_is_unhealthy_even_if_api_is_alive(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "state.json"
            state.write_text(json.dumps({"api": child.pid}), encoding="utf-8")
            with patch.dict(os.environ, BACKEND_WORKERS=""):
                with self.assertRaises(OSError):
                    runtime.check(state)

    @unittest.skipUnless(sys.platform == "linux", "Docker uses Linux process sessions")
    def test_child_exit_stops_siblings_and_descendants(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pid_file = root / "descendant.pid"
            child_code = (
                "import subprocess,sys,pathlib,time; "
                "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']); "
                f"pathlib.Path({str(pid_file)!r}).write_text(str(p.pid)); time.sleep(120)"
            )
            state = root / "state.json"
            result = runtime.supervise(
                {
                    "long": [sys.executable, "-c", child_code],
                    "exits": [sys.executable, "-c", "import time; time.sleep(1)"],
                },
                state=state,
                grace_seconds=1,
            )
            self.assertEqual(result, 1)
            self.assertFalse(state.exists())
            pid = int(pid_file.read_text())
            deadline = time.monotonic() + 3
            while Path(f"/proc/{pid}").exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(Path(f"/proc/{pid}").exists(), "descendant survived shutdown")

    @unittest.skipUnless(sys.platform == "linux", "Docker uses Linux process sessions")
    def test_sigterm_shuts_down_children_and_removes_readiness(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "state.json"
            code = (
                "import sys; from pathlib import Path; from app.cli.runtime import supervise; "
                "raise SystemExit(supervise({'api':[sys.executable,'-c',"
                "'import time; time.sleep(120)']},"
                f"state=Path({str(state)!r}),grace_seconds=1))"
            )
            parent = subprocess.Popen([sys.executable, "-c", code])
            try:
                deadline = time.monotonic() + 5
                while not state.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(state.exists())
                child = json.loads(state.read_text())["api"]
                parent.send_signal(signal.SIGTERM)
                self.assertEqual(parent.wait(timeout=5), 0)
                self.assertFalse(state.exists())
                with self.assertRaises(ProcessLookupError):
                    os.kill(child, 0)
            finally:
                if parent.poll() is None:
                    parent.kill()
                    parent.wait()


if __name__ == "__main__":
    unittest.main()
