export const agentModules: Record<string, string> = {
  diagnosis: "Agent 诊断",
  inspection: "巡检",
  report: "复盘",
  evaluation: "效果评测",
};

export const channelErrors: Record<string, string> = {
  authentication_failed: "认证失败",
  insufficient_balance: "余额不足",
  model_not_found: "模型或接口不存在",
  rate_limited: "请求限流",
  service_error: "服务异常",
  provider_request_error: "请求不兼容",
  provider_timeout: "请求超时",
  provider_unavailable: "连接失败",
  context_limit: "超出上下文限制",
  stream_incomplete: "输出中断",
  invalid_response: "响应格式错误",
  configuration_changed: "配置已变化",
  task_authorization_changed: "任务授权已变化",
  analysis_timeout: "任务时限耗尽",
  model_output_limit: "输出额度耗尽",
  provider_response_too_large: "响应过大",
  tool_not_allowed: "工具调用被拒绝",
  requester_unavailable: "操作人授权已失效",
};

export function channelPriority(index: number) {
  return index === 0 ? "主用" : `备用 ${index}`;
}
