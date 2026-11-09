import type { ListConfig, Row } from "@/types/records";
import { title, created, metricOptions } from "@/config/listHelpers";
import { date, label } from "@/utils/display";
import { predictionConclusion } from "./model/results";
import { predictionMetrics, metricLabels } from "@/config/metrics";

export const predictionsLists: Record<string, ListConfig> = {
  "prediction-tasks": {
    title: "预测任务",
    subtitle: "周期预测与残差异常检测；新建任务默认停用",
    read: "predictions:read",
    manage: "predictions:manage",
    detail: true,
    columns: [
      title("name", "任务"),
      {
        key: "metric",
        label: "指标",
        width: 250,
        format: (r) => metricLabels[r.metric] || r.metric,
      },
      {
        key: "enabled",
        label: "调度",
        width: 110,
        format: (r) => (r.enabled ? "已启用" : "已停用"),
      },
      {
        key: "interval_seconds",
        label: "间隔",
        width: 110,
        format: (r) => `${r.interval_seconds / 60} 分钟`,
      },
      {
        key: "next_run_at",
        label: "下次调度",
        width: 175,
        format: (r) => (r.enabled ? date(r.next_run_at) : "已停用"),
      },
    ],
    fields: [
      { key: "name", label: "任务名称", required: true },
      {
        key: "resource",
        label: "目标资源",
        type: "select",
        source: "resources",
        required: true,
      },
      {
        key: "metric",
        label: "预测指标",
        type: "select",
        options: metricOptions(predictionMetrics),
        required: true,
      },
      {
        key: "series",
        label: "磁盘 / 网络序列",
        type: "select",
        hint: "需要指定磁盘或网卡时，从实际采集序列中选择",
      },
      {
        key: "threshold",
        label: "风险阈值",
        required: true,
        type: "number",
        min: 0,
        default: 90,
        hint: "使用指标原始单位；LLM 错误率使用 0–1 的比例",
      },
      {
        key: "enabled",
        label: "启用自动调度",
        type: "switch",
        default: false,
        hint: "开启并保存后，按调度间隔自动运行预测；关闭后仍可手动运行。",
      },
      {
        key: "interval_seconds",
        label: "调度间隔（秒）",
        required: true,
        type: "number",
        min: 300,
        max: 86400,
        default: 300,
      },
    ],
  },
  predictions: {
    title: "预测结果",
    subtitle: "查看 1h / 6h / 24h 曲线、预测质量及残差异常",
    read: "predictions:read",
    exportColumns: [
      { label: "记录编号", value: (r) => r.id },
      { label: "预测对象", value: (r) => r.target_name },
      { label: "任务名称", value: (r) => r.config?.name || "任务名称未保留" },
      {
        label: "指标",
        value: (r) =>
          metricLabels[r.config?.metric] || r.config?.metric || "未记录指标",
      },
      { label: "执行状态", value: (r) => label(r.status) },
      { label: "预测时间", value: (r) => date(r.created_at) },
      { label: "数据截至时间", value: (r) => date(r.data_end_at) },
      ...["1", "6", "24"].flatMap((hours) => [
        {
          label: `${hours} 小时结论`,
          value: (r: Row) => predictionConclusion(r, hours).text,
        },
        {
          label: `${hours} 小时首次预计超限时间`,
          value: (r: Row) => date(predictionConclusion(r, hours).breachAt),
        },
      ]),
      { label: "时效", value: (r) => (r.fresh ? "时效内" : "已失效") },
    ],
    detail: true,
    states: ["queued", "running", "completed", "failed", "interrupted"],
    dateFilter: true,
    columns: [
      title("target_name", "预测对象 / 任务", 250),
      {
        key: "metric",
        label: "预测指标",
        width: 130,
        format: (r) =>
          metricLabels[r.config?.metric] || r.config?.metric || "未记录指标",
      },
      { ...created, label: "预测时间" },
      { key: "conclusions", label: "预测结论", width: 310 },
      {
        key: "fresh",
        label: "时效",
        width: 90,
        format: (r) => (r.fresh ? "时效内" : "已失效"),
      },
    ],
  },
};
