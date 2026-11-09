"""Bearer authentication for internal service endpoints."""

import secrets
from pathlib import Path

from fastapi import HTTPException

from app.core.secrets import file_secret


def service_authorization(authorization: str | None, path: Path):
    expected = file_secret(path)
    if not authorization or not secrets.compare_digest(
        authorization.encode(), f"Bearer {expected}".encode()
    ):
        raise HTTPException(401, "Invalid service authorization")
