"""Only fixed diagnostic/restart commands for the current lab business container."""

import os
import re
import subprocess
from pathlib import Path

command = os.environ.get("SSH_ORIGINAL_COMMAND", "")
identifier = r"([a-f0-9]{64})"
patterns = [
    (
        r"docker inspect --format '\{\{json \.Id\}\} \{\{json \.State\}\}' "
        + identifier,
        ["docker", "inspect", "--format", "{{json .Id}} {{json .State}}"],
    ),
    (
        r"docker inspect --format '\{\{json \.State\}\}' " + identifier,
        ["docker", "inspect", "--format", "{{json .State}}"],
    ),
    (
        r"docker logs --since 15m --tail 200 --timestamps " + identifier,
        ["docker", "logs", "--since", "15m", "--tail", "200", "--timestamps"],
    ),
    (r"docker restart -t 10 " + identifier, ["docker", "restart", "-t", "10"]),
]
allowed = Path("/fixtures/allowed_ids").read_text().splitlines()
for pattern, args in patterns:
    match = re.fullmatch(pattern, command)
    if match and match[1] in allowed:
        raise SystemExit(
            subprocess.run([*args, match[1]], timeout=30, check=False).returncode
        )
raise SystemExit("Command or target denied")
