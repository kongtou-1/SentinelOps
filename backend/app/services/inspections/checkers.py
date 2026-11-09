"""Registered, deterministic checks; no executable expressions from Skills or data."""

import json
from decimal import Decimal, InvalidOperation

from app.schemas.inspections import InspectionRule


def field(value, path):
    for part in path.split("."):
        value = value[int(part)] if isinstance(value, list) and part.isdigit() else value[part]
    return value


def paths(value, parts, prefix=(), indices=()):
    if not parts:
        return [(".".join(prefix), indices)]
    part, *rest = parts
    if part == "*":
        if not isinstance(value, list) or not value:
            raise KeyError("array_required")
        return [
            item
            for index, child in enumerate(value)
            for item in paths(child, rest, (*prefix, str(index)), (*indices, str(index)))
        ]
    try:
        child = value[int(part)] if isinstance(value, list) and part.isdigit() else value[part]
    except (KeyError, IndexError, TypeError):
        return [(".".join((*prefix, *parts)), indices)]
    return paths(child, rest, (*prefix, part), indices)


def aligned(pattern, indices):
    positions = iter(indices)
    parts = []
    for part in pattern.split("."):
        if part == "*":
            part = next(positions, None)
            if part is None:
                raise ValueError("comparison_shape_mismatch")
        parts.append(part)
    return ".".join(parts)


def check_one(record, rule, path, indices):
    result = {
        "code": rule.code,
        "title": rule.title,
        "basis": rule.basis,
        "path": path,
        "status": "unknown",
    }
    try:
        actual = field(record["facts"], path)
        result["actual"] = actual
        if rule.kind == "required":
            passed = actual is not None and actual != "" and actual != [] and actual != {}
        elif rule.kind == "unique":
            if not isinstance(actual, list):
                raise ValueError("array_required")
            values = [
                field(item, rule.compare_path) if rule.compare_path else item for item in actual
            ]
            normalized = [json.dumps(value, sort_keys=True, allow_nan=False) for value in values]
            passed = len(normalized) == len(set(normalized))
            result["duplicate_count"] = len(normalized) - len(set(normalized))
        elif rule.kind in {"contains", "starts_with", "ends_with"}:
            if not isinstance(actual, str) or not isinstance(rule.expected, str):
                raise ValueError("string_value_required")
            passed = (
                rule.expected in actual
                if rule.kind == "contains"
                else actual.startswith(rule.expected)
                if rule.kind == "starts_with"
                else actual.endswith(rule.expected)
            )
            result["expected"] = rule.expected
        elif rule.kind in {"equals", "field_equals"}:
            expected = (
                field(record["facts"], aligned(rule.compare_path, indices))
                if rule.kind == "field_equals"
                else rule.expected
            )
            if rule.kind == "field_equals" and (
                actual in (None, "", [], {}) or expected in (None, "", [], {})
            ):
                raise ValueError("comparison_value_required")
            if (
                rule.kind == "equals"
                and type(expected) in (int, float)
                and not isinstance(actual, bool)
            ):
                numeric = Decimal(str(actual))
                if not numeric.is_finite():
                    raise ValueError("numeric_value_required")
                passed = numeric == Decimal(str(expected))
            else:
                passed = type(actual) is type(expected) and actual == expected
            result["expected"] = expected
            if rule.kind == "field_equals":
                result["compare_path"] = aligned(rule.compare_path, indices)
        else:
            if rule.kind == "count_range":
                if not isinstance(actual, list):
                    raise ValueError("array_required")
                actual = len(actual)
            if actual is None or isinstance(actual, bool):
                raise ValueError("numeric_value_required")
            value = Decimal(str(actual))
            if not value.is_finite():
                raise ValueError("numeric_value_required")
            if rule.unit and field(record["facts"], aligned(rule.unit_path, indices)) != rule.unit:
                raise ValueError("unit_mismatch")
            value = value.copy_abs() if rule.absolute else value
            passed = (rule.minimum is None or value >= rule.minimum) and (
                rule.maximum is None or value <= rule.maximum
            )
            result.update(
                {
                    "value": str(value),
                    "minimum": str(rule.minimum) if rule.minimum is not None else None,
                    "maximum": str(rule.maximum) if rule.maximum is not None else None,
                    "unit": rule.unit,
                }
            )
        result["status"] = "pass" if passed else "fail"
    except (KeyError, IndexError, TypeError):
        result["reason"] = "field_missing"
    except (ValueError, InvalidOperation) as exc:
        result["reason"] = "unit_mismatch" if str(exc) == "unit_mismatch" else "invalid_value"
    return result


def check(record, rules):
    results = []
    for definition in rules:
        rule = InspectionRule.model_validate(definition)
        try:
            locations = (
                paths(record["facts"], rule.path.split("."))
                if "*" in rule.path
                else [(rule.path, ())]
            )
        except (KeyError, IndexError, TypeError):
            locations = [(rule.path, ())]
        if len(results) + len(locations) > 1000:
            results.append(
                {
                    "code": rule.code,
                    "title": rule.title,
                    "path": rule.path,
                    "basis": rule.basis,
                    "status": "unknown",
                    "reason": "check_budget_exhausted",
                }
            )
        else:
            results.extend(check_one(record, rule, path, indices) for path, indices in locations)
    issues = record["facts"].get("_inspection", {}).get("issues", [])
    results.extend(
        {
            "code": "source_" + issue["source"],
            "title": "关联资料完整性",
            "path": issue["source"],
            "basis": "巡检计划的数据关联配置",
            "status": "unknown",
            "reason": issue["reason"],
        }
        for issue in issues
    )
    original = record.get("original_verdict", "unknown")
    statuses = {r["status"] for r in results}
    expected = "fail" if "fail" in statuses else ("pass" if statuses == {"pass"} else "unknown")
    if issues or expected == "unknown":
        verdict = "unassessable"
    elif original == "unknown":
        verdict = "needs_review"
    else:
        verdict = "supported" if expected == original else "contradicted"
    return results, verdict
