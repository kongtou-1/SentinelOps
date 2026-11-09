"""Deterministic model projections; the complete frozen snapshot stays untouched."""

import math
from copy import deepcopy

from app.utils.canonical_json import digest, encoded

AUTOMATIC_EVENTS = {"alert_created", "alert_linked", "alert_resolved", "alert_recovery_checked"}


def metric_samples(values):
    """Keep exact extrema, endpoints and time windows, including invalid samples."""
    valid, invalid = [], 0
    for point in values:
        try:
            number = float(point[1])
            if not math.isfinite(number):
                raise ValueError("Non-finite sample")
            valid.append((point, number))
        except (ValueError, TypeError, IndexError):
            invalid += 1

    def extrema(points):
        if not points:
            return {}
        return {
            "first": points[0][0],
            "last": points[-1][0],
            "min": min(points, key=lambda x: x[1])[0],
            "max": max(points, key=lambda x: x[1])[0],
        }

    windows = []
    for start in range(3):
        points = valid[len(valid) * start // 3 : len(valid) * (start + 1) // 3]
        if points:
            windows.append(
                {
                    "count": len(points),
                    "start_at": points[0][0][0],
                    "end_at": points[-1][0][0],
                    "min": min(points, key=lambda x: x[1])[0],
                    "max": max(points, key=lambda x: x[1])[0],
                }
            )
    return {
        "sample_count": len(values),
        "invalid_sample_count": invalid,
        **extrema(valid),
        "windows": windows,
    }


def compact_payload(value):
    if isinstance(value, list):
        return [compact_payload(item) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if (
                key == "result"
                and isinstance(item, list)
                and item
                and all(
                    isinstance(series, dict)
                    and isinstance(series.get("metric"), dict)
                    and ("values" in series or "value" in series)
                    for series in item
                )
            ):
                groups = {}
                for series in item:
                    samples = {k: v for k, v in series.items() if k != "metric"}
                    identity = encoded(samples)
                    if identity not in groups:
                        groups[identity] = {"metric_labels": [], **compact_payload(samples)}
                    groups[identity]["metric_labels"].append(deepcopy(series["metric"]))
                result[key] = list(groups.values())
            elif (
                key == "values"
                and isinstance(item, list)
                and len(item) > 8
                and all(
                    isinstance(point, (list, tuple))
                    and len(point) == 2
                    and isinstance(point[0], (int, float))
                    for point in item
                )
            ):
                summary = metric_samples(item)
                if len(encoded(summary)) < len(encoded(item)):
                    result["values_summary"] = summary
                else:
                    result[key] = deepcopy(item)
            else:
                result[key] = compact_payload(item)
        return result
    if isinstance(value, str) and len(value) > 4000:
        # Label omitted text explicitly; never change the stored source.
        return {
            "first_excerpt": value[:2000],
            "last_excerpt": value[-2000:],
            "original_chars": len(value),
            "note": "中间文本未送入模型；完整原文保留在冻结证据中，不得推断省略内容",
        }
    return value


def model_context(snapshot):
    context = {
        k: deepcopy(v) for k, v in snapshot.items() if k not in ("records", "context_summary")
    }
    records, groups = [], {}
    for source in snapshot.get("records", []):
        row = deepcopy(source)
        data = row["data"]
        if row["kind"] == "timeline" and data.get("event_type") in AUTOMATIC_EVENTS:
            # Group only machine events with identical facts; human operations stay individual.
            facts = {k: v for k, v in data.get("content", {}).items() if k != "alert_event_id"}
            key = encoded(
                {
                    "incident_id": data.get("incident_id"),
                    "event_type": data["event_type"],
                    "actor_type": data.get("actor_type"),
                    "actor_id": data.get("actor_id"),
                    "facts": facts,
                }
            )
            if key not in groups:
                groups[key] = []
            groups[key].append(row)
            continue
        if row["kind"] == "analysis":
            for field in ("evidence", "knowledge", "source_context", "database_context"):
                if field in data:
                    data[field] = compact_payload(data[field])
        elif row["kind"] == "repair_handoff" and "packet" in data:
            data["packet"] = compact_payload(data["packet"])
        elif row["kind"] == "execution":
            for field in ("precheck", "output"):
                if field in data:
                    data[field] = compact_payload(data[field])
        if data != source["data"]:
            row["projection"] = "原始证据的确定性摘要；完整原文保留在冻结快照"
            row["source_content_hash"] = row.pop("content_hash", None)
        records.append(row)

    for group in groups.values():
        group.sort(key=lambda row: (row["occurred_at"], row["id"]))
        row = group[0]
        if len(group) > 1:
            row["data"]["content"] = {
                k: v for k, v in row["data"]["content"].items() if k != "alert_event_id"
            }
            row["aggregation"] = {
                "event_count": len(group),
                "first_at": group[0]["occurred_at"],
                "last_at": group[-1]["occurred_at"],
                "first_source_id": group[0]["id"],
                "last_source_id": group[-1]["id"],
                "sources_hash": digest([item["id"] for item in group]),
                "note": "同类自动事件合并；次数不是当前有效告警数，不代表业务停机次数",
            }
            row["source_content_hash"] = row.pop("content_hash", None)
        records.append(row)
    context["records"] = sorted(records, key=lambda row: (row["occurred_at"], row["id"]))
    context["projection_policy"] = {
        "version": 1,
        "original_records": len(snapshot.get("records", [])),
        "input_records": len(records),
        "note": "仅使用摘要中实际可见事实；引用记录原始 id；不得补写省略内容或恢复结论",
    }
    return context
