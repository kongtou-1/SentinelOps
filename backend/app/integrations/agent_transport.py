"""Bounded OpenAI-compatible transport; incomplete tool calls never leave this module."""

import asyncio
import json
import time

import httpx2

from app.utils.redaction import RedactedStream


class ProviderFailure(ValueError):
    def __init__(self, code, retryable=True):
        super().__init__(code)
        self.code, self.retryable = code, retryable


def http_error(status, raw):
    text = raw.decode(errors="replace").lower()
    if "context_length" in text or "maximum context" in text:
        return ProviderFailure("context_limit")
    code = {
        401: "authentication_failed",
        403: "authentication_failed",
        402: "insufficient_balance",
        404: "model_not_found",
        429: "rate_limited",
    }.get(status, "service_error" if status >= 500 else "provider_request_error")
    return ProviderFailure(code, status in (401, 402, 403, 404, 408, 429) or status >= 500)


async def buffered_request(
    body, channel, key, capture, allowed_tools, guard, emit=None, max_bytes=2 * 1024 * 1024
):
    """Return a validated full response, emitting only provisional display text while reading."""
    wire = {**body, "model": channel["model"]}
    if wire.get("stream"):
        wire["stream_options"] = {**wire.get("stream_options", {}), "include_usage": True}
    sanitizer = RedactedStream()
    pending, called, chunks, size = b"", {}, [], 0
    done, meaningful, finished = False, False, False
    draft, last_flush = "", time.monotonic()

    def line_received(line):
        nonlocal done, meaningful, finished, draft, last_flush
        capture.line(line)
        if not line.startswith("data:"):
            return
        raw = line[5:].strip()
        if raw == "[DONE]":
            done = True
            return
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get("error"):
            raise ProviderFailure("invalid_response")
        # Only the transport model alias is normalized; accounting uses the actual channel model.
        value["model"] = body["model"]
        for choice in value.get("choices", []):
            delta = choice.get("delta", {})
            if choice.get("finish_reason") == "length":
                raise ProviderFailure("model_output_limit", False)
            finished |= choice.get("finish_reason") is not None
            meaningful |= bool(delta.get("content") or delta.get("tool_calls"))
            draft += sanitizer.feed(delta.get("content") or "")
            for call in delta.get("tool_calls", []):
                index = call.get("index", 0)
                called[index] = called.get(index, "") + call.get("function", {}).get("name", "")
                if called[index] and not any(n.startswith(called[index]) for n in allowed_tools):
                    raise ProviderFailure("tool_not_allowed", False)
        if emit and draft and time.monotonic() - last_flush >= 0.2:
            emit("assistant_delta", {"text": draft})
            draft, last_flush = "", time.monotonic()
        return "data: " + json.dumps(value, ensure_ascii=False) + "\n\n"

    try:
        async with asyncio.timeout(channel["timeout_seconds"]):
            async with httpx2.AsyncClient(
                timeout=channel["timeout_seconds"], trust_env=False, follow_redirects=False
            ) as client:
                async with client.stream(
                    "POST",
                    channel["base_url"].rstrip("/") + "/chat/completions",
                    headers={"Authorization": "Bearer " + key},
                    json=wire,
                ) as reply:
                    if reply.status_code != 200:
                        raw = bytearray()
                        async for part in reply.aiter_bytes():
                            raw.extend(part[: max(0, 8192 - len(raw))])
                            if len(raw) >= 8192:
                                break
                        raise http_error(reply.status_code, bytes(raw))
                    is_stream = "text/event-stream" in reply.headers.get("content-type", "")
                    async for part in reply.aiter_bytes():
                        await guard()
                        size += len(part)
                        if size > max_bytes:
                            raise ProviderFailure("provider_response_too_large", False)
                        if not is_stream:
                            chunks.append(part)
                            continue
                        pending += part
                        while b"\n" in pending:
                            line, pending = pending.split(b"\n", 1)
                            normalized = line_received(line.decode("utf-8").rstrip("\r"))
                            if normalized:
                                chunks.append(normalized.encode())
                    if is_stream:
                        if pending.strip():
                            normalized = line_received(pending.decode("utf-8").rstrip("\r"))
                            if normalized:
                                chunks.append(normalized.encode())
                        if not done or not meaningful or not finished:
                            raise ProviderFailure("stream_incomplete")
                        if any(name not in allowed_tools for name in called.values()):
                            raise ProviderFailure("tool_not_allowed", False)
                        draft += sanitizer.feed("", final=True)
                        if emit and draft:
                            emit("assistant_delta", {"text": draft})
                        chunks.append(b"data: [DONE]\n\n")
                        return b"".join(chunks), "text/event-stream"
                    raw = b"".join(chunks)
                    capture.raw_json(raw)
                    value = json.loads(raw)
                    if (
                        not isinstance(value, dict)
                        or value.get("error")
                        or not value.get("choices")
                    ):
                        raise ProviderFailure("invalid_response")
                    for choice in value["choices"]:
                        if choice.get("finish_reason") == "length":
                            raise ProviderFailure("model_output_limit", False)
                        message = choice.get("message", {})
                        if not (message.get("content") or message.get("tool_calls")):
                            raise ProviderFailure("invalid_response")
                        if any(
                            c.get("function", {}).get("name") not in allowed_tools
                            for c in message.get("tool_calls", [])
                        ):
                            raise ProviderFailure("tool_not_allowed", False)
                    value["model"] = body["model"]
                    return json.dumps(value, ensure_ascii=False).encode(), "application/json"
    except ProviderFailure:
        raise
    except (TimeoutError, httpx2.TimeoutException):
        raise ProviderFailure("provider_timeout") from None
    except httpx2.HTTPError:
        raise ProviderFailure("provider_unavailable") from None
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        raise ProviderFailure("invalid_response") from None
