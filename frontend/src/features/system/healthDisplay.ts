export type HealthTone = "success" | "danger" | "warning" | "info";
export const workerNames: Record<string, string> = {
  inspection: "巡检Agent",
  operations: "运维调度",
  prediction: "风险预测",
  ai: "智能诊断",
  source: "源码同步",
  report: "复盘报告",
  execution: "受控执行",
};
export const taskNames: Record<string, string> = {
  cleanup: "历史数据清理",
  diagnosis: "事故诊断",
  conversations: "诊断对话",
  evaluation: "效果评测",
  prediction: "风险预测",
  source: "源码同步",
  report: "报告生成",
  report_mail: "报告邮件发送",
  notification: "通知邮件发送",
  execution: "审批后执行",
  knowledge: "知识库索引",
};
export function healthState(state?: string): {
  label: string;
  tone: HealthTone;
} {
  switch (state) {
    case "ok":
    case "healthy":
    case "available":
      return { label: "正常", tone: "success" };
    case "unhealthy":
      return { label: "运行异常", tone: "danger" };
    case "stale":
      return { label: "心跳超时", tone: "warning" };
    case "unavailable":
      return { label: "连接失败", tone: "danger" };
    case "not_observed":
      return { label: "未收到心跳", tone: "warning" };
    default:
      return { label: "未确认", tone: "info" };
  }
}
export type WorkerHealth = {
  name: string;
  expected: boolean;
  status: string;
  observed_at?: string | null;
  error_code?: string | null;
};
export function workerState(worker: Pick<WorkerHealth, "expected" | "status">) {
  if (!worker.expected && worker.status === "not_observed")
    return { label: "未启用", tone: "info" as const };
  return healthState(worker.status);
}
export type TaskActivity = {
  name: string;
  queued: number;
  running: number;
  failed_24h: number;
  oldest_wait_seconds: number | null;
};
export function activeTasks(rows: TaskActivity[]) {
  return rows.filter(
    (row) => row.queued > 0 || row.running > 0 || row.failed_24h > 0,
  );
}
export function duration(seconds?: number | null): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.floor(seconds)} 秒`;
  if (seconds < 3600)
    return `${Math.floor(seconds / 60)} 分 ${Math.floor(seconds % 60)} 秒`;
  return `${Math.floor(seconds / 3600)} 小时 ${Math.floor((seconds % 3600) / 60)} 分`;
}
export function capacity(bytes?: number | null): string {
  if (bytes == null) return "—";
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024)
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
}
