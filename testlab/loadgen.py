"""Small real HTTP load, with no LLM calls and a maximum of five requests/second."""

import json
import math
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

STATE = Path("/state")
started = time.monotonic()
count = errors = 0
while True:
    config = json.loads((STATE / "traffic.json").read_text())
    mode = config.get("mode", "steady")
    elapsed = time.monotonic() - started
    rates = {
        "paused": 0,
        "steady": 0.2,
        "ramp": min(5, 0.2 + (elapsed % 600) / 120),
        "cycle": 0.2 + 1.8 * (1 + math.sin(2 * math.pi * elapsed / 600)) / 2,
        "burst": 5 if elapsed % 120 < 15 else 0.2,
    }
    rate = rates.get(mode, 0.2)
    begin = time.monotonic()
    if rate:
        try:
            with urlopen("http://testlab-business:8080/work", timeout=5) as response:
                response.read()
            count += 1
        except (HTTPError, URLError, TimeoutError, OSError):
            count += 1
            errors += 1
    status = {
        "mode": mode,
        "target_rps": rate,
        "requests": count,
        "errors": errors,
        "updated_at": time.time(),
        "calls_real_llm": False,
    }
    temp = STATE / "traffic-status.tmp"
    temp.write_text(json.dumps(status))
    temp.replace(STATE / "traffic-status.json")
    if count % 12 == 1:
        print(json.dumps(status), flush=True)
    time.sleep(max(0.01, (1 / rate if rate else 2) - (time.monotonic() - begin)))
