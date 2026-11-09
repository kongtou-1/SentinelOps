import type { ListConfig } from "@/types/records";
import { options, title, state, metricOptions } from "@/config/listHelpers";
import { date, label, recoveryLabel, short } from "@/utils/display";
import { ruleMetrics, metricLabels } from "@/config/metrics";

export const alertsLists: Record<string, ListConfig> = {
  alerts: {
    title: "告警事件",
    subtitle: "告警结束、人工确认与资源恢复核验分别记录",
    read: "alerts:read",
    exportColumns: [
      { label: "记录编号", value: (r) => r.id },
      {
        label: "告警名称",
        value: (r) =>
          r.annotations?.summary || r.labels?.alertname || "未命名告警",
      },
      { label: "级别", value: (r) => label(r.severity) },
      { label: "状态", value: (r) => label(r.status) },
      {
        label: "人工确认",
        value: (r) => (r.acknowledged_at ? "已确认" : "未确认"),
      },
      { label: "恢复核验", value: (r) => recoveryLabel(r.recovery_status) },
      { label: "触发时间", value: (r) => date(r.starts_at) },
      { label: "结束时间", value: (r) => date(r.ends_at) },
    ],
    detail: true,
    states: ["firing", "resolved"],
    dateFilter: true,
    columns: [
      {
        key: "annotations",
        label: "告警名称",
        width: 300,
        format: (r) =>
          r.annotations?.summary || r.labels?.alertname || short(r.id),
      },
      { key: "severity", label: "级别", status: true, width: 90 },
      state,
      {
        key: "acknowledged_at",
        label: "确认",
        width: 110,
        format: (r) => (r.acknowledged_at ? "已确认" : "未确认"),
      },
      {
        key: "recovery_status",
        label: "恢复核验",
        status: true,
        width: 140,
        format: (r) => recoveryLabel(r.recovery_status),
      },
      {
        key: "starts_at",
        label: "触发时间",
        width: 175,
        format: (r) => date(r.starts_at),
      },
    ],
  },
  "alert-rules": {
    title: "告警规则",
    subtitle: "保存修改后，需显式应用规则并重载 Prometheus",
    read: "alerts:read",
    manage: "rules:manage",
    columns: [
      title("code", "规则", 180),
      {
        key: "metric",
        label: "指标",
        width: 240,
        format: (r) => metricLabels[r.metric] || r.metric,
      },
      title("threshold", "阈值", 110),
      {
        key: "auto_diagnose",
        label: "AI 诊断",
        width: 120,
        format: (r) =>
          r.auto_diagnose == null
            ? "跟随全局"
            : r.auto_diagnose
              ? "自动诊断"
              : "仅手动",
      },
      { key: "severity", label: "级别", status: true, width: 100 },
      {
        key: "for_seconds",
        label: "持续",
        width: 110,
        format: (r) => `${r.for_seconds} 秒`,
      },
      {
        key: "enabled",
        label: "启用",
        width: 100,
        format: (r) => (r.enabled ? "是" : "否"),
      },
    ],
    fields: [
      {
        key: "code",
        label: "规则标识",
        required: true,
        hint: "字母或下划线开头，仅字母、数字、下划线",
      },
      {
        key: "metric",
        label: "指标",
        type: "select",
        options: metricOptions(ruleMetrics),
        required: true,
      },
      {
        key: "server_id",
        label: "服务器范围",
        type: "select",
        source: "/servers",
        nullable: true,
      },
      {
        key: "service_monitor_id",
        label: "服务监测范围",
        type: "select",
        source: "/service-monitors",
        nullable: true,
      },
      {
        key: "llm_endpoint_id",
        label: "LLM 范围",
        type: "select",
        source: "/llm-endpoints",
        nullable: true,
      },
      {
        key: "prediction_task_id",
        label: "预测任务范围",
        type: "select",
        source: "/prediction-tasks",
        nullable: true,
      },
      {
        key: "prediction_horizon_hours",
        label: "预测时间范围",
        type: "select",
        options: [
          { label: "1 小时", value: "1" },
          { label: "6 小时", value: "6" },
          { label: "24 小时", value: "24" },
        ],
        nullable: true,
      },
      {
        key: "threshold",
        label: "阈值（原始单位）",
        required: true,
        type: "number",
        min: 0,
        default: 90,
        hint: "可用性阈值为 1，错误标志与异常阈值为 0；百分比单位为 %，延迟单位为秒",
      },
      {
        key: "for_seconds",
        label: "持续时间（秒）",
        required: true,
        type: "number",
        min: 0,
        max: 86400,
        default: 30,
      },
      {
        key: "severity",
        label: "级别",
        required: true,
        type: "select",
        options: options(["warning", "critical"]),
        default: "warning",
      },
      { key: "enabled", label: "启用", type: "switch", default: true },
      {
        key: "auto_diagnose",
        label: "AI 诊断",
        type: "select",
        nullable: true,
        default: "",
        options: [
          { label: "跟随全局", value: "" },
          { label: "自动诊断", value: "true" },
          { label: "仅手动", value: "false" },
        ],
      },
    ],
  },
};
