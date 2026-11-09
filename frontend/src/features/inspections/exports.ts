import type { CsvColumn } from "@/utils/csv";
import { date } from "@/utils/display";
import {
  health,
  runName,
  runResult,
  stateNames,
  type Item,
  type Run,
} from "./ui";
import { itemTitle } from "./display";

export const runExportColumns: CsvColumn<Run>[] = [
  { label: "批次编号", value: (r) => r.id },
  { label: "巡检任务", value: runName },
  { label: "执行时间", value: (r) => date(r.started_at || r.created_at) },
  { label: "完成时间", value: (r) => date(r.finished_at) },
  { label: "检查对象", value: (r) => Number(r.summary.objects || 0) },
  { label: "正常对象", value: (r) => health(r).pass || 0 },
  { label: "异常对象", value: (r) => health(r).fail || 0 },
  { label: "待确认对象", value: (r) => health(r).unknown || 0 },
  { label: "执行状态", value: (r) => stateNames[r.status] || r.status },
  { label: "执行说明", value: runResult },
];

export const itemExportColumns: CsvColumn<Item>[] = [
  { label: "对象名称", value: (r, index) => String(itemTitle(r, index + 1)) },
  { label: "业务编号", value: (r) => r.record_id },
  {
    label: "检查状态",
    value: (r) => stateNames[r.final_status] || r.final_status,
  },
  { label: "检查说明", value: (r) => r.explanation },
  {
    label: "智能分析状态",
    value: (r) => stateNames[r.agent_status] || r.agent_status,
  },
  { label: "观察时间", value: (r) => date(r.observed_at) },
  { label: "批次编号", value: (r) => r.run_id },
];
