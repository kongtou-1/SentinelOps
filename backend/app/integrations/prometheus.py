import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import HTTPException

from app.core.config import get_settings


def prometheus(path: str, params: dict | None = None, method="GET", timeout_seconds=None) -> dict:
    settings = get_settings()
    url = settings.prometheus_url.rstrip("/") + path
    if params:
        url += "?" + urlencode(params)
    try:
        request = Request(url, data=b"" if method == "POST" else None, method=method)
        with urlopen(
            request, timeout=timeout_seconds or settings.monitoring_timeout_seconds
        ) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError
        if path == "/-/reload":
            return {"reloaded": True}
        payload = json.loads(raw)
        if payload.get("status") != "success":
            raise ValueError
        return payload["data"]
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        raise HTTPException(502, "Prometheus request failed") from None
