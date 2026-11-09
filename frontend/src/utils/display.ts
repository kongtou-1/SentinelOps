export const labels: Record<string, string> = {
  database_busy: "数据库正在执行其他读取",
  database_unavailable: "数据库连接不可用",
  database_configuration_changed: "数据库配置已变化，请重新诊断",
  database_environment_ambiguous: "无法确定数据库所属环境",
  database_service_ambiguous: "无法确定数据库所属服务",
  database_context_too_large: "数据库资料超过本轮容量限制",
  database_queries_disabled: "尚未启用业务数据读取",
  database_permissions_unverified: "数据库只读权限未通过核验",
  query_timeout: "数据库读取超时",
  schema_changed: "数据库结构已变化",
  schema_too_large: "数据库结构超过容量限制",
  permission_denied: "数据库读取权限不足",
  table_not_allowed: "表不在授权范围内",
  column_not_allowed: "字段不在授权范围内",
  unsupported_query: "查询包含尚未支持的语法",
  function_not_allowed: "查询包含不允许的函数",
  explicit_columns_required: "查询需要明确指定字段",
  credential_unavailable: "数据库凭据无法解密",
  tls_required: "数据库未建立要求的 TLS 连接",
  source_target_unresolved: "告警尚未明确关联到服务实例",
  deployment_missing: "告警发生时没有部署记录",
  deployment_ambiguous: "告警发生时的部署记录存在冲突",
  deployment_identity_unverified: "部署镜像身份未核验",
  build_source_unverified: "构建源码关联未核验",
  release_source_unavailable: "对应发布的源码尚未就绪",
  reference_source_version_unverified: "使用其他快照作参考，版本未核验",
  local_source_version_unverified: "本地目录仅作参考，部署版本未核验",
  source_unavailable_continue_runtime: "没有源码，继续使用运行证据",
  source_context_unrecorded: "历史诊断未记录源码上下文",
  source_context_limit: "开发资料超过本轮容量限制",
  source_not_allowed: "源码不在本轮授权范围内",
  runtime_path_unmapped: "运行路径没有对应源码映射",
  runtime_path_ambiguous: "运行路径对应多个部署映射",
  snapshot_storage_unavailable: "快照文件存储不可用",
  snapshot_integrity_error: "快照文件完整性检查失败",
  snapshot_identity_changed: "快照版本身份已改变",
  source_file_not_in_snapshot: "文件不在本轮快照中",
  admin: "管理员",
  operator: "运维人员",
  viewer: "查看者",
  warning: "警告",
  critical: "严重",
  open: "待处理",
  in_progress: "处理中",
  recovered: "已核验恢复",
  closed: "已关闭",
  merged: "已合并",
  firing: "触发中",
  resolved: "告警已结束",
  pending: "待审批",
  approved: "已批准",
  rejected: "已拒绝",
  consumed: "已消费",
  expired: "已过期",
  invalidated: "已失效",
  queued: "排队中",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  interrupted: "已中断",
  cancelling: "正在停止",
  cancelled: "已停止本轮",
  context_limit: "上下文超过模型容量",
  analysis_timeout: "诊断超时",
  incident_changed: "事故状态已变化",
  requester_unavailable: "发起人权限已失效",
  configuration_changed: "AI 配置已变化",
  provider_unavailable: "模型服务不可用",
  worker_interrupted: "worker 重启，运行已中断",
  unknown: "未知",
  prechecking: "前置检查",
  executing: "执行中",
  verifying: "验证中",
  succeeded: "执行成功",
  draft: "草稿",
  ready: "索引就绪",
  sending: "发送中",
  sent: "已发送",
  disabled: "已停用",
  stale: "数据过期",
  healthy: "可用",
  unavailable: "不可用",
  stopped: "已停止",
  exited: "已退出",
  restarting: "重启中",
  paused: "已暂停",
  created: "已创建",
  removing: "移除中",
  dead: "异常退出",
  verified: "恢复已核验",
  terminated: "核验已终止",
  normal: "正常",
  possible: "可能超限",
  expected: "预计超限",
  insufficient_data: "数据不足",
  provisional: "初步结果",
  validated: "通过验证",
  interval_low_confidence: "区间可信度不足",
  authentication_failed: "鉴权失败",
  credential_error: "API Key 不可用",
  insufficient_balance: "余额不足",
  rate_limited: "限流",
  service_error: "服务异常",
  request_error: "请求失败",
  timeout: "超时",
  connection_failed: "连接失败",
  invalid_response: "响应无效",
};
export const label = (v: unknown) => labels[String(v)] || String(v ?? "—");
export const recoveryLabel = (v: unknown) =>
  v === "pending" ? "待核验" : label(v);
export const date = (v: unknown) =>
  v
    ? new Date(typeof v === "number" ? v * 1000 : String(v)).toLocaleString(
        "zh-CN",
        { hour12: false },
      )
    : "—";
export const short = (v: unknown) => String(v ?? "—").slice(0, 8);
export function number(v: unknown, digits = 2) {
  const n = Number(v);
  return v === null || v === undefined || !Number.isFinite(n)
    ? "—"
    : n.toLocaleString("zh-CN", { maximumFractionDigits: digits });
}
export const activeTask = (state: string) =>
  [
    "queued",
    "running",
    "prechecking",
    "executing",
    "verifying",
    "sending",
  ].includes(state);
export function effectiveApproval(
  row: { status?: string; expires_at?: string | null },
  now = Date.now(),
) {
  return ["pending", "approved"].includes(row.status || "") &&
    row.expires_at &&
    new Date(row.expires_at).getTime() <= now
    ? "expired"
    : row.status || "unknown";
}
export const units: Record<string, string> = {
  host_cpu_percent: "%",
  host_memory_percent: "%",
  host_disk_percent: "%",
  container_cpu_percent: "%",
  container_memory_bytes: "bytes",
  llm_latency_avg_seconds: "s",
  llm_latency_p95_seconds: "s",
  llm_success_rate: "%",
  llm_error_rate: "%",
  service_probe_success_rate: "%",
  service_error_rate: "%",
  service_request_rate: "请求/s",
  service_request_count: "次/5分钟",
};
export function metricUnit(metric = "") {
  if (
    ["container_memory_bytes", "host_memory_available_bytes"].includes(metric)
  )
    return "GB";
  return (
    units[metric] ||
    (metric.endsWith("_percent")
      ? "%"
      : metric.includes("network") && metric.endsWith("_bytes")
        ? "bytes/s"
        : metric.endsWith("_seconds")
          ? "s"
          : metric.endsWith("_bytes")
            ? "bytes"
            : metric.endsWith("_5m")
              ? "次/5分钟"
              : "")
  );
}
export function metricValue(metric: string, value: unknown) {
  if (value === null || value === undefined) return null;
  const n = Number(value);
  return Number.isFinite(n)
    ? metricUnit(metric) === "GB"
      ? n / 1e9
      : n *
        ([
          "llm_success_rate",
          "llm_error_rate",
          "service_probe_success_rate",
          "service_error_rate",
        ].includes(metric)
          ? 100
          : 1)
    : null;
}
