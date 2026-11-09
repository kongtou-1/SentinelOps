"""Disposable local HTTP latency and memory-limited OOM target."""

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

slow = False


def exhaust_memory():
    # The acceptance Compose service is limited to 48 MiB. No host-sized allocation.
    allocation = bytearray(128 * 1024 * 1024)
    allocation[0] = 1


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        time.sleep(0.4 if slow else 0.01)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ready\n")

    def do_POST(self):
        global slow
        if self.path in ("/slow", "/normal"):
            slow = self.path == "/slow"
        elif self.path == "/oom":
            threading.Thread(target=exhaust_memory, daemon=True).start()
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.end_headers()

    def log_message(self, *_args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8003), Handler).serve_forever()
