"""Fixed read-only Docker lifecycle collector. Only /metrics is exposed.

The Engine socket cannot enforce HTTP permissions; this process permits GET on
two fixed endpoints only and never accepts Engine paths or commands from clients.
"""

import http.client
import json
import re
import socket
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlencode

API = "/v1.47"
WINDOW = 300
MAX_EVENTS = 20000


def identity_labels(container):
    labels = container.get("Labels") or {}
    result = {}
    image_id = container.get("ImageID", "")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        result["image_id"] = image_id
    for target, source, pattern in (
        ("revision", "org.opencontainers.image.revision", r"(?:[0-9a-f]{40}|[0-9a-f]{64})"),
        ("application", "sentinelops.application", r"[a-z][a-z0-9_.-]{0,99}"),
        ("release", "sentinelops.release", r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}"),
        ("compose_project", "com.docker.compose.project", r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}"),
        ("compose_service", "com.docker.compose.service", r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}"),
    ):
        value = labels.get(source, "")
        if isinstance(value, str) and re.fullmatch(pattern, value):
            result[target] = value
    return result


def health_status(container):
    if container.get("State") != "running":
        return "stopped"
    state = (container.get("Health") or {}).get("Status")
    if state in ("healthy", "unhealthy", "starting", "none"):
        return state
    status = container.get("Status", "")
    match = re.search(r"\((healthy|unhealthy|health: starting)\)$", status)
    if match:
        return match[1].removeprefix("health: ")
    return "none" if status.startswith("Up ") and "(" not in status else "unknown"


class EngineConnection(http.client.HTTPConnection):
    def __init__(self):
        super().__init__("localhost", timeout=3)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect("/var/run/docker.sock")


def read_engine(endpoint, params):
    if endpoint not in ("/containers/json", "/events"):
        raise ValueError("Engine endpoint rejected")
    connection = EngineConnection()
    try:
        connection.request("GET", API + endpoint + "?" + urlencode(params))
        response = connection.getresponse()
        raw = response.read(8 * 1024 * 1024 + 1)
        if response.status != 200 or len(raw) > 8 * 1024 * 1024:
            raise ValueError("Engine response unavailable")
        return (
            [json.loads(line) for line in raw.splitlines() if line]
            if endpoint == "/events"
            else json.loads(raw)
        )
    finally:
        connection.close()


class Collector:
    def __init__(self):
        self.lock = threading.Lock()
        self.containers = {}
        self.started = set()
        self.events = deque()
        self.seen = set()
        self.cursor = None
        self.since = None
        self.updated_at = 0
        self.healthy = False

    def poll(self, now=None):
        now = int(now if now is not None else time.time())
        try:
            if self.cursor is not None and now - self.cursor > 15:
                with self.lock:
                    self.cursor, self.since = None, None
                    self.events.clear()
                    self.seen.clear()
            containers = read_engine("/containers/json", {"all": "true"})
            if not isinstance(containers, list) or len(containers) > 2000:
                raise ValueError("Inventory limit exceeded")
            if self.cursor is None:
                events = []
            else:
                events = read_engine(
                    "/events",
                    {
                        "since": self.cursor,
                        "until": now - 1,
                        "filters": json.dumps(
                            {
                                "type": ["container"],
                                "event": ["create", "start", "die", "oom", "destroy"],
                            }
                        ),
                    },
                )
                # Docker retains only the last 256 historical events. A saturated
                # response cannot establish completeness, even if some were duplicates.
                if len(events) >= 256:
                    raise ValueError("Event history truncated")
            with self.lock:
                if self.cursor is None:
                    self.started = {c["Id"] for c in containers if c.get("State") != "created"}
                    self.since = now
                for event in sorted(
                    events, key=lambda e: e.get("timeNano", e.get("time", 0) * 10**9)
                ):
                    identifier = event.get("Actor", {}).get("ID", event.get("id", ""))
                    action = event.get("Action", event.get("status"))
                    timestamp = event.get("timeNano", event.get("time", 0) * 10**9) / 10**9
                    identity = (timestamp, identifier, action)
                    if identity in self.seen:
                        continue
                    self.seen.add(identity)
                    self.events.append(identity)
                    if action in ("create", "destroy"):
                        self.started.discard(identifier)
                    elif action == "die":
                        self.started.add(identifier)
                    elif action == "start":
                        # First startup is not a restart; manual and automatic
                        # restarts each have one subsequent start event.
                        if identifier in self.started:
                            self.events.append((timestamp, identifier, "counted_restart"))
                        self.started.add(identifier)
                while self.events and self.events[0][0] < now - WINDOW:
                    self.seen.discard(self.events.popleft())
                if len(self.events) > MAX_EVENTS:
                    raise ValueError("Event window limit exceeded")
                self.containers = {c["Id"]: c for c in containers}
                self.cursor, self.updated_at, self.healthy = now - 1, now, True
        except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
            with self.lock:
                self.healthy, self.cursor, self.since = False, None, None
                self.events.clear()
                self.seen.clear()

    def metrics(self, now=None):
        now = now if now is not None else time.time()
        with self.lock:
            healthy = self.healthy and now - self.updated_at <= 15
            complete = healthy and self.since is not None and now - self.since >= WINDOW
            lines = [
                "# TYPE docker_state_up gauge",
                f"docker_state_up {int(healthy)}",
                "# TYPE docker_state_window_complete gauge",
                f"docker_state_window_complete {int(complete)}",
                "# TYPE docker_state_last_poll_timestamp_seconds gauge",
                f"docker_state_last_poll_timestamp_seconds {self.updated_at}",
                "# TYPE docker_container_identity_info gauge",
            ]
            if healthy:
                for identifier, container in self.containers.items():
                    labels = {
                        "id": "/docker/" + identifier,
                        "docker_id": identifier,
                        "name": (container.get("Names") or [identifier])[0].lstrip("/"),
                        "image": container.get("Image", ""),
                    }
                    text = ",".join(
                        k + "=" + json.dumps(v, ensure_ascii=False) for k, v in labels.items()
                    )
                    health = health_status(container)
                    state = container.get("State", "unknown")
                    if state not in (
                        "running",
                        "exited",
                        "created",
                        "restarting",
                        "paused",
                        "removing",
                        "dead",
                    ):
                        state = "unknown"
                    lines.append(f'docker_container_state_info{{{text},state="{state}"}} 1')
                    # Publish membership only; no environment variables or arbitrary labels.
                    mounts = container.get("Mounts")
                    if (
                        isinstance(mounts, list)
                        and len(mounts) <= 64
                        and all(
                            isinstance(m, dict)
                            and all(
                                isinstance(m.get(k), str)
                                and len(m[k]) <= 1024
                                and all(ord(c) >= 32 for c in m[k])
                                for k in ("Source", "Destination")
                            )
                            for m in mounts
                        )
                    ):
                        lines.append(f"docker_container_mount_inventory{{{text}}} 1")
                        for mount in mounts:
                            if not isinstance(mount, dict):
                                continue
                            source, destination = mount.get("Source"), mount.get("Destination")
                            if (
                                not isinstance(source, str)
                                or not isinstance(destination, str)
                                or max(len(source), len(destination)) > 1024
                            ):
                                continue
                            extra = {
                                "source": source,
                                "destination": destination,
                                "type": str(mount.get("Type", ""))[:20],
                                "read_only": str(not mount.get("RW", False)).lower(),
                            }
                            mount_text = ",".join(
                                k + "=" + json.dumps(v, ensure_ascii=False)
                                for k, v in extra.items()
                            )
                            lines.append(f"docker_container_mount_info{{{text},{mount_text}}} 1")
                    lines.append(f"docker_container_network_inventory{{{text}}} 1")
                    for network in container.get("NetworkSettings", {}).get("Networks") or {}:
                        if isinstance(network, str) and len(network) <= 200:
                            network_text = json.dumps(network, ensure_ascii=False)
                            lines.append(
                                f"docker_container_network_info{{{text},network={network_text}}} 1"
                            )
                    identity = identity_labels(container)
                    if identity:
                        identity_text = ",".join(
                            k + "=" + json.dumps(v)
                            for k, v in {
                                "id": "/docker/" + identifier,
                                "docker_id": identifier,
                                **identity,
                            }.items()
                        )
                        lines.append(f"docker_container_identity_info{{{identity_text}}} 1")
                    lines.append(f'docker_container_health_status{{{text},health="{health}"}} 1')
                    # Missing or inconclusive health checks cannot prove recovery.
                    if health in ("healthy", "unhealthy"):
                        lines.append(
                            f"docker_container_unhealthy{{{text}}} {int(health == 'unhealthy')}"
                        )
                    if not complete:
                        continue
                    for name, action in (
                        ("docker_container_restarts_5m", "counted_restart"),
                        ("docker_container_oom_5m", "oom"),
                    ):
                        count = sum(
                            t > now - WINDOW and identity == identifier and kind == action
                            for t, identity, kind in self.events
                        )
                        lines.append(f"{name}{{{text}}} {count}")
            return "\n".join(lines) + "\n"


def main():
    collector = Collector()

    def polling():
        while True:
            collector.poll()
            time.sleep(5)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/metrics":
                self.send_error(404)
                return
            body = collector.metrics().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    threading.Thread(target=polling, daemon=True).start()
    HTTPServer(("0.0.0.0", 9400), Handler).serve_forever()


if __name__ == "__main__":
    main()
