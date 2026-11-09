"""Create local configuration without printing secrets or replacing existing configuration."""

import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[2]
contents = (root / ".env.example").read_text(encoding="utf-8")
contents = contents.replace(
    "POSTGRES_PASSWORD=\n", f"POSTGRES_PASSWORD={secrets.token_urlsafe(48)}\n"
)
contents = contents.replace("JWT_SECRET=\n", f"JWT_SECRET={secrets.token_urlsafe(48)}\n")
try:
    with (root / ".env").open("x", encoding="utf-8", newline="\n") as file:
        file.write(contents)
except FileExistsError:
    raise SystemExit(".env already exists; nothing changed") from None
print("Created .env with random secrets")
