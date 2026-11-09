"""Read-only inspection protocol over captured testlab responses, never a live proxy."""

import json
import os
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def page(records, query):
    start = datetime.fromisoformat(query["updated_after"][0])
    end = datetime.fromisoformat(query["updated_before"][0])
    limit = int(query.get("limit", ["50"])[0])
    offset = int(query.get("cursor", ["0"])[0])
    if not start.tzinfo or not end.tzinfo or start >= end:
        raise ValueError("invalid_window")
    if not 1 <= limit <= 50 or offset < 0:
        raise ValueError("invalid_page")
    selected = sorted(
        (row for row in records if start <= datetime.fromisoformat(row["updated_at"]) < end),
        key=lambda row: (row["updated_at"], row["id"]),
    )
    items = selected[offset : offset + limit]
    next_offset = offset + len(items)
    return {
        "items": items,
        "next_cursor": str(next_offset) if next_offset < len(selected) else None,
    }


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, value):
        raw = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/health":
            self.reply(200, {"ok": True})
            return
        kinds = {"/records": "observed", "/acceptance-records": "replays"}
        if url.path not in kinds:
            self.reply(404, {"error": "not_found"})
            return
        try:
            data = json.loads(self.server.snapshot.read_text(encoding="utf-8"))
            self.reply(200, page(data[kinds[url.path]], parse_qs(url.query)))
        except (KeyError, ValueError, IndexError):
            self.reply(400, {"error": "invalid_inspection_query"})

    def do_POST(self):
        self.reply(405, {"error": "read_only"})

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST

    def log_message(self, *_):
        pass


def main():
    server = ThreadingHTTPServer(("0.0.0.0", 8090), Handler)
    server.snapshot = Path(os.environ.get("INSPECTION_SNAPSHOT", "/samples/snapshot.json"))
    server.serve_forever()


if __name__ == "__main__":
    main()
