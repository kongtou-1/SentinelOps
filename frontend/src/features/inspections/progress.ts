import type { Models } from "@/api/client";
import { stateNames } from "./ui";

export type ProgressEvent = Models["InspectionProgressEvent"];
export const activeRun = (status: string) =>
  ["queued", "collecting", "evaluating"].includes(status);

const tools: Record<string, string> = {
  read_inspection_skill: "读取检查方法",
  get_inspection_object: "读取对象数据",
  get_inspection_record: "读取对象数据",
  get_inspection_checks: "读取规则结果",
  list_inspection_queries: "查看授权查询",
  list_inspection_apis: "查看授权接口",
  request_inspection_api: "查询业务接口",
  query_inspection_database: "查询只读数据库",
  execute_inspection_python: "执行 Python",
  search_agent_history: "检索历史结果",
  read_agent_history: "读取历史结果",
  search_knowledge: "检索知识",
};
export function eventTitle(event: ProgressEvent) {
  switch (event.kind) {
    case "queued":
      return "巡检进入队列";
    case "started":
      return "开始巡检 · 获取检查对象";
    case "object":
      return "发现检查对象";
    case "check_started":
      return `开始检查 · ${event.check_name}`;
    case "check_finished":
      return `检查${event.status === "error" ? "出错" : "完成"} · ${event.check_name}`;
    case "tool":
      return tools[event.tool || ""] || event.tool || "调用工具";
    case "finished":
      return `巡检${stateNames[event.status] || event.status}`;
  }
}
export function eventTone(event: ProgressEvent) {
  if (["error", "failed", "fail"].includes(event.status)) return "danger";
  if (["partial", "cancelled", "unknown"].includes(event.status))
    return "warning";
  if (["queued", "started", "object", "check_started"].includes(event.kind))
    return "info";
  return "success";
}
export function eventState(event: ProgressEvent) {
  if (event.kind === "tool") return event.status === "error" ? "失败" : "成功";
  if (event.kind === "check_finished")
    return event.status === "error" ? "执行错误" : stateNames[event.status];
  return "";
}
export function mergeEvents(
  previous: ProgressEvent[],
  incoming: ProgressEvent[],
) {
  const entries = new Map(previous.map((event) => [event.id, event]));
  incoming.forEach((event) => entries.set(event.id, event));
  return [...entries.values()].sort(compareEvents);
}

export function compareEvents(a: ProgressEvent, b: ProgressEvent) {
  const fraction = (at: string) =>
    Number((at.match(/\.(\d+)/)?.[1] || "").padEnd(6, "0").slice(0, 6));
  return (
    Date.parse(a.occurred_at) - Date.parse(b.occurred_at) ||
    fraction(a.occurred_at) - fraction(b.occurred_at) ||
    (a.id < b.id ? -1 : a.id > b.id ? 1 : 0)
  );
}
