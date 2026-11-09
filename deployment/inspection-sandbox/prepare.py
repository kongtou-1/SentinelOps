"""Create the broker credential once; never print or overwrite an existing credential."""

import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[2]
target = root / ".local" / "inspection-sandbox.token"
target.parent.mkdir(exist_ok=True)
if not target.exists():
    with target.open("x", encoding="utf-8") as stream:
        stream.write(secrets.token_urlsafe(32))
    target.chmod(0o600)
print("Inspection sandbox credential ready.")
