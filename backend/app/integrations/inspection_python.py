"""No host execution fallback. Only the separately deployed container broker runs code."""

import httpx2
from fastapi import HTTPException

from app.core.config import get_settings
from app.core.secrets import file_secret


def execute(code, data):
    settings = get_settings()
    if not settings.inspection_sandbox_url:
        raise ValueError("inspection_sandbox_unavailable")
    try:
        with httpx2.Client(timeout=12, trust_env=False, follow_redirects=False) as client:
            with client.stream(
                "POST",
                settings.inspection_sandbox_url.rstrip("/") + "/execute",
                headers={
                    "Authorization": "Bearer " + file_secret(settings.inspection_sandbox_token_file)
                },
                json={"code": code, "data": data},
            ) as response:
                if response.status_code != 200:
                    raise ValueError("inspection_sandbox_rejected")
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 65536:
                        raise ValueError("inspection_python_output_limit")
        import json

        return json.loads(raw)
    except (httpx2.HTTPError, OSError, HTTPException):
        raise ValueError("inspection_sandbox_unavailable") from None
