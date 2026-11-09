"""Local-test Chat Completions provider; no external inference or real key."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

status = 200


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, payload):
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.reply(200, {"status": status})

    def do_POST(self):
        global status
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        except (ValueError, TypeError):
            self.reply(400, {"error": "invalid body"})
            return
        if self.path == "/scenario":
            if body.get("status") not in (200, 401, 402, 429, 500, 503):
                self.reply(400, {"error": "unsupported scenario"})
                return
            status = body["status"]
            self.reply(200, {"status": status})
        elif self.path == "/chat/completions":
            if status != 200:
                self.reply(status, {"error": {"message": "local simulated provider failure"}})
            elif body.get("messages") != [{"role": "user", "content": "Return only OK."}]:
                self.reply(400, {"error": "unexpected probe"})
            else:
                self.reply(
                    200,
                    {
                        "choices": [{"message": {"content": "OK"}}],
                        "usage": {"prompt_tokens": 7, "completion_tokens": 1},
                    },
                )
        else:
            self.reply(404, {"error": "not found"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8002), Handler).serve_forever()
