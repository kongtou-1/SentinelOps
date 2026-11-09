import importlib.util
import json
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread


def test_business_failure_stream_timing_and_unknown_usage(tmp_path):
    path = Path(__file__).resolve().parents[2] / "testlab/service/service.py"
    spec = importlib.util.spec_from_file_location("business_telemetry_target", path)
    target = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(target)
    target.STATE = tmp_path / "scenario.json"
    server = ThreadingHTTPServer(("127.0.0.1", 0), target.Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def call(route, body=None):
        connection = HTTPConnection(*server.server_address, timeout=5)
        connection.request(
            "GET" if body is None else "POST",
            route,
            body=None if body is None else json.dumps(body),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        status, raw = response.status, response.read().decode()
        connection.close()
        return status, raw

    try:
        target.STATE.write_text(json.dumps({"mode": "logic_error"}))
        status, raw = call("/work")
        assert status == 200 and json.loads(raw)["ok"] is False
        assert target.BUSINESS.labels("work", "failure", "business")._value.get() == 1
        target.STATE.write_text(json.dumps({"mode": "mock_slow"}))
        status, raw = call("/ask/stream", {"question": "health"})
        assert status == 200 and "data: [DONE]" in raw
        metric = target.TTFT.labels("ask", "mock", "business")
        assert metric._sum.get() >= 0.19
        target.STATE.write_text(json.dumps({"mode": "mock_missing_usage"}))
        assert call("/ask", {"question": "health"})[0] == 200
        assert target.LLM_USAGE.labels("ask", "mock", "unknown", "business")._value.get() == 1
        target.STATE.write_text(json.dumps({"mode": "mock_interrupted"}))
        _, raw = call("/ask/stream", {"question": "health"})
        assert "event: error" in raw and "data: [DONE]" not in raw
        assert target.BUSINESS.labels("ask", "failure", "business")._value.get() == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
