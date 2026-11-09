"""Run in an isolated Linux container as root, never on the host.

docker run --rm --user 0 --entrypoint /app/.venv/bin/python \
  -v <this file>:/check.py:ro sentinelops-backend:local /check.py
"""

import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, "/app")
from prepare_volumes import main


def must_deny(operation):
    try:
        operation()
    except PermissionError:
        return
    raise AssertionError("Unauthorized operation succeeded")


seed, config = Path("/seed"), Path("/config")
seed.mkdir()
config.mkdir()
(config / "rules").mkdir()
(config / "rules/platform.yml").write_text("groups: []\n")
for name in ("credential_key", "monitoring_token", "alertmanager_token"):
    file = seed / name
    file.write_text("synthetic-secret-for-permission-check")
    os.chown(file, 12345, 12345)
    file.chmod(0o600)
smtp = config / "smtp.password"
smtp.write_text("synthetic-smtp-password")
os.chown(smtp, 12345, 12345)
smtp.chmod(0o600)
os.chown(seed, 12345, 12345)
seed.chmod(0o700)
main()

for uid, directory, names in (
    (1000, "backend", ("credential_key", "monitoring_token", "alertmanager_token")),
    (65534, "prometheus", ("monitoring_token",)),
    (65534, "alertmanager", ("alertmanager_token", "smtp_password")),
):
    path = Path("/owned") / directory
    assert path.stat().st_uid == uid and stat.S_IMODE(path.stat().st_mode) == 0o700
    for name in names:
        file = path / name
        assert file.stat().st_uid == uid and stat.S_IMODE(file.stat().st_mode) == 0o400
    os.setegid(uid)
    os.seteuid(uid)
    try:
        for name in names:
            assert (path / name).read_bytes()
            must_deny(lambda: (path / name).write_text("replacement"))
        must_deny(lambda: (seed / "credential_key").read_bytes())
        if uid == 1000:
            must_deny(lambda: Path("/owned/prometheus/monitoring_token").read_bytes())
            Path("/owned/rules/platform.yml").write_text("groups: []\n")
        else:
            must_deny(lambda: Path("/owned/backend/credential_key").read_bytes())
            assert Path("/owned/rules/platform.yml").read_bytes()
            must_deny(lambda: Path("/owned/rules/platform.yml").write_text("bad"))
    finally:
        os.seteuid(0)
        os.setegid(0)
# Reinitialization preserves applied rules and restores safe permissions.
Path("/owned/rules/platform.yml").write_text("groups: []\n# retained\n")
main()
assert "retained" in Path("/owned/rules/platform.yml").read_text()
print("Linux permissions passed: 0600 seed, 0400 owned secrets, rules write restricted to UID 1000")
