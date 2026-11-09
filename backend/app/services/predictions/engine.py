"""CPU-only forecast calculations. Imported by worker children, never by the API."""

from datetime import UTC, datetime

import numpy as np
from statsforecast.models import MSTL, AutoETS, Naive, SeasonalNaive

STEP = 300
MIN_HISTORY = {1: 72, 6: 288, 24: 864}


def prepare_series(points, end):
    """Regularize closed bins; missing observations never become healthy zeros."""
    samples = {}
    for timestamp, value in points:
        timestamp = int(float(timestamp))
        if timestamp > end or timestamp % STEP:
            continue
        try:
            number = float(value)
        except (ValueError, TypeError):
            number = np.nan
        samples[timestamp] = number if np.isfinite(number) else np.nan
    valid = [t for t, value in samples.items() if np.isfinite(value)]
    if not valid:
        return None, {"status": "insufficient_data", "reason": "no_samples"}
    first = max(min(valid), end - 28 * 86400 + STEP)
    times = np.arange(first, end + STEP, STEP, dtype=np.int64)
    values = np.array([samples.get(int(t), np.nan) for t in times])
    observed = np.isfinite(values)
    # Keep only the contiguous recent segment. Never interpolate across a long gap.
    run, boundary = 0, 0
    gaps = []
    for index, available in enumerate(observed):
        if not available:
            run += 1
        else:
            if run > 2:
                gaps.append(
                    {"start": int(times[index - run]), "end": int(times[index - 1]), "bins": run}
                )
                boundary = index
            run = 0
    if boundary:
        times, values, observed = times[boundary:], values[boundary:], observed[boundary:]
    quality = {
        "status": "usable",
        "samples": len(values),
        "observed_samples": int(observed.sum()),
        "missing_fraction": float(1 - observed.mean()),
        "history_hours": len(values) * STEP / 3600,
        "history_start": int(times[0]),
        "excluded_gaps": gaps,
    }
    reason = None
    if not observed[-1]:
        reason = "latest_bin_missing"
    elif quality["missing_fraction"] > 0.1:
        reason = "too_many_missing_samples"
    if reason:
        return None, {**quality, "status": "insufficient_data", "reason": reason}
    values = np.interp(times, times[observed], values[observed])
    return (times, values, observed), quality


def eligible_models(n):
    names = ["naive", "auto_ets"]
    if n >= 864:
        names += ["seasonal_naive_daily", "mstl_daily"]
    if n >= 6048:
        names += ["seasonal_naive_weekly", "mstl_daily_weekly"]
    return names


def model(name):
    if name == "naive":
        return Naive()
    if name.startswith("seasonal_naive"):
        return SeasonalNaive(season_length=2016 if name.endswith("weekly") else 288)
    if name.startswith("mstl"):
        periods = [288, 2016] if name.endswith("weekly") else [288]
        return MSTL(
            season_length=periods,
            trend_forecaster=AutoETS(model="ZZN"),
            # Interpolate the long LOESS smoothers; retain exact seasonal smoothing.
            # A jump of 10% of the shortest period bounds CPU cost for weekly series.
            stl_kwargs={
                "robust": True,
                "trend_jump": min(periods) // 10,
                "low_pass_jump": min(periods) // 10,
            },
        )
    return AutoETS(model="ZZN")


def bounds(metric):
    if metric in ("host_cpu_percent", "host_memory_percent", "host_disk_percent"):
        return 0, 100
    if metric == "llm_error_rate":
        return 0, 1
    return 0, np.inf


def forecast(values, name, steps, metric):
    fitted = model(name).fit(y=values)
    with np.errstate(invalid="raise", over="raise"):
        prediction = fitted.predict(h=steps, level=[95])
    lower, upper = bounds(metric)
    arrays = [
        np.clip(np.asarray(prediction[key]), lower, upper) for key in ("mean", "lo-95", "hi-95")
    ]
    if not all(np.isfinite(part).all() for part in arrays):
        raise ValueError("Non-finite forecast")
    return arrays, fitted


def backtest(values, observed, hours, metric):
    steps = hours * 12
    minimum = MIN_HISTORY[hours]
    shortest = len(values) - 3 * steps
    if shortest < minimum:
        return {"status": "pending", "reason": "not_enough_validation_history"}
    scores = {}
    for name in eligible_models(shortest):
        errors, covered = [], []
        try:
            for window in range(3, 0, -1):
                cutoff = len(values) - window * steps
                if not observed[cutoff - 1]:
                    raise ValueError("Validation boundary is missing")
                # Reconstruct gaps using training observations only, never future holdout values.
                training_mask = observed[:cutoff]
                training = np.interp(
                    np.arange(cutoff), np.flatnonzero(training_mask), values[:cutoff][training_mask]
                )
                (mean, lo, hi), _ = forecast(training, name, steps, metric)
                actual = values[cutoff : cutoff + steps]
                mask = observed[cutoff : cutoff + steps]
                if mask.sum() < 0.9 * steps:
                    raise ValueError("Insufficient validation observations")
                errors.extend(np.abs(actual[mask] - mean[mask]).tolist())
                covered.extend(((actual[mask] >= lo[mask]) & (actual[mask] <= hi[mask])).tolist())
            scores[name] = {
                "mae": float(np.mean(errors)),
                "coverage": float(np.mean(covered)),
                "validation_points": len(errors),
            }
        except (ValueError, FloatingPointError, RuntimeError):
            continue
    if not scores:
        return {"status": "pending", "reason": "validation_failed"}
    winner = min(scores, key=lambda key: scores[key]["mae"])
    baseline = min(
        (
            v["mae"]
            for key, v in scores.items()
            if key == "naive" or key.startswith("seasonal_naive")
        ),
        default=None,
    )
    return {
        "status": "validated",
        "eligible_models": eligible_models(shortest),
        "selected_model": winner,
        "scores": scores,
        "coverage": scores[winner]["coverage"],
        "improvement_over_baseline": (
            (baseline - scores[winner]["mae"]) / baseline if baseline and baseline > 0 else None
        ),
        "windows": 3,
    }


def residual_baseline(fitted, values):
    expected = np.asarray(fitted.predict_in_sample()["fitted"])
    errors = values[-2016:] - expected[-2016:]
    errors = errors[np.isfinite(errors)]
    if not len(errors):
        errors = np.array([0.0])
    center = float(np.median(errors))
    scale = max(
        float(1.4826 * np.median(np.abs(errors - center))),
        1e-6 * max(float(np.median(np.abs(values))), 1),
    )
    return center, scale


def detect_anomaly(timestamp, actual, previous, observed=True):
    """Compare with an earlier forecast, never a fit including this observation."""
    old = previous.get("detector_state", {})
    state = dict(old)
    state["baselines"] = dict(old.get("baselines", {}))
    if not observed or timestamp <= old.get("last_timestamp", 0):
        if observed and timestamp == old.get("last_timestamp") and actual == old.get("last_actual"):
            return [a for a in previous.get("anomalies", []) if a["timestamp"] == timestamp], state
        return [], state
    expected, baseline = None, old
    for hours in ("1", "6", "24"):
        horizon = previous.get("horizons", {}).get(hours, {})
        expected = next(
            (p["value"] for p in horizon.get("points", []) if p["timestamp"] == timestamp), None
        )
        if expected is not None:
            baseline = old.get("baselines", {}).get(hours, old)
            break
    if expected is None or "scale" not in baseline:
        return [], state
    residual = actual - expected
    z = (residual - baseline.get("center", 0)) / baseline["scale"]
    # A gap ends the consecutive drift sequence and starts a new EWMA segment.
    consecutive = timestamp - old.get("last_timestamp", timestamp - STEP) == STEP
    count = old.get("count", 0) + 1 if consecutive else 1
    ewma = 0.2 * z + 0.8 * (old.get("ewma", 0) if consecutive else 0)
    limit = 3 * np.sqrt(0.2 / 1.8 * (1 - 0.8 ** (2 * count)))
    streak = (old.get("streak", 0) if consecutive else 0) + 1 if abs(ewma) > limit else 0
    state.update(
        ewma=float(ewma), count=count, streak=streak, last_timestamp=timestamp, last_actual=actual
    )
    anomaly = []
    for kind, detected in (("spike", abs(z) > 3.5), ("drift", streak >= 3)):
        if detected:
            anomaly.append(
                {
                    "timestamp": timestamp,
                    "kind": kind,
                    "actual": actual,
                    "expected": expected,
                    "residual": residual,
                    "robust_z": float(z),
                    "ewma": float(ewma),
                }
            )
    return anomaly, state


def risk(points, threshold, quality):
    if quality not in ("validated", "interval_low_confidence"):
        return {"level": "unknown", "first_breach_at": None}
    breach = next((p for p in points if p["value"] > threshold), None)
    possible = next((p for p in points if p["upper"] > threshold), None)
    point = breach or (possible if quality == "validated" else None)
    return {
        "level": "expected" if breach else "possible" if point else "normal",
        "first_breach_at": point["timestamp"] if point else None,
    }


def compute(points, end, config, previous=None):
    previous = previous or {}
    series, quality = prepare_series(points, end)
    # Each completed observation consumes the prior forecast, before this run is fitted.
    state = previous.get("detector_state", {})
    anomalies = []
    samples = {}
    for timestamp, value in points:
        timestamp = int(float(timestamp))
        try:
            value = float(value)
        except (ValueError, TypeError):
            continue
        if timestamp <= end and timestamp % STEP == 0 and np.isfinite(value):
            samples[timestamp] = value
    for timestamp in sorted(samples):
        last_timestamp = previous.get("detector_state", {}).get("last_timestamp", 0)
        if timestamp <= last_timestamp and timestamp != end:
            continue
        found, state = detect_anomaly(
            timestamp, samples[timestamp], {**previous, "detector_state": state}
        )
        anomalies.extend(found)
    answer = {
        "quality": quality,
        "horizons": {},
        "anomalies": anomalies,
        "detector_state": state,
        "backtest": {},
        "data_end_at": end,
    }
    if series is None:
        if config.get("probe_interval_seconds", 300) > 300:
            answer["quality"]["reason"] = "probe_interval_too_large"
        answer["horizons"] = {
            str(h): {
                "quality": "insufficient_data",
                "points": [],
                "risk": {"level": "unknown", "first_breach_at": None},
            }
            for h in MIN_HISTORY
        }
        return answer
    times, values, observed = series
    anomaly = [a for a in anomalies if a["timestamp"] == end]
    cached = previous.get("backtest", {})
    fits = {}
    for hours, minimum in MIN_HISTORY.items():
        key = str(hours)
        if len(values) < minimum:
            answer["horizons"][key] = {
                "quality": "insufficient_data",
                "points": [],
                "risk": {"level": "unknown", "first_breach_at": None},
            }
            continue
        validation = cached.get(key, {})
        cache_time = validation.get("checked_at", 0)
        # Retry pending validation as soon as sufficient history is accumulated.
        candidates = eligible_models(len(values) - 3 * hours * 12)
        if (
            end - cache_time >= 86400
            or validation.get("status") != "validated"
            or validation.get("eligible_models") != candidates
            or any(gap["end"] > cache_time for gap in quality["excluded_gaps"])
        ):
            validation = backtest(values, observed, hours, config["metric"])
            validation["checked_at"] = end
            validation["history_start"] = quality["history_start"]
        answer["backtest"][key] = validation
        preferred = eligible_models(len(values))[-1]
        chosen = validation.get("selected_model", preferred)
        if chosen not in fits:
            fits[chosen] = forecast(values, chosen, 288, config["metric"])
        (mean, lower, upper), fitted = fits[chosen]
        q = (
            "provisional"
            if validation.get("status") != "validated"
            else ("validated" if validation["coverage"] >= 0.8 else "interval_low_confidence")
        )
        curve = [
            {
                "timestamp": int(times[-1] + (i + 1) * STEP),
                "value": float(mean[i]),
                "lower": float(lower[i]),
                "upper": float(upper[i]),
            }
            for i in range(hours * 12)
        ]
        answer["horizons"][key] = {
            "model": chosen,
            "quality": q,
            "interval_level": 0.95,
            "points": curve,
            "risk": risk(curve, config["threshold"], q),
        }
        center, scale = residual_baseline(fitted, values)
        old_baseline = previous.get("detector_state", {}).get("baselines", {}).get(key, {})
        # Freeze the reference during anomalies so a persistent fault is not learned away.
        if anomaly and old_baseline.get("model") == chosen:
            center, scale = old_baseline["center"], old_baseline["scale"]
        state.setdefault("baselines", {})[key] = {"center": center, "scale": scale, "model": chosen}
        if hours == 1:
            state.update(center=center, scale=scale)
    answer["detector_state"] = state
    answer["quality"]["daily_eligible"] = len(values) >= 864
    answer["quality"]["weekly_eligible"] = len(values) >= 6048
    answer["quality"]["data_end"] = datetime.fromtimestamp(end, UTC).isoformat()
    return answer
