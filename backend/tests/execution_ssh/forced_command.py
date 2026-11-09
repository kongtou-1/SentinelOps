"""Acceptance-only sshd gateway. Every command and target is independently checked."""

import os
import re
import subprocess
from pathlib import Path

identifier = r"([a-f0-9]{64})"
command = os.environ.get("SSH_ORIGINAL_COMMAND", "")
inspect = re.fullmatch(
    r"docker inspect --format '\{\{json \.Id\}\} \{\{json \.State\}\}' " + identifier, command
)
restart = re.fullmatch("docker restart -t 10 " + identifier, command)
match = inspect or restart
allowed = Path("/fixtures/allowed_ids").read_text().splitlines()
if not match or match[1] not in allowed:
    raise SystemExit("Command or target denied")
args = (
    ["docker", "inspect", "--format", "{{json .Id}} {{json .State}}", match[1]]
    if inspect
    else ["docker", "restart", "-t", "10", match[1]]
)
raise SystemExit(subprocess.run(args, timeout=30).returncode)
