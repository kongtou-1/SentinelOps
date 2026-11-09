import type { Models } from "@/api/client";

type Item = Models["InspectionItemResponse"];

export function itemTitle(item: Item, ordinal: number) {
  const facts = item.evidence.facts as Record<string, unknown> | undefined;
  const main = (facts?.main || facts) as Record<string, unknown> | undefined;
  for (const key of ["title", "name", "certificate_name", "service_name"]) {
    const value = main?.[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  const certificate = main?.certificate_no || main?.certificate_number;
  if (typeof certificate === "string" && certificate.trim())
    return [main?.instrument || main?.instrument_name, `证书 ${certificate}`]
      .filter(Boolean)
      .join(" · ");
  const instrument = main?.instrument_name || main?.instrument;
  if (typeof instrument === "string" && instrument.trim()) {
    const businessId = item.record_id;
    const readable =
      businessId &&
      businessId.length <= 80 &&
      !/^[a-f\d-]{32,}$/i.test(businessId);
    return [
      instrument,
      readable
        ? /CERT/i.test(businessId)
          ? `证书 ${businessId}`
          : businessId
        : "",
    ]
      .filter(Boolean)
      .join(" · ");
  }
  if (main?.work_http_status != null && main?.work) return "业务请求响应";
  if (
    item.record_id &&
    item.record_id.length <= 80 &&
    !/^[a-f\d-]{32,}$/i.test(item.record_id)
  )
    return item.record_id;
  const failed = item.checks.find((check) => check.status === "fail");
  if (failed?.title) return failed.title;
  if (
    item.verdict === "contradicted" &&
    item.evidence.original_verdict === "fail" &&
    item.checks.length
  )
    return "原判定与检查依据不符";
  if (item.checks.length === 1 && item.checks[0]?.title)
    return item.checks[0].title;
  if (item.verdict === "supported" && item.checks.length)
    return "检查依据支持原判定";
  return `检查结果 ${ordinal}`;
}

export function itemSourceLabel(item: Item) {
  const facts = item.evidence.facts as Record<string, unknown> | undefined;
  const main = (facts?.main || facts) as Record<string, unknown> | undefined;
  const labels: Record<string, string> = {
    synthetic_acceptance_replay: "验收回放",
    constructed_acceptance: "构造样例",
    real_response_snapshot: "响应快照",
  };
  return labels[String(main?.sample_kind)] || "";
}

export function displayValue(value: unknown): string {
  if (value === true) return "是";
  if (value === false) return "否";
  if (value == null) return "未提供";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

export function itemConclusion(item: Item) {
  if (Array.isArray(item.evidence.tool_calls))
    return String(
      item.agent_result.summary || item.explanation || "自主巡检尚未完成",
    );
  const failed = item.checks.filter((check) => check.status === "fail");
  const incomplete = item.checks.filter(
    (check) => check.status !== "pass" && check.status !== "fail",
  );
  if (
    failed.length &&
    item.verdict === "supported" &&
    item.evidence.original_verdict === "fail"
  )
    return `有 ${failed.length} 项检查未通过：${failed.map((check) => check.title || "检查项").join("、")}，与原判定一致。`;
  if (failed.length)
    return `有 ${failed.length} 项检查未通过：${failed.map((check) => check.title || "检查项").join("、")}。`;
  if (incomplete.length)
    return `有 ${incomplete.length} 项检查缺少可评估的资料。`;
  if (item.agent_status === "failed")
    return "规则检查已完成，Agent 评审失败，需要人工复核。";
  if (
    item.verdict === "contradicted" &&
    item.evidence.original_verdict === "fail" &&
    item.checks.length
  )
    return `原判定为未通过，但 ${item.checks.length} 项规则检查均通过，现有依据与原判定矛盾。`;
  if (item.verdict === "supported") return "现有检查依据支持原判定。";
  if (item.verdict === "needs_review")
    return "现有检查尚不能确认最终结论，需要人工复核。";
  if (item.verdict === "unassessable") return "资料不足，无法完成评估。";
  return "现有检查依据与原判定存在矛盾。";
}

export function checkTitle(check: Record<string, unknown>) {
  const index = String(check.path || "").match(/(?:^|\.)(\d+)(?:\.|$)/);
  return `${index ? `第 ${Number(index[1]) + 1} 项 · ` : ""}${check.title || "检查项"}`;
}

export function expectation(check: Record<string, unknown>) {
  if ("expected" in check && check.expected != null)
    return displayValue(check.expected);
  const unit = check.unit ? ` ${check.unit}` : "";
  const lower = check.minimum,
    upper = check.maximum;
  if (lower != null && upper != null) return `${lower} ～ ${upper}${unit}`;
  if (upper != null) return `≤ ${upper}${unit}`;
  if (lower != null) return `≥ ${lower}${unit}`;
  return "已提供有效资料";
}

export function actualValue(check: Record<string, unknown>) {
  return `${displayValue(check.actual)}${check.actual != null && check.unit ? ` ${check.unit}` : ""}`;
}

export const checkReasons: Record<string, string> = {
  field_missing: "缺少检查所需资料",
  invalid_value: "数据无效，无法比较",
  unit_mismatch: "实际单位与要求不一致",
  join_field_missing: "资料缺少关联信息",
  invalid_join_key: "资料关联信息无效",
  join_key_mismatch: "关联资料不属于同一业务对象",
  duplicate_matches: "应唯一的关联资料存在重复记录",
  related_record_limit: "关联明细超过本轮检查上限",
  related_record_missing: "缺少必需的关联资料",
  related_source_unavailable: "关联资料读取失败",
  assembled_record_too_large: "关联资料超过本轮读取上限",
  check_budget_exhausted: "检查项目超过本轮上限",
};
