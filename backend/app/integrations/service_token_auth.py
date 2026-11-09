"""JSON login/refresh for service probes; tokens stay in the probe runtime only."""

import json
import math
import time
from dataclasses import dataclass, field

import httpx2


class TokenAuthError(Exception):
    def __init__(self, code, http_status=None):
        self.code, self.http_status = code, http_status
        super().__init__(code)


def response_value(payload, path, default=None):
    for part in path.split("."):
        try:
            payload = payload[int(part)] if isinstance(payload, list) else payload[part]
        except (KeyError, IndexError, TypeError, ValueError):
            return default
    return payload


def valid_token(value):
    return (
        isinstance(value, str)
        and 0 < len(value) <= 16384
        and all(33 <= ord(c) <= 126 for c in value)
    )


@dataclass
class TokenSession:
    access_token: str | None = field(default=None, repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    expires_at: float = 0
    allow_login_fallback: bool = True

    def clear(self):
        self.access_token = self.refresh_token = None
        self.expires_at = 0

    async def exchange(self, client, config, refresh=False):
        stage = "refresh" if refresh else "login"
        body = dict(config[f"{stage}_body"])
        if refresh:
            body[config["refresh_token_field"]] = self.refresh_token
        headers = {**config["headers"], "X-SentinelOps-Probe": "1"}
        try:
            async with client.stream(
                "POST", config[f"{stage}_url"], json=body, headers=headers
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise TokenAuthError(f"auth_{stage}_failed", response.status_code)
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 65536:
                        raise TokenAuthError("auth_invalid_response")
        except httpx2.TimeoutException:
            raise TokenAuthError("auth_timeout") from None
        except httpx2.HTTPError:
            raise TokenAuthError("auth_connection_failed") from None
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            raise TokenAuthError("auth_invalid_response") from None
        access = response_value(payload, config["access_token_path"])
        rotation = response_value(payload, config["refresh_token_path"])
        lifetime = response_value(payload, config["expires_in_path"], config["default_expires_in"])
        if (
            not valid_token(access)
            or (rotation is not None and not valid_token(rotation))
            or isinstance(lifetime, bool)
            or not isinstance(lifetime, (int, float))
            or not math.isfinite(lifetime)
            or lifetime <= 0
        ):
            raise TokenAuthError("auth_invalid_response")
        self.access_token = access
        self.refresh_token = rotation or (self.refresh_token if refresh else None)
        # Refresh shortly before expiry without exhausting very short lived tokens.
        self.expires_at = time.monotonic() + lifetime - min(30, lifetime * 0.1)

    async def token(self, client, config, force=False):
        if not force and self.access_token and time.monotonic() < self.expires_at:
            return self.access_token
        try:
            if self.refresh_token and config["refresh_url"]:
                try:
                    await self.exchange(client, config, refresh=True)
                    return self.access_token
                except TokenAuthError as exc:
                    if not self.allow_login_fallback or exc.http_status not in {400, 401, 403}:
                        raise
                    self.clear()
            await self.exchange(client, config)
            return self.access_token
        except BaseException:
            self.clear()
            raise
