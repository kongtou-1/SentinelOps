"""Human-facing health is independent of legacy decision agreement and Agent errors."""

from app.schemas.inspections import (
    InspectionItemResponse,
    InspectionRunResponse,
    InspectionTraceResponse,
)


def rule_status(checks):
    states = {c.get("status") for c in checks}
    return "fail" if "fail" in states else "pass" if states == {"pass"} else "unknown"


def item_response(item):
    value = InspectionItemResponse.model_validate(item)
    if item.check_status is not None:
        value.final_status = item.check_status
        value.explanation = item.agent_result.get("summary", "检查尚未完成")
        return value
    status = item.rule_verdict or rule_status(item.checks)
    if not item.checks and item.agent_status == "completed":
        status = {"supported": "pass", "contradicted": "fail"}.get(
            item.agent_result.get("verdict"), "unknown"
        )
    value.final_status = status
    failed = [c.get("title", "检查项") for c in item.checks if c.get("status") == "fail"]
    if failed:
        value.explanation = "、".join(failed[:3]) + "未通过"
    elif item.checks:
        value.explanation = "全部规则检查通过" if status == "pass" else "检查数据不足，无法完成判断"
    else:
        value.explanation = item.agent_result.get("summary", "智能检查尚未完成")
    if item.agent_status == "failed":
        value.explanation += (
            "；检查已完成，智能分析失败" if item.checks else "；智能分析失败，未得出结论"
        )
    return value


def run_responses(db, rows):
    from app.repositories.inspections import health_counts

    counts = health_counts(db, [row.id for row in rows]) if rows else {}
    result = []
    for row in rows:
        value = InspectionRunResponse.model_validate(row)
        value.summary = {
            **row.summary,
            "health_counts": counts.get(row.id, {}),
            "objects": sum(counts.get(row.id, {}).values()),
        }
        result.append(value)
        if row.snapshot.get("checks"):
            from app.services.inspections.checks_runtime import coverage

            value.summary["coverage"] = coverage(db, row)
    return result


def trace_responses(db, rows):
    from app.repositories import inspections as repository

    checks = repository.check_result_map(db, {row.check_result_id for row in rows}) if rows else {}
    values = []
    for row in rows:
        value = InspectionTraceResponse.model_validate(row)
        check = checks.get(row.check_result_id)
        if check:
            value.object_key = check.object_key
            value.check_id = check.check_id
            value.check_name = check.result.get("name", check.check_id)
        values.append(value)
    return values
