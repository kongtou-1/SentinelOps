"""Capture provider-reported usage and actual first streamed content."""

import asyncio
import json
import logging
import time

from app.services.operations.usage import finish_gateway, start_gateway

logger = logging.getLogger(__name__)


class UsageCapture:
    def __init__(self, context, index, model, provider_kind, attempt=None):
        self.attempt = attempt or {}
        self.context, self.index, self.model, self.provider_kind = (
            context,
            index,
            model,
            provider_kind,
        )
        self.started = time.monotonic()
        self.key = None
        self.usage = {}
        self.first_content = None
        self.pending = b""
        self.stream_seen, self.stream_done = False, False

    async def start(self):
        self.key = await asyncio.to_thread(
            start_gateway, self.context, self.index, self.model, self.provider_kind, self.attempt
        )

    def observe(self, value, streamed=False):
        if not isinstance(value, dict):
            return
        if isinstance(value.get("usage"), dict):
            self.usage = value["usage"]
        if (
            streamed
            and self.first_content is None
            and any(
                choice.get("delta", {}).get("content")
                for choice in value.get("choices", [])
                if isinstance(choice, dict)
            )
        ):
            self.first_content = time.monotonic() - self.started

    def line(self, line):
        if not line.startswith("data:"):
            return
        self.stream_seen = True
        if line[5:].strip() == "[DONE]":
            self.stream_done = True
            return
        try:
            self.observe(json.loads(line[5:].strip()), streamed=True)
        except (ValueError, TypeError):
            pass

    def feed(self, chunk):
        self.pending += chunk
        while b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            self.line(line.decode(errors="replace"))

    def raw_json(self, raw):
        try:
            self.observe(json.loads(raw))
        except (ValueError, TypeError):
            pass

    async def finish(self, error=None):
        if self.pending:
            self.line(self.pending.decode(errors="replace"))
        if self.stream_seen and not self.stream_done:
            error = error or "stream_incomplete"
        try:
            await asyncio.to_thread(
                finish_gateway,
                self.key,
                self.usage,
                time.monotonic() - self.started,
                self.first_content,
                "failed" if error else "success",
                error,
            )
        except Exception as exc:
            logger.error("Usage settlement unavailable (%s)", type(exc).__name__)
