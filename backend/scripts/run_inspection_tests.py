"""Run only against the separately configured local inspection test database."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for line in (ROOT / ".local/inspections-test.env").read_text(encoding="utf-8").splitlines():
    key, value = line.split("=", 1)
    os.environ[key] = value
if not os.environ["POSTGRES_DB"].endswith("_test"):
    raise SystemExit("An isolated _test database is required")
sys.path.insert(0, str(ROOT / "backend"))

import pytest  # noqa: E402

raise SystemExit(pytest.main(sys.argv[1:] or ["backend/tests/test_inspections.py", "-q"]))
