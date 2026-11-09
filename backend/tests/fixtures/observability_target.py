"""Local Docker acceptance target with independently healthy runtime and business faults."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOCK = threading.Lock()
MODE = "healthy"
BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5)
COUNTS, DURATIONS = {}, {}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        global MODE
        started = time.perf_counter()
        if self.path == "/health":
            return self.reply(200, {"healthy": True})
        if self.path == "/metrics":
            with LOCK:
                lines = [
                    "# TYPE sentinelops_http_requests_total counter",
                    "# TYPE sentinelops_http_request_duration_seconds histogram",
                ]
                for traffic in ("business", "probe"):
                    labels = f'method="GET",route="/work",traffic="{traffic}"'
                    for status in ("2xx", "5xx"):
                        count = COUNTS.get((traffic, status), 0)
                        lines.append(
                            f"sentinelops_http_requests_total{{{labels},"
                            f'status_class="{status}"}} {count}'
                        )
                    values = DURATIONS.get(traffic, [])
                    for bound in (*BUCKETS, "+Inf"):
                        count = sum(v <= float(bound) for v in values)
                        lines.append(
                            f"sentinelops_http_request_duration_seconds_bucket"
                            f'{{{labels},le="{bound}"}} {count}'
                        )
                    lines.append(
                        f"sentinelops_http_request_duration_seconds_sum{{{labels}}} {sum(values)}"
                    )
                    lines.append(
                        f"sentinelops_http_request_duration_seconds_count{{{labels}}} {len(values)}"
                    )
            return self.reply(200, "\n".join(lines) + "\n", "text/plain")
        if self.path != "/work":
            return self.reply(404, {})
        if MODE == "slow":
            time.sleep(0.3)
        else:
            time.sleep(0.005)
        code = 500 if MODE == "http_error" else 200
        traffic = "probe" if self.headers.get("X-SentinelOps-Probe") == "1" else "business"
        with LOCK:
            COUNTS[(traffic, str(code // 100) + "xx")] = (
                COUNTS.get((traffic, str(code // 100) + "xx"), 0) + 1
            )
            DURATIONS.setdefault(traffic, []).append(time.perf_counter() - started)
        self.reply(code, {"ok": MODE not in ("logic_error", "http_error")})

    def do_POST(self):
        global MODE
        if self.path != "/scenario":
            return self.reply(404, {})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        if body.get("mode") not in ("healthy", "logic_error", "http_error", "slow"):
            return self.reply(400, {})
        MODE = body["mode"]
        self.reply(200, {"mode": MODE})

    def reply(self, code, body, content_type="application/json"):
        raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
