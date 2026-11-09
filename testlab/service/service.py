"""Disposable business API. Background traffic never invokes the paid provider."""

import json
import os
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

REGISTRY = CollectorRegistry()
REQUESTS = Counter(
    "sentinelops_http_requests_total",
    "Completed API requests",
    ["method", "route", "status_class", "traffic"],
    registry=REGISTRY,
)
DURATION = Histogram(
    "sentinelops_http_request_duration_seconds",
    "API request duration",
    ["method", "route", "traffic"],
    registry=REGISTRY,
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
BUSINESS = Counter(
    "sentinelops_business_requests_total",
    "Explicit business outcomes",
    ["operation", "outcome", "traffic"],
    registry=REGISTRY,
)
LLM_REQUESTS = Counter(
    "sentinelops_llm_requests_total",
    "LLM exchanges",
    ["operation", "model", "outcome", "traffic"],
    registry=REGISTRY,
)
LLM_TOKENS = Counter(
    "sentinelops_llm_tokens_total",
    "Provider reported tokens",
    ["operation", "model", "kind", "traffic"],
    registry=REGISTRY,
)
LLM_USAGE = Counter(
    "sentinelops_llm_usage_observations_total",
    "Usage availability",
    ["operation", "model", "availability", "traffic"],
    registry=REGISTRY,
)
LLM_DURATION = Histogram(
    "sentinelops_llm_duration_seconds",
    "LLM exchange duration",
    ["operation", "model", "traffic"],
    registry=REGISTRY,
)
TTFT = Histogram(
    "sentinelops_llm_ttft_seconds",
    "Time to first content delta",
    ["operation", "model", "traffic"],
    registry=REGISTRY,
    buckets=(0.1, 0.25, 0.5, 1, 2, 3, 5, 10, 30),
)
for operation in ("work", "ask"):
    for traffic in ("business", "probe"):
        for outcome in ("success", "failure"):
            BUSINESS.labels(operation, outcome, traffic)
ROUTES = {
    "/",
    "/health",
    "/ready",
    "/work",
    "/status",
    "/ask",
    "/ask/stream",
    "/v1/chat/completions",
}
for method, routes in (
    ("GET", ("/health", "/ready", "/work", "/status")),
    ("POST", ("/ask", "/v1/chat/completions")),
):
    for route in routes:
        for traffic in ("business", "probe"):
            DURATION.labels(method, route, traffic)
            for status_class in ("2xx", "3xx", "4xx", "5xx"):
                REQUESTS.labels(method, route, status_class, traffic)

STATE = Path("/state/scenario.json")
FAULTS = {
    "healthy",
    "slow",
    "business_error",
    "stuck",
    "cpu",
    "memory",
    "llm_401",
    "llm_429",
    "llm_503",
    "llm_timeout",
    "logic_error",
    "mock_llm",
    "mock_slow",
    "mock_interrupted",
    "mock_missing_usage",
}
STATS = {"requests": 0, "errors": 0, "llm_calls": 0}
MEMORY = []
LOCK = threading.Lock()
START = time.time()


def scenario():
    try:
        return json.loads(STATE.read_text()).get("mode", "healthy")
    except (FileNotFoundError, ValueError):
        return "healthy"


def provider_ready():
    return os.environ.get("LAB_LLM_ENABLED", "false").lower() == "true"


def work():
    mode = scenario()
    if mode == "slow":
        time.sleep(3)
    if mode in ("stuck", "business_error"):
        return 503, {"error": mode}
    if mode == "logic_error":
        return 200, {"ok": False, "error": "injected_logic_error"}
    with LOCK:
        if mode == "memory" and not MEMORY:
            MEMORY.append(bytearray(64 * 1024 * 1024))
        elif mode != "memory":
            MEMORY.clear()
    until = time.perf_counter() + (0.8 if mode == "cpu" else 0.005)
    while time.perf_counter() < until:
        sum(i * i for i in range(100))
    return 200, {"ok": True, "scenario": mode, "version": "1.0.0"}


def _complete(body):
    mode = scenario()
    if mode.startswith("mock_"):
        if mode == "mock_slow":
            time.sleep(0.2)
        return 200, {
            "choices": [{"message": {"content": "OK"}}],
            **(
                {}
                if mode == "mock_missing_usage"
                else {"usage": {"prompt_tokens": 12, "completion_tokens": 2}}
            ),
        }
    if mode in ("llm_401", "llm_429", "llm_503"):
        return int(mode.rsplit("_", 1)[1]), {"error": {"message": "Injected upstream failure"}}
    if mode == "llm_timeout":
        time.sleep(20)
        return 504, {"error": {"message": "Injected upstream timeout"}}
    if not provider_ready():
        return 503, {"error": {"message": "Real provider credentials not configured"}}
    prompt = body.get("messages")
    if not isinstance(prompt, list) or len(json.dumps(prompt)) > 6000:
        return 400, {"error": {"message": "Provide short messages"}}
    payload = {
        "model": os.environ["LAB_LLM_MODEL"],
        "messages": prompt,
        "max_tokens": min(max(int(body.get("max_tokens", 128)), 1), 128),
        "stream": False,
        "thinking": {"type": "disabled"},
    }
    try:
        with LOCK:
            STATS["llm_calls"] += 1
        r = httpx.post(
            os.environ["LAB_LLM_BASE_URL"].rstrip("/") + "/chat/completions",
            headers={
                "Authorization": "Bearer " + Path("/run/secrets/provider_key").read_text().strip()
            },
            json=payload,
            timeout=45,
            follow_redirects=False,
        )
        if r.status_code != 200:
            return r.status_code, {"error": {"message": "Upstream provider rejected request"}}
        return 200, r.json()
    except httpx.TimeoutException:
        return 504, {"error": {"message": "Upstream provider timeout"}}
    except (httpx.RequestError, ValueError):
        return 502, {"error": {"message": "Upstream connection or response failure"}}


def record_llm(started, code, usage, traffic, operation="ask"):
    model = os.environ.get("LAB_LLM_MODEL", "mock")[:100]
    LLM_REQUESTS.labels(operation, model, "success" if code == 200 else "failure", traffic).inc()
    LLM_DURATION.labels(operation, model, traffic).observe(time.perf_counter() - started)
    known = isinstance(usage, dict) and all(
        type(usage.get(key)) is int and usage[key] >= 0
        for key in ("prompt_tokens", "completion_tokens")
    )
    LLM_USAGE.labels(operation, model, "known" if known else "unknown", traffic).inc()
    if known:
        for kind, key in (("input", "prompt_tokens"), ("output", "completion_tokens")):
            LLM_TOKENS.labels(operation, model, kind, traffic).inc(usage[key])


def complete(body, traffic="business"):
    started = time.perf_counter()
    code, result = _complete(body)
    record_llm(started, code, result.get("usage"), traffic)
    return code, result


def stream_content(question):
    """Yield provider events without fabricating first-token timing for JSON responses."""
    mode = scenario()
    if mode.startswith("mock_"):
        if mode == "mock_slow":
            time.sleep(0.2)
        yield {"choices": [{"delta": {"content": "O"}}]}
        if mode == "mock_interrupted":
            raise ValueError("injected_stream_interruption")
        yield {"choices": [{"delta": {"content": "K"}}]}
        if mode != "mock_missing_usage":
            yield {"usage": {"prompt_tokens": 12, "completion_tokens": 2}}
        return
    if not provider_ready():
        raise ValueError("provider_not_configured")
    with httpx.stream(
        "POST",
        os.environ["LAB_LLM_BASE_URL"].rstrip("/") + "/chat/completions",
        headers={
            "Authorization": "Bearer " + Path("/run/secrets/provider_key").read_text().strip()
        },
        json={
            "model": os.environ["LAB_LLM_MODEL"],
            "messages": [{"role": "user", "content": question}],
            "max_tokens": 128,
            "stream": True,
            "stream_options": {"include_usage": True},
            "thinking": {"type": "disabled"},
        },
        timeout=45,
        follow_redirects=False,
    ) as response:
        response.raise_for_status()
        finished = False
        for line in response.iter_lines():
            if line == "data: [DONE]":
                finished = True
                break
            if line.startswith("data: "):
                yield json.loads(line[6:])
        if not finished:
            raise ValueError("stream_incomplete")


class Handler(BaseHTTPRequestHandler):
    def traffic(self):
        return "probe" if self.headers.get("X-SentinelOps-Probe") == "1" else "business"

    def reply(self, status, body, content_type="application/json; charset=utf-8"):
        raw = (
            body.encode()
            if isinstance(body, str)
            else json.dumps(body, ensure_ascii=False).encode()
        )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass
        if self.path != "/metrics":
            route = self.path.split("?", 1)[0]
            route = route if route in ROUTES else "/{unmatched}"
            traffic = "probe" if self.headers.get("X-SentinelOps-Probe") == "1" else "business"
            REQUESTS.labels(self.command, route, str(status // 100) + "xx", traffic).inc()
            DURATION.labels(self.command, route, traffic).observe(
                time.perf_counter() - self.request_started
            )
            if route in ("/work", "/ask"):
                successful = (
                    status == 200
                    and isinstance(body, dict)
                    and not body.get("error")
                    and body.get("ok") is not False
                )
                BUSINESS.labels(
                    "work" if route == "/work" else "ask",
                    "success" if successful else "failure",
                    traffic,
                ).inc()
        with LOCK:
            STATS["requests"] += 1
            STATS["errors"] += status >= 400

    def do_GET(self):
        self.request_started = time.perf_counter()
        if self.path == "/metrics":
            self.reply(200, generate_latest(REGISTRY).decode(), "text/plain; version=0.0.4")
        elif self.path == "/":
            self.reply(
                200,
                """<!doctype html><html lang="zh"><meta charset="utf-8">
<title>实测业务服务</title><style>body{font:16px system-ui;max-width:800px;margin:50px auto;padding:20px}textarea{width:100%;height:100px}button{padding:10px 20px}pre{white-space:pre-wrap;background:#f3f5f7;padding:20px}</style>
<h1>实测业务服务</h1><p>背景流量调用本地 /work；下方问答会调用已配置的真实 LLM。</p>
<p><a href="/status">服务状态</a> · <a href="/work">业务请求</a> · <a href="http://127.0.0.1:8080">运维平台</a></p>
<textarea id="question">请用一句话说明什么是容器健康检查。</textarea><p><button id="send">发送一次问答</button></p><pre id="answer">等待请求</pre>
<script>send.onclick=async()=>{send.disabled=true;answer.textContent='请求中…';try{let r=await fetch('/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:question.value})});answer.textContent=JSON.stringify(await r.json(),null,2)}catch(e){answer.textContent='连接失败'}finally{send.disabled=false}}</script></html>""",
                "text/html; charset=utf-8",
            )
        elif self.path in ("/health", "/ready"):
            mode = scenario()
            failed = mode == "stuck" or (self.path == "/ready" and mode == "business_error")
            self.reply(503 if failed else 200, {"ok": not failed, "scenario": mode})
        elif self.path == "/work":
            self.reply(*work())
        elif self.path == "/status":
            self.reply(
                200,
                {
                    "scenario": scenario(),
                    "version": "1.0.0",
                    "started_at": datetime.fromtimestamp(START, UTC).isoformat(),
                    "llm_configured": provider_ready(),
                    "model": os.environ.get("LAB_LLM_MODEL", ""),
                    **STATS,
                },
            )
        else:
            self.reply(404, {"error": "not_found"})

    def do_POST(self):
        self.request_started = time.perf_counter()
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 8192:
                raise ValueError
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise TypeError
            if self.path in ("/ask", "/ask/stream"):
                question = body.get("question")
                if not isinstance(question, str) or not 1 <= len(question.strip()) <= 2000:
                    raise ValueError
                if self.path == "/ask/stream":
                    self.stream_answer(question)
                    return
                code, result = complete(
                    {"messages": [{"role": "user", "content": question}]}, self.traffic()
                )
                if code == 200:
                    result = {
                        "answer": result["choices"][0]["message"]["content"],
                        "usage": result.get("usage"),
                        "model": os.environ.get("LAB_LLM_MODEL", "mock"),
                    }
                self.reply(code, result)
            elif self.path == "/v1/chat/completions":
                expected = "Bearer " + Path("/run/secrets/gateway_key").read_text().strip()
                if self.headers.get("Authorization") != expected:
                    self.reply(401, {"error": {"message": "Gateway authentication failed"}})
                    return
                self.reply(*complete(body))
            else:
                self.reply(404, {"error": "not_found"})
        except (ValueError, KeyError, TypeError):
            self.reply(400, {"error": "invalid_request"})

    def stream_answer(self, question):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        traffic, first, usage, code = self.traffic(), False, None, 502
        try:
            for event in stream_content(question):
                if event.get("error"):
                    raise ValueError("provider_error")
                for choice in event.get("choices", []):
                    content = choice.get("delta", {}).get("content")
                    if content and not first:
                        TTFT.labels(
                            "ask", os.environ.get("LAB_LLM_MODEL", "mock")[:100], traffic
                        ).observe(time.perf_counter() - self.request_started)
                        first = True
                if "usage" in event:
                    usage = event["usage"]
                self.wfile.write(
                    ("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode()
                )
                self.wfile.flush()
            if not first:
                raise ValueError("empty_stream")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            code = 200
        except (ValueError, KeyError, httpx.HTTPError, OSError):
            try:
                self.wfile.write(b'event: error\ndata: {"error":"stream_failed"}\n\n')
                self.wfile.flush()
            except OSError:
                pass
        finally:
            record_llm(self.request_started, code, usage, traffic)
            BUSINESS.labels("ask", "success" if code == 200 else "failure", traffic).inc()
            REQUESTS.labels("POST", "/ask/stream", "2xx" if code == 200 else "5xx", traffic).inc()
            DURATION.labels("POST", "/ask/stream", traffic).observe(
                time.perf_counter() - self.request_started
            )

    def log_message(self, fmt, *args):
        if self.path not in ("/health", "/ready"):
            print(
                json.dumps(
                    {
                        "time": datetime.now(UTC).isoformat(),
                        "path": self.path,
                        "scenario": scenario(),
                        "http": str(args[1]) if len(args) > 1 else "",
                    }
                ),
                flush=True,
            )


if __name__ == "__main__":
    # A stuck runtime is restart-recoverable; source/logic errors persist until reset.
    if scenario() == "stuck":
        STATE.write_text(json.dumps({"mode": "healthy"}))
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
