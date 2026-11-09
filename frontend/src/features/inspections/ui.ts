import type { Models } from "@/api/client";
import { date } from "@/utils/display";
export type Plan = Models["InspectionPlanResponse"];
export type Run = Models["InspectionRunResponse"];
export type Item = Models["InspectionItemResponse"];
export type DataSource = Models["InspectionDataSourceResponse"];
export function nextExecution(plan: Plan) {
  if (!plan.enabled) return "任务已暂停";
  if (plan.schedule_kind === "manual") return "仅手动执行";
  if (plan.schedule_kind === "event") return "等待事件触发";
  if (plan.next_run_at) return date(plan.next_run_at);
  if (plan.schedule_kind === "once") return "单次计划无后续执行";
  return "暂无执行安排";
}
export const emptySource = (): Models["InspectionSource"] => ({
  url: "",
  agent_api_enabled: false,
  request_method: "GET",
  path_template: "",
  allowed_query_params: [],
  allowed_body_fields: [],
  response_format: "records",
  items_path: "",
  object_id_path: "id",
  query_params: [],
  connection_id: null,
  table: "",
  id_column: "",
  version_column: "",
  time_column: "",
  decision_column: "",
  decision_pass_value: "pass",
  decision_fail_value: "fail",
  naive_time_is_utc: false,
  fact_columns: [],
  pagination: "none",
  page_parameter: "page",
  size_parameter: "page_size",
  next_cursor_path: "next_cursor",
  has_more_path: "",
  query_sql: "",
  query_parameters: {},
});
export const recoveryErrors: Record<string, string> = {
  inspection_worker_interrupted: "巡检进程中断，未完成项可补检",
  inspection_resume_discovery_incomplete: "对象发现未完成，请重新执行任务",
  inspection_resume_unavailable: "当前执行不支持补检",
  inspection_resume_nothing_pending: "没有未完成的检查项",
  inspection_resume_evidence_missing: "原始证据已删除，请重新执行任务",
  inspection_run_active: "该任务已有正在执行的巡检",
};
export const stateNames: Record<string, string> = {
  pass: "正常",
  fail: "异常",
  unknown: "待确认",
  queued: "排队中",
  collecting: "获取数据中",
  evaluating: "检查中",
  completed: "已完成",
  partial: "部分完成",
  failed: "执行失败",
  cancelled: "已停止",
  pending: "待分析",
  running: "分析中",
  disabled: "未启用",
  skipped_budget: "未分析（达到上限）",
};
export function tone(
  state?: string | null,
): "success" | "danger" | "warning" | "info" | "primary" {
  if (state === "pass" || state === "completed") return "success";
  if (state === "fail" || state === "failed") return "danger";
  if (state === "unknown" || state === "partial" || state === "skipped_budget")
    return "warning";
  return "info";
}
export function schedule(plan: Plan) {
  if (plan.schedule_kind === "manual") return "手动执行";
  if (plan.schedule_kind === "event") return "事件触发";
  if (plan.schedule_kind === "once") return "固定时间";
  if (plan.schedule_kind === "cron") return `Cron · ${plan.cron_expression}`;
  const seconds = plan.interval_seconds;
  return seconds % 86400 === 0
    ? `每 ${seconds / 86400} 天`
    : seconds % 3600 === 0
      ? `每 ${seconds / 3600} 小时`
      : `每 ${seconds / 60} 分钟`;
}
export function runName(run: Run) {
  return String(
    (run.snapshot.plan as { name?: string } | undefined)?.name || "巡检计划",
  );
}
export function health(run: Run): Record<string, number> {
  return (run.summary.health_counts || {}) as Record<string, number>;
}
export type Coverage = {
  discovery_complete: boolean;
  objects_discovered: number;
  objects_expected: number | null;
  checks_expected: number;
  checks_completed: number;
  checks_errors?: number;
  checks_unfinished: number;
  expected_is_final: boolean;
  conclusion: "pass" | "fail" | "unknown";
  no_objects: boolean;
  collection_checks: number;
};
export function coverage(run: Run): Coverage | undefined {
  return run.summary.coverage as Coverage | undefined;
}
export function runConclusion(run: Run) {
  const value = coverage(run);
  if (value) return value.conclusion;
  const counts = health(run);
  return counts.fail
    ? "fail"
    : counts.unknown || !counts.pass
      ? "unknown"
      : "pass";
}
export function trialLimit(run: Run) {
  return Number(
    (run.snapshot.trial as { sample_limit?: number } | undefined)
      ?.sample_limit || 3,
  );
}
export function runResult(run: Run) {
  const value = coverage(run);
  if (value) {
    if (["queued", "collecting", "evaluating"].includes(run.status))
      return stateNames[run.status]!;
    if (run.status === "cancelled") return "已取消，保留已完成结果";
    if (run.status === "failed") return "执行失败，检查未完成";
    if (value.checks_errors) {
      if (!value.checks_completed)
        return `检查执行失败，${value.checks_errors} 项报错，未得出检查结论`;
      return `已完成 ${value.checks_completed}/${value.checks_expected} 项检查，${value.checks_errors} 项执行出错`;
    }
    if (run.run_kind === "trial")
      return (
        "样本检查" +
        (run.status === "completed" ? "完成" : "部分完成") +
        "，未覆盖全部对象"
      );
    if (value.no_objects) return "无检查对象";
    const result =
      value.conclusion === "fail"
        ? "发现异常"
        : value.conclusion === "pass"
          ? "本轮全部通过"
          : "存在未判定检查";
    if (!value.discovery_complete)
      return `${result} · 已发现 ${value.objects_discovered} 个对象，发现未完成`;
    if (value.checks_unfinished)
      return `${result} · ${value.checks_unfinished} 项检查未完成`;
    return result;
  }
  if (run.error_code === "inspection_workflow_incomplete")
    return "Agent 巡检未全部完成";
  if (run.summary.no_matches) return "未找到符合条件的项目";
  if (run.status === "failed")
    return run.error_stage === "collection"
      ? "数据获取失败"
      : run.error_stage === "timeout"
        ? "执行超时"
        : "执行失败";
  if (run.status === "partial")
    return "达到本批对象上限，剩余对象将在下一批检查";
  if (run.error_stage === "agent" || run.error_code === "agent_incomplete") {
    const configuration = run.snapshot.plan as
      | { rules?: unknown[] }
      | undefined;
    if (!configuration?.rules?.length) return "智能检查未完成，尚未得出结论";
    if (run.summary.agent_failed === 0 && Number(run.summary.agent_skipped) > 0)
      return "检查已完成，部分智能分析未执行";
    return "检查已完成，智能分析失败";
  }
  if (["queued", "collecting", "evaluating"].includes(run.status))
    return stateNames[run.status];
  const counts = health(run);
  if (counts.fail) return `发现 ${counts.fail} 个异常对象`;
  if (counts.unknown) return `${counts.unknown} 个对象待确认`;
  if (run.status === "cancelled") return "本次执行已停止";
  return Object.values(counts).some(Boolean)
    ? "检查对象全部正常"
    : "本次未发现检查对象";
}
export function errorText(error: unknown) {
  const message = error instanceof Error ? error.message : String(error);
  return errorNames[message] || message;
}
export const errorNames: Record<string, string> = {
  ...recoveryErrors,
  inspection_connection_in_use: "连接仍被 API 使用，请先调整相关 API",
  inspection_connection_scope_in_use: "连接仍被 API 使用，暂不能改变服务或环境",
  inspection_connection_version_changed: "连接已被修改，请刷新后再保存",
  inspection_source_scope_unavailable:
    "API 或数据库查询的服务、环境与任务不匹配，或已禁止巡检任务使用",
  inspection_skill_unavailable: "Skill 已停用、过期或不适用于当前服务",
  inspection_discovery_requires_fixed_get:
    "获取检查对象需要使用固定地址的 GET API",
  inspection_coverage_incomplete: "检查未完整覆盖，请查看未完成项与执行过程",
  inspection_agent_budget_exhausted: "达到 Agent 检查上限",
  inspection_collection_incomplete: "对象发现尚未完成，集合检查未执行",
  inspection_duplicate_object: "发现重复对象编号，请检查编号映射和分页",
  source_pagination_invalid: "分页未前进，请核对分页参数和结束条件",
  inspection_check_configuration_changed:
    "检查所用方法、API 或数据库查询已变更",
  inspection_check_failed: "检查执行失败",
  harness_failed: "Agent 运行进程异常，评估未完成",
  provider_unavailable: "模型连接失败，评估未完成",
  provider_timeout: "等待模型响应超时，评估未完成",
  analysis_timeout: "Agent 执行超时，评估未完成",
  invalid_response: "模型返回格式不符合要求，评估未完成",
  source_http_error: "接口返回错误，请检查地址和认证",
  record_budget_exhausted: "达到本批对象上限，剩余对象将在下一批检查",
  source_timeout: "数据源请求超时",
  source_invalid_page: "接口返回格式与配置不符",
  source_invalid_objects: "对象映射未得到 JSON 对象",
  source_record_outside_window: "记录时间超出扫描范围",
  inspection_source_unavailable: "数据源暂时不可用",
  inspection_timeout: "执行超过计划超时限制",
  inspection_source_changed: "数据源已修改，本次执行已停止",
  database_unavailable: "数据库无法连接",
  authentication_failed: "账号认证失败",
  inspection_agent_failed: "智能分析调用失败",
  inspection_sandbox_unavailable: "Python 沙箱尚未配置或无法连接",
  inspection_sandbox_rejected: "Python 沙箱拒绝执行",
  inspection_workflow_incomplete: "自主巡检未全部完成",
  inspection_api_parameters_required: "动态接口需要在巡检中传入路径参数",
  inspection_api_not_authorized: "接口未获得本计划的查询授权",
  inspection_tool_limit: "已达到本轮工具调用上限",
  inspection_evidence_limit: "已达到本轮证据大小上限",
  python_timeout: "Python 执行超时",
  python_execution_failed: "Python 执行失败",
};
