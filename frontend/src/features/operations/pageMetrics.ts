import { all, query, request } from "@/api/client";
import type { Row } from "@/types/records";
import type { PageMetric } from "@/components/common/PageMetrics.vue";

export const metricNames: Record<string, string[]> = {
  servers: ["服务器", "已启用监控", "未绑定 SSH 凭据"],
  containers: ["清单容器", "最近同步运行", "最近同步停止"],
  applications: ["登记服务", "已启用", "未登记负责人"],
  "llm-endpoints": ["可用服务", "探测异常", "未取得有效探测", "探测已停用"],
  "prediction-tasks": ["预测任务", "调度已启用", "最近运行失败"],
  predictions: ["调度已启用", "最新结果有效", "最近运行失败"],
  alerts: ["活跃告警", "活跃未确认", "活跃严重告警"],
  incidents: ["待处理事故", "处理中", "已恢复待关闭"],
  approvals: ["待审批", "已批准待执行", "已过期"],
  executions: ["近 24 小时执行", "进行中", "近 24 小时失败"],
  reports: ["待审核草稿", "生成中", "生成失败"],
  knowledge: ["知识文档", "可检索", "索引失败"],
  skills: ["Skill", "已启用且有效", "已过期"],
  "alert-rules": ["告警规则", "已启用", "严重级别规则"],
  users: ["启用账号", "停用账号", "有效管理员"],
  "ssh-credentials": ["SSH 凭据", "私钥认证", "密码认证"],
  "audit-logs": ["近 24 小时操作", "近 7 天操作"],
  "inspection-plans": ["巡检任务", "已启用", "已暂停"],
  "inspection-runs": ["执行批次", "进行中", "失败批次"],
  "inspection-data-sources": ["登记查询", "已启用", "最近测试失败"],
  "inspection-http-connections": ["HTTP 连接", "已启用", "已配置认证头"],
  "improvement-tasks": ["待分配", "进行中", "待验收"],
  notifications: ["未读待办", "全部待办"],
  "knowledge-validity": ["已过期", "到期未复核", "7 天内到期"],
};

export const metricTones: Record<string, PageMetric["tone"][]> = {
  servers: [undefined, "success", "warning"],
  containers: [undefined, "success", "danger"],
  applications: [undefined, "success", "warning"],
  "llm-endpoints": ["success", "danger", "warning", undefined],
  "prediction-tasks": [undefined, "success", "danger"],
  predictions: ["success", "success", "danger"],
  alerts: ["warning", "warning", "danger"],
  incidents: ["warning", undefined, "success"],
  approvals: ["warning", "success", "danger"],
  executions: [undefined, "warning", "danger"],
  reports: ["warning", undefined, "danger"],
  knowledge: [undefined, "success", "danger"],
  skills: [undefined, "success", "warning"],
  "alert-rules": [undefined, "success", "danger"],
  users: ["success", undefined, "warning"],
  "ssh-credentials": [undefined, "success", "warning"],
  "inspection-plans": [undefined, "success", "warning"],
  "inspection-runs": [undefined, "warning", "danger"],
  "inspection-data-sources": [undefined, "success", "danger"],
  "inspection-http-connections": [undefined, "success", undefined],
  "improvement-tasks": ["warning", undefined, "warning"],
  notifications: ["warning", undefined],
  "knowledge-validity": ["danger", "warning", "warning"],
};

const inventories = new Set([
  "servers",
  "applications",
  "llm-endpoints",
  "prediction-tasks",
  "predictions",
  "knowledge",
  "skills",
  "alert-rules",
  "users",
  "ssh-credentials",
  "inspection-data-sources",
  "inspection-http-connections",
]);
export function inventoryValues(
  kind: string,
  rows: Row[],
  now = Date.now(),
): (number | null)[] {
  const count = (test: (row: Row) => boolean) => rows.filter(test).length;
  const valid = (row: Row) =>
    !row.expires_at || Date.parse(row.expires_at) > now;
  switch (kind) {
    case "servers":
      return [
        rows.length,
        count((r) => r.monitoring_enabled === true),
        count((r) => !r.credential_id),
      ];
    case "applications":
      return [
        rows.length,
        count((r) => r.enabled === true),
        count((r) => !r.owner?.trim()),
      ];
    case "llm-endpoints":
      return [
        count((r) => r.status === "healthy"),
        count((r) => r.status === "failed"),
        count((r) => ["stale", "unknown"].includes(r.status)),
        count((r) => r.status === "disabled"),
      ];
    case "prediction-tasks":
      return [
        rows.length,
        count((r) => r.enabled === true),
        count((r) => r.latest_result?.status === "failed"),
      ];
    case "predictions":
      return [
        count((r) => r.enabled === true),
        count(
          (r) =>
            r.latest_result?.status === "completed" &&
            r.latest_result.fresh === true,
        ),
        count((r) => r.latest_result?.status === "failed"),
      ];
    case "knowledge":
      return [
        rows.length,
        count((r) => r.enabled && valid(r) && r.index_status === "ready"),
        count((r) => r.index_status === "failed"),
      ];
    case "skills":
      return [
        rows.length,
        count((r) => r.enabled && valid(r)),
        count((r) => !!r.expires_at && !valid(r)),
      ];
    case "alert-rules":
      return [
        rows.length,
        count((r) => r.enabled === true),
        count((r) => r.enabled && r.severity === "critical"),
      ];
    case "users":
      return [
        count((r) => r.is_active === true),
        count((r) => r.is_active === false),
        count((r) => r.is_active && r.role === "admin"),
      ];
    case "ssh-credentials":
      return [
        rows.length,
        count((r) => r.auth_type === "private_key"),
        count((r) => r.auth_type === "password"),
      ];
    case "inspection-http-connections":
      return [
        rows.length,
        count((r) => r.enabled === true),
        count((r) => r.headers_configured === true),
      ];
    case "inspection-data-sources":
      return [
        rows.length,
        count((r) => r.enabled === true),
        count((r) => r.last_test_success === false),
      ];
    default:
      return [];
  }
}

export async function readPageMetrics(
  kind: string,
  scope: Row = {},
  signal?: AbortSignal,
): Promise<PageMetric[]> {
  const labels = metricNames[kind] || [];
  if (kind === "knowledge-validity") {
    const rows = (
      await Promise.all([
        all<Row>("/knowledge", signal),
        all<Row>("/skills", signal),
      ])
    ).flat();
    const now = Date.now();
    const values = [
      rows.filter((r) => r.expires_at && Date.parse(r.expires_at) <= now)
        .length,
      rows.filter(
        (r) => r.enabled && r.review_at && Date.parse(r.review_at) <= now,
      ).length,
      rows.filter(
        (r) =>
          r.enabled &&
          r.expires_at &&
          Date.parse(r.expires_at) > now &&
          Date.parse(r.expires_at) <= now + 604800000,
      ).length,
    ];
    return labels.map((label, i) => ({
      label,
      value: values[i],
      tone: metricTones[kind]?.[i],
    }));
  }
  if (inventories.has(kind)) {
    const endpoint = kind === "predictions" ? "prediction-tasks" : kind;
    const rows = await all<Row>(`/${endpoint}` + query(scope), signal);
    const values = inventoryValues(kind, rows);
    return labels.map((label, i) => ({
      label,
      value: values[i],
      tone: metricTones[kind]?.[i],
    }));
  }
  const since = (days: number) =>
    new Date(Date.now() - days * 86400000).toISOString();
  const count = async (filters: Row = {}): Promise<number> => {
    const result = await request<Row[]>(
      `/${kind}` + query({ ...scope, ...filters, limit: 1 }),
      { signal },
    );
    if (result.total === null || !Number.isFinite(result.total))
      throw new Error("统计总量不可用");
    return result.total;
  };
  const states = (values: string[]) =>
    Promise.all(values.map((status) => count({ status }))).then((values) =>
      values.reduce((a, b) => a + b, 0),
    );
  let values: Promise<number>[];
  switch (kind) {
    case "containers":
      values = [
        count(),
        count({ status: "running" }),
        count({ status: "stopped" }),
      ];
      break;
    case "alerts":
      values = [
        count({ status: "firing" }),
        count({ status: "firing", acknowledged: false }),
        count({ status: "firing", severity: "critical" }),
      ];
      break;
    case "incidents":
      values = [
        count({ status: "open" }),
        count({ status: "in_progress" }),
        count({ status: "recovered" }),
      ];
      break;
    case "approvals":
      values = [
        count({ status: "pending" }),
        count({ status: "approved" }),
        count({ status: "expired" }),
      ];
      break;
    case "executions":
      values = [
        count({ start: since(1) }),
        states(["queued", "prechecking", "executing", "verifying"]),
        count({ status: "failed", start: since(1) }),
      ];
      break;
    case "reports":
      values = [
        count({ status: "draft" }),
        states(["queued", "running"]),
        count({ status: "failed" }),
      ];
      break;
    case "audit-logs":
      values = [count({ start: since(1) }), count({ start: since(7) })];
      break;
    case "inspection-plans":
      values = [count(), count({ enabled: true }), count({ enabled: false })];
      break;
    case "inspection-runs":
      values = [
        count(),
        states(["queued", "collecting", "evaluating"]),
        count({ status: "failed" }),
      ];
      break;
    case "improvement-tasks":
      values = [
        count({ status: "unassigned" }),
        count({ status: "in_progress" }),
        count({ status: "awaiting_review" }),
      ];
      break;
    case "notifications":
      values = [count({ unread_only: true }), count()];
      break;
    default:
      values = [];
  }
  const results = await Promise.allSettled(values);
  return labels.map((label, i) => {
    const result = results[i];
    return {
      label,
      value: result?.status === "fulfilled" ? result.value : null,
      tone: metricTones[kind]?.[i],
    };
  });
}
