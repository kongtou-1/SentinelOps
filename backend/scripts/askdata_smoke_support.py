"""Local credentials for authenticated smoke checks; never log their values."""

import json
import os
from pathlib import Path


def admin_credentials(path: str | None = None) -> dict[str, str]:
    if path:
        try:
            values = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ValueError("Cannot read the local credentials JSON file") from None
    else:
        values = {
            "username": os.environ.get("ASKDATA_SMOKE_USERNAME", "admin"),
            "password": os.environ.get("ASKDATA_SMOKE_PASSWORD", ""),
        }
    if not isinstance(values, dict) or not all(
        isinstance(values.get(key), str) and values[key].strip() for key in ("username", "password")
    ):
        raise ValueError("Provide --credentials-file or ASKDATA_SMOKE_PASSWORD")
    return {key: values[key] for key in ("username", "password")}
