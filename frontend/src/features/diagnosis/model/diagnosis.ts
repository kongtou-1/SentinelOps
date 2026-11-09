import type { Models } from "@/api/client";

export const activeRun = (status?: string) =>
  ["queued", "running", "cancelling"].includes(status || "");
export const messageStatus: Record<string, string> = {
  saved: "已保存",
  dispatched: "正在送达",
  waiting: "等待 Agent 接收",
  accepted: "已接收",
  next_round: "等待下一轮",
  unknown: "接收状态未确认 · 继续时纳入上下文",
  streaming: "正在生成",
  committed: "",
  partial: "未完成回复",
  interrupted: "已中断",
};
export const toolNames: Record<string, string> = {
  list_service_databases: "查看服务数据库",
  get_database_schema: "读取数据库结构",
  query_database_readonly: "查询只读数据库",
  list_source_tree: "查看源码目录",
  search_source: "搜索源码与文档",
  read_source_file: "读取版本源码",
  compare_source_snapshots: "比较源码版本",
  get_alert_context: "读取事故与告警",
  get_server_metrics: "读取服务器指标",
  get_container_metrics: "读取容器指标",
  get_llm_metrics: "读取 LLM 指标",
  get_container_status: "检查容器状态",
  get_recent_logs: "读取容器日志",
  get_predictions: "读取预测结果",
  search_knowledge: "检索知识库",
  list_skills: "查看 Skill 目录",
  read_skill: "加载 Skill 指令",
  propose_action_skill: "校验动作提案",
  submit_diagnosis: "提交候选诊断",
};
export function mergeEvents(
  previous: Models["EventResponse"][],
  incoming: Models["EventResponse"][],
) {
  return [
    ...new Map([...previous, ...incoming].map((e) => [e.seq, e])).values(),
  ].sort((a, b) => a.seq - b.seq);
}
export function stage(events: Models["EventResponse"][], status?: string) {
  if (status === "queued") return "等待诊断 worker";
  if (status === "cancelling") return "正在停止本轮";
  if (!activeRun(status))
    return status === "completed" ? "本轮已完成" : "本轮已结束";
  for (const event of [...events].reverse()) {
    if (event.kind === "model_fallback") return "正在切换备用通道";
    if (event.kind === "tool_started")
      return toolNames[String(event.data.tool)] || "调用只读工具";
    if (event.kind === "model_started") return "等待模型回复";
    if (event.kind === "assistant_delta") return "正在生成回复";
    if (event.kind === "run_started") return "初始化诊断";
  }
  return "诊断运行中";
}
