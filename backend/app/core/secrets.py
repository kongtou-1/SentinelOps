from pathlib import Path

from cryptography.fernet import Fernet
from fastapi import HTTPException

from app.core.config import get_settings


def file_secret(path: Path) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
        if len(value) < 32:
            raise ValueError
        return value
    except (OSError, ValueError):
        raise HTTPException(503, "Required service secret is unavailable") from None


def cipher():
    try:
        return Fernet(file_secret(get_settings().credential_key_file).encode())
    except ValueError:
        raise HTTPException(503, "Credential encryption key is invalid") from None
