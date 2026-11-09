import { metricValue } from "@/utils/display";
export function parseTimeWindow(start: unknown, end: unknown) {
  if (typeof start !== "string" || typeof end !== "string") return undefined;
  const first = Date.parse(start),
    last = Date.parse(end);
  if (
    !Number.isFinite(first) ||
    !Number.isFinite(last) ||
    last <= first ||
    last - first > 31 * 86400000
  )
    return undefined;
  return {
    start: new Date(first).toISOString(),
    end: new Date(last).toISOString(),
  };
}
export const monitoringReasons: Record<string, string> = {
  monitoring_disabled: "监控已暂停",
  target_not_configured: "未配置目标",
  docker_state_not_configured: "未配置 Docker 状态采集器",
  http_probe_not_configured: "未配置 HTTP 探测",
  prometheus_unavailable: "无法连接监控服务",
  awaiting_discovery: "等待服务发现，请核对目标地址",
  scrape_stale: "采样已过期，请检查采集器与 Prometheus",
  scrape_failed: "采集连接失败，请检查地址、端口与网络",
  query_failed: "指标查询失败",
  no_valid_samples: "没有有效样本",
  collector_unavailable: "采集器不可用",
  collector_unknown: "采集器状态未知",
  source_missing_or_stale: "数据缺失或已过期",
  engine_unavailable: "无法读取 Docker Engine，请检查只读套接字挂载",
  probe_failed: "HTTP 探测失败，请检查目标服务",
  healthcheck_not_configured: "容器未配置 HEALTHCHECK",
  docker_identity_unavailable: "缺少有效 Docker 标识",
  data_stale: "最近数据已过期，历史曲线仅供参考",
  traffic_metrics_unconfigured: "真实流量或路由模板未配置",
  monitoring_paused: "监测已暂停",
  unexpected_status: "HTTP 状态码不符合预期",
  assertion_failed: "业务结果断言失败",
  invalid_json: "返回值不是有效 JSON",
  timeout: "探测超时",
  connection_failed: "目标连接失败",
  target_unavailable: "目标不可达",
  collector_invalid_response: "采集器返回值无效",
  credential_unavailable: "探测凭据不可用",
  auth_login_failed: "登录失败，请检查认证地址和登录凭据",
  auth_refresh_failed: "Token 刷新失败",
  auth_rejected: "重新认证后仍被目标 API 拒绝",
  auth_invalid_response: "认证响应无效，请检查 Token 与有效期字段映射",
  auth_missing_refresh_token: "登录响应缺少 Refresh Token，无法测试刷新",
  auth_timeout: "认证请求超时",
  auth_connection_failed: "认证接口连接失败",
  response_too_large: "返回值超过大小限制",
};
export function monitoringReason(reason?: string) {
  return reason ? monitoringReasons[reason] || reason : "";
}
export const availabilityLabels: Record<string, string> = {
  available: "已接通",
  unavailable: "无数据或连接失败",
  unconfigured: "未配置",
  paused: "已暂停",
  stale: "数据过期",
};
export const healthLabels: Record<string, string> = {
  healthy: "健康",
  unhealthy: "不健康",
  starting: "健康检查启动中",
  none: "未配置健康检查",
  stopped: "容器未运行",
  unknown: "健康状态未知",
};
export function chartPoints(
  points: [number, string | number | null][],
  metric: string,
  step?: number,
) {
  const result: [number, number | null][] = [];
  const ordered = [...points].sort((a, b) => a[0] - b[0]);
  for (const [i, [t, v]] of ordered.entries()) {
    const previous = ordered[i - 1];
    if (step && previous && t - previous[0] > step * 1.5)
      result.push([(previous[0] + step) * 1000, null]);
    result.push([t * 1000, metricValue(metric, v)]);
  }
  return result;
}
