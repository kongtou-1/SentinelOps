"""Token flow tests use a real local HTTP target and need no database."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from pydantic import ValidationError

from app.integrations import service_probes
from app.integrations.service_probes import probe, request_config
from app.integrations.service_token_auth import TokenSession
from app.schemas.monitoring import MonitorInput, MonitorTestInput
from app.services.monitoring import service_monitors
from app.services.monitoring.service_probes import ServiceProbes


@pytest.fixture
def token_target():
    state = {
        "requests": [],
        "login_status": 200,
        "refresh_status": 200,
        "work_status": 200,
        "reject_once": False,
        "generation": 0,
        "nested": False,
        "invalid": False,
        "omit_rotation": False,
        "omit_refresh": False,
        "expires_in": 3600,
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.reply()

        def do_POST(self):
            self.reply()

        def reply(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = json.loads(raw) if raw else None
            state["requests"].append((self.path, dict(self.headers), body))
            if self.path in {"/login", "/refresh"}:
                stage = self.path[1:]
                status = state[f"{stage}_status"]
                state["generation"] += 1
                payload = {
                    "access_token": f"access-private-{state['generation']}",
                    "refresh_token": f"refresh-private-{state['generation']}",
                    "expires_in": state["expires_in"],
                }
                if state["invalid"]:
                    payload["access_token"] = "bad\nsecret"
                if state["omit_refresh"] or (stage == "refresh" and state["omit_rotation"]):
                    payload.pop("refresh_token")
                if state["nested"]:
                    payload = {"data": payload}
            else:
                status = state["work_status"]
                if state["reject_once"]:
                    status, state["reject_once"] = 401, False
                if (
                    self.headers.get("Authorization")
                    != f"Bearer access-private-{state['generation']}"
                ):
                    status = 401
                payload = {"ok": True}
            encoded = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", state
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.fixture
def encryption(monkeypatch):
    key = Fernet(Fernet.generate_key())
    monkeypatch.setattr(service_probes, "cipher", lambda: key)
    monkeypatch.setattr(service_monitors, "cipher", lambda: key)
    return key


def configuration(url, **auth):
    return MonitorInput(
        name="Authenticated API",
        target=url + "/work",
        assertions={"ok": True},
        token_auth={
            "login_url": url + "/login",
            "login_body": {"password": "private-password"},
            "refresh_url": url + "/refresh",
            **auth,
        },
    )


def monitor(body):
    return SimpleNamespace(
        **body.model_dump(exclude={"headers", "request_body", "token_auth"}),
        encrypted_request=service_monitors.encrypted_request(body),
    )


def paths(state):
    return [request[0] for request in state["requests"]]


def test_login_reuse_expiry_refresh_rotation_and_redaction(token_target, encryption):
    url, state = token_target
    row = monitor(configuration(url, refresh_body={"client_id": "monitor"}))
    session = TokenSession()

    async def exercise():
        first = await probe(row, token_session=session)
        assert first.success
        assert (await probe(row, token_session=session)).success
        assert paths(state) == ["/login", "/work", "/work"]
        session.expires_at = 0
        assert (await probe(row, token_session=session)).success
        assert state["requests"][-2][2] == {
            "client_id": "monitor",
            "refresh_token": "refresh-private-1",
        }
        assert session.refresh_token == "refresh-private-2"
        session.expires_at = 0
        state["omit_rotation"] = True
        assert (await probe(row, token_session=session)).success
        assert session.refresh_token == "refresh-private-2"
        assert "private" not in repr(session)
        assert "private" not in json.dumps(first.evidence)

    asyncio.run(exercise())
    assert all(headers["X-SentinelOps-Probe"] == "1" for _, headers, _ in state["requests"])
    assert "private-password" not in row.encrypted_request


@pytest.mark.parametrize("refresh_status", [400, 401, 403])
def test_expired_refresh_falls_back_to_login(token_target, encryption, refresh_status):
    url, state = token_target
    row, session = monitor(configuration(url)), TokenSession()

    async def exercise():
        assert (await probe(row, token_session=session)).success
        state["refresh_status"] = refresh_status
        session.expires_at = 0
        assert (await probe(row, token_session=session)).success

    asyncio.run(exercise())
    assert paths(state) == ["/login", "/work", "/refresh", "/login", "/work"]


def test_401_refreshes_once_and_persistent_401_stops(token_target, encryption):
    url, state = token_target
    row, session = monitor(configuration(url)), TokenSession()

    async def exercise():
        state["reject_once"] = True
        assert (await probe(row, token_session=session)).success
        assert paths(state) == ["/login", "/work", "/refresh", "/work"]
        state["requests"].clear()
        state["work_status"] = 401
        result = await probe(row, token_session=session)
        assert result.error_code == "auth_rejected" and result.http_status == 401
        assert session.access_token is None

    asyncio.run(exercise())
    assert paths(state) == ["/work", "/refresh", "/work"]


def test_refresh_server_error_does_not_hide_failure_with_login(token_target, encryption):
    url, state = token_target
    row, session = monitor(configuration(url)), TokenSession()

    async def exercise():
        assert (await probe(row, token_session=session)).success
        state["requests"].clear()
        state["refresh_status"] = 500
        session.expires_at = 0
        result = await probe(row, token_session=session)
        assert result.error_code == "auth_refresh_failed"
        assert result.http_status == 500
        assert session.access_token is None and session.refresh_token is None

    asyncio.run(exercise())
    assert paths(state) == ["/refresh"]


@pytest.mark.parametrize("status", [403, 500, 302])
def test_non_401_target_errors_do_not_trigger_auth_retry(token_target, encryption, status):
    url, state = token_target
    state["work_status"] = status
    result = asyncio.run(probe(monitor(configuration(url))))
    assert result.error_code == "unexpected_status" and result.http_status == status
    assert paths(state) == ["/login", "/work"]


def test_nested_fields_and_no_refresh_endpoint_relogin(token_target, encryption):
    url, state = token_target
    state["nested"] = True
    row = monitor(
        configuration(
            url,
            refresh_url="",
            access_token_path="data.access_token",
            refresh_token_path="data.refresh_token",
            expires_in_path="data.expires_in",
        )
    )
    session = TokenSession()

    async def exercise():
        assert (await probe(row, token_session=session)).success
        session.expires_at = 0
        assert (await probe(row, token_session=session)).success

    asyncio.run(exercise())
    assert paths(state) == ["/login", "/work", "/login", "/work"]


@pytest.mark.parametrize(
    "change,code",
    [
        ({"login_status": 401}, "auth_login_failed"),
        ({"login_status": 302}, "auth_login_failed"),
        ({"invalid": True}, "auth_invalid_response"),
        ({"expires_in": -1}, "auth_invalid_response"),
        ({"expires_in": True}, "auth_invalid_response"),
    ],
)
def test_auth_failure_never_calls_target_or_exposes_secrets(token_target, encryption, change, code):
    url, state = token_target
    state.update(change)
    result = asyncio.run(probe(monitor(configuration(url))))
    assert result.error_code == code and result.success is False
    assert "private" not in repr(result)
    assert paths(state) == ["/login"]


@pytest.mark.parametrize("refresh_status,expected", [(200, True), (401, False), (500, False)])
def test_draft_checks_refresh_without_login_fallback(
    token_target, encryption, refresh_status, expected
):
    url, state = token_target
    state["refresh_status"] = refresh_status
    body = MonitorTestInput(**configuration(url).model_dump())
    result = asyncio.run(service_monitors.test_probe(None, body))
    assert result["success"] is expected
    assert result["refresh_tested"]
    assert paths(state).count("/login") == 1
    assert "private" not in json.dumps(result)


def test_draft_reports_missing_refresh_token(token_target, encryption):
    url, state = token_target
    state["omit_refresh"] = True
    result = asyncio.run(
        service_monitors.test_probe(None, MonitorTestInput(**configuration(url).model_dump()))
    )
    assert result["error_code"] == "auth_missing_refresh_token"


def test_preserve_replace_clear_config_and_runtime_cache(encryption):
    body = configuration("https://example.com")
    row = monitor(body)
    plain = MonitorInput(name="Updated", target=body.target)
    preserved = service_monitors.encrypted_request(plain, row)
    assert request_config(SimpleNamespace(encrypted_request=preserved))["token_auth"]
    cleared = plain.model_copy(update={"token_auth": False})
    assert service_monitors.encrypted_request(cleared, row) is None
    runtime = ServiceProbes()
    runtime.token_sessions["monitor"] = TokenSession(access_token="private")
    runtime.forget("monitor")
    assert not runtime.token_sessions
    with pytest.raises(HTTPException):
        service_monitors.encrypted_request(
            MonitorInput(name="TCP", protocol="tcp", target="example.com:80"), row
        )


@pytest.mark.parametrize(
    "auth",
    [
        {"login_url": "https://user:password@example.com/login"},
        {"refresh_url": "https://example.com/refresh?token=secret"},
        {"headers": {"Authorization": "bad\nvalue"}},
        {"headers": {"Host": "other"}},
        {"access_token_path": "data[token]"},
    ],
)
def test_invalid_auth_config_rejected(auth):
    with pytest.raises(ValidationError):
        configuration("https://example.com", **auth)
