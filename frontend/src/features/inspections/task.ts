import type { Models } from "@/api/client";
import { emptySource, type Plan } from "./ui";

export type TaskInput = Models["InspectionPlanInput"];
export type CheckInput = Models["InspectionCheck-Input"];
export type Condition = Models["InspectionRule-Input"];

export function taskDefaults(): TaskInput {
  return {
    name: "",
    application_id: null,
    environment: "test",
    enabled: false,
    source_kind: "api",
    source_config: emptySource(),
    data_source_id: null,
    execution_mode: "checks",
    object_mode: "dynamic",
    fixed_objects: [],
    check_items: [],
    rules: [],
    related_sources: [],
    agent_enabled: false,
    agent_source_ids: [],
    agent_parameters: {},
    skill_id: null,
    schedule_kind: "manual",
    interval_seconds: 3600,
    schedule_timezone: "Asia/Shanghai",
    cron_expression: "0 2 * * *",
    scheduled_at: null,
    timeout_seconds: 300,
    max_retries: 0,
    lookback_seconds: 86400,
    max_records: 100,
    max_agent_items: 10,
  };
}

export function taskFromPlan(plan: Plan): TaskInput {
  const values = Object.fromEntries(
    Object.keys(taskDefaults()).map((key) => [key, plan[key as keyof Plan]]),
  );
  return JSON.parse(
    JSON.stringify({
      ...taskDefaults(),
      ...values,
      expected_version: plan.version,
    }),
  ) as TaskInput;
}

export function newCondition(): Condition {
  return {
    code: `field_${crypto.randomUUID().slice(0, 8)}`,
    title: "",
    kind: "required",
    path: "",
    basis: "",
    expected: "",
    minimum: null,
    maximum: null,
    compare_path: "",
    absolute: false,
    unit: "",
    unit_path: "",
  };
}

export function newCheck(kind: "rule" | "agent"): CheckInput {
  return {
    check_id: `check_${crypto.randomUUID().slice(0, 8)}`,
    name: "",
    kind,
    enabled: true,
    scope: "object",
    severity: "warning",
    operator: "and",
    conditions: kind === "rule" ? [newCondition()] : [],
    requirements: "",
    skill_id: null,
    auxiliary_skill_ids: [],
    source_ids: [],
    parameters: {},
  };
}

export const ruleKinds: Record<Condition["kind"], string> = {
  required: "必须有值",
  equals: "等于指定值",
  field_equals: "等于另一字段",
  numeric_range: "数值范围",
  contains: "包含文本",
  starts_with: "文本前缀",
  ends_with: "文本后缀",
  count_range: "集合数量范围",
  unique: "集合字段唯一",
};

export function checkCounts(plan: Plan) {
  if (plan.execution_mode === "checks") {
    const enabled = (plan.check_items || []).filter((check) => check.enabled);
    return `规则 ${enabled.filter((check) => check.kind === "rule").length} · Agent ${enabled.filter((check) => check.kind === "agent").length}`;
  }
  return `旧版 · 规则 ${plan.rules.length} · Agent ${plan.agent_enabled ? 1 : 0}`;
}
