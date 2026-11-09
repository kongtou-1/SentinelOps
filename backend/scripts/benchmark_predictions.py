"""Reproducible synthetic accuracy and full worker-runtime acceptance."""

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    from app.services.predictions.engine import forecast
    from app.workers.predictions import calculate

    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "data_source": "fixed-seed synthetic",
        "seed": 20261002,
        "cases": [],
    }
    for weekly in (False, True):
        rng = np.random.default_rng(20261002)
        count = 8064 if weekly else 2016
        t = np.arange(count + 288)
        values = 40 + 15 * np.sin(2 * np.pi * t / 288) + 0.001 * t
        if weekly:
            values += 10 * np.cos(2 * np.pi * t / 2016)
        values += rng.normal(0, 0.25, len(t))
        name = "mstl_daily_weekly" if weekly else "mstl_daily"
        started = time.monotonic()
        (prediction, _, _), _ = forecast(values[:count], name, 288, "host_cpu_percent")
        elapsed = time.monotonic() - started
        linear = np.polyval(
            np.polyfit(t[count - 288 : count], values[count - 288 : count], 1), t[count:]
        )
        mae = float(np.mean(np.abs(prediction - values[count:])))
        linear_mae = float(np.mean(np.abs(linear - values[count:])))
        report["cases"].append(
            {
                "model": name,
                "mae": mae,
                "linear_mae": linear_mae,
                "improvement": 1 - mae / linear_mae,
                "seconds": elapsed,
            }
        )
        assert mae < linear_mae * 0.5
        if weekly:
            end = int(datetime.now(UTC).timestamp()) // 300 * 300
            points = [
                [end - (count - 1 - i) * 300, float(value)]
                for i, value in enumerate(values[:count])
            ]
            started = time.monotonic()
            answer, error = calculate(
                {
                    "points": points,
                    "end": end,
                    "config": {"metric": "host_cpu_percent", "threshold": 80},
                }
            )
            report["full_28d_worker"] = {"seconds": time.monotonic() - started, "error": error}
            assert error is None, error
            report["full_28d_worker"]["horizons"] = {
                h: {"model": v["model"], "quality": v["quality"]}
                for h, v in answer["horizons"].items()
            }
    (ROOT / ".local/batch6-algorithm-report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
