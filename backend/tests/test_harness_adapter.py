import json
import os
import subprocess
import sys
from io import StringIO
from types import SimpleNamespace

import pytest

from app.core.config import BACKEND_ROOT
from app.integrations.harness import child


def test_harness_child_import_needs_no_platform_credentials(tmp_path):
    environment = {
        name: os.environ[name]
        for name in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG")
        if name in os.environ
    }
    environment["PYTHONPATH"] = str(BACKEND_ROOT)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import app.integrations.harness; "
            "assert 'app.db.session' not in sys.modules",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "reason,expected", [("max-tokens", "model_output_limit"), ("error", "harness_failed")]
)
def test_report_output_budget_and_truncation_error(monkeypatch, capsys, reason, expected):
    class Harness:
        def __init__(self, **kwargs):
            assert kwargs["max_tokens"] == 16384

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def run(self, *_args, **_kwargs):
            return SimpleNamespace(finish_reason=reason)

    payload = {
        "home": "local",
        "workspace": "local",
        "patch": "local",
        "key": "dummy",
        "base_url": "http://127.0.0.1",
        "model": "local",
        "timeout": 60,
        "prompt": "local",
        "session_id": "local",
        "max_tokens": 16384,
    }
    monkeypatch.setattr("deepseek_harness.DeepSeekHarness", Harness)
    monkeypatch.setattr("sys.stdin", StringIO(json.dumps(payload)))
    monkeypatch.setattr("signal.signal", lambda *_: None)
    child()
    assert json.loads(capsys.readouterr().out)["error_code"] == expected
