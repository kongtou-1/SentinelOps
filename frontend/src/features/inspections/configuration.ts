import type { Plan } from "./ui";
import type { CheckInput, Condition } from "./task";

export function configuredChecks(plan: Plan): CheckInput[] {
  if (plan.execution_mode === "checks") return plan.check_items || [];
  const checks: CheckInput[] = [];
  if (plan.rules.length)
    checks.push({
      check_id: "rules",
      name: "规则检查",
      kind: "rule",
      enabled: true,
      scope: "object",
      severity: "warning",
      requirements: "",
      conditions: plan.rules,
      operator: "and",
    });
  if (plan.agent_enabled || plan.execution_mode === "agent")
    checks.push({
      check_id: "agent",
      name: "智能检查",
      kind: "agent",
      enabled: true,
      scope: "object",
      severity: "warning",
      operator: "and",
      requirements: "",
      skill_id: plan.skill_id,
      source_ids: plan.agent_source_ids,
      parameters: plan.agent_parameters,
    });
  return checks;
}

export function inputRows(
  value: unknown,
  prefix = "",
): { name: string; value: string }[] {
  if (value !== null && typeof value === "object") {
    const entries = Object.entries(value);
    if (!entries.length)
      return prefix ? [{ name: prefix, value: "无内容" }] : [];
    return entries.flatMap(([key, item]) =>
      inputRows(
        item,
        Array.isArray(value)
          ? `${prefix} · 第 ${Number(key) + 1} 项`
          : [prefix, key].filter(Boolean).join(" / "),
      ),
    );
  }
  return [
    {
      name: prefix,
      value:
        value === null
          ? "未设置"
          : value === true
            ? "是"
            : value === false
              ? "否"
              : value === ""
                ? "空文本"
                : String(value),
    },
  ];
}

export function conditionRequirement(condition: Condition) {
  const value = condition.expected;
  const text =
    value === true
      ? "是"
      : value === false
        ? "否"
        : value === null || value === undefined
          ? "空值"
          : typeof value === "string"
            ? `“${value}”`
            : String(value);
  switch (condition.kind) {
    case "required":
      return "必须填写有效内容";
    case "equals":
      return `等于 ${text}`;
    case "field_equals":
      return `与字段“${condition.compare_path}”的值相同`;
    case "contains":
      return `包含 ${text}`;
    case "starts_with":
      return `以 ${text} 开头`;
    case "ends_with":
      return `以 ${text} 结尾`;
    case "unique":
      return "各项的值不能重复";
    default: {
      const bounds = [
        condition.minimum != null ? `不小于 ${condition.minimum}` : "",
        condition.maximum != null ? `不大于 ${condition.maximum}` : "",
      ]
        .filter(Boolean)
        .join("，");
      const unit = condition.unit
        ? `；单位字段“${condition.unit_path}”必须为“${condition.unit}”`
        : "";
      return `${condition.kind === "count_range" ? "项目数量" : "数值"}${condition.absolute ? "取绝对值后" : ""}${bounds}${unit}`;
    }
  }
}
