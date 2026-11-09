import type { Models } from "@/api/client";
import { agentModules } from "./agentConfig";

export type OperationRecord = Models["OperationsAuditResponse"];

const actions: Record<string, string> = {
  resource_restored: "从回收站恢复资源",
  resource_purged: "永久删除回收站资源",
  notification_policy_saved: "保存通知策略",
  platform_mail_saved: "保存平台发件邮箱",
  platform_mail_tested: "发送平台邮箱测试邮件",
  maintenance_saved: "保存维护或静默配置",
  maintenance_deleted: "删除维护或静默配置",
  inhibition_created: "添加告警抑制规则",
  inhibition_deleted: "移除告警抑制规则",
  notification_deleted: "删除个人待办",
  notification_sent: "发送通知邮件",
  notification_failed: "通知邮件发送失败",
  notification_unknown: "通知邮件发送结果待确认",
  diagnosis_reviewed: "评价诊断结果",
  evaluation_case_created: "创建评测案例",
  evaluation_requested: "发起效果评测",
  evaluation_reviewed: "审核评测结果",
  improvement_imported: "从复盘导入整改任务",
  improvement_updated: "更新整改任务",
  improvement_submitted: "提交整改验收",
  improvement_decided: "审核整改任务",
  knowledge_validity_updated: "更新知识有效期",
  report_knowledge_drafted: "从复盘创建知识草稿",
  agent_result_knowledge_drafted: "从 Agent 结果创建知识草稿",
  agent_result_knowledge_published: "审核并启用 Agent 结果知识",
  agent_config_updated: "修改 Agent 配置",
  agent_config_imported: "导入 Agent 配置",
  agent_channel_saved: "保存模型通道",
  agent_channel_deleted: "删除模型通道",
  agent_channel_tested: "测试模型通道",
  agent_channels_reordered: "调整模型通道顺序",
  inspection_source_configured: "保存巡检数据源",
  inspection_source_tested: "测试巡检数据源",
  inspection_source_deleted: "删除巡检数据源",
  inspection_plan_configured: "保存巡检计划",
  inspection_plan_state_changed: "切换巡检计划状态",
  inspection_plan_deleted: "删除巡检计划",
  inspection_enqueued: "发起巡检",
  inspection_cancelled: "取消巡检",
  inspection_suspended: "暂停巡检计划",
  inspection_run_deleted: "删除巡检批次",
  inspection_item_deleted: "删除巡检结果",
  inspection_reviewed: "复核巡检结果",
  inspection_trial_snapshot_created: "创建巡检试运行快照",
  inspection_plan_copied: "复制巡检任务",
  inspection_query_previewed: "预览巡检数据",
  inspection_connection_configured: "保存巡检 HTTP 连接",
  inspection_connection_deleted: "删除巡检 HTTP 连接",
  role_permissions_updated: "修改角色权限",
  access_requested: "申请访问权限",
  access_approved: "批准访问申请",
  access_rejected: "拒绝访问申请",
  access_withdrawn: "撤回访问申请",
  access_revoked: "撤销访问授权",
  access_policy_updated: "修改访问策略",
  backup_recorded: "登记备份结果",
  prediction_incident_linked: "关联预测与事故",
  cleanup_previewed: "生成清理预览",
  cleanup_confirmed: "确认历史数据清理",
  cleanup_archived: "归档历史数据",
  model_price_added: "新增模型价格（历史记录）",
  usage_budget_saved: "保存预算（历史记录）",
};
const kinds: Record<string, string> = {
  recycle_bin: "回收站",
  notification_policy: "通知策略",
  platform_mail: "平台发件邮箱",
  maintenance: "维护与静默",
  inhibition: "告警抑制",
  notification: "通知邮件",
  analysis: "事故诊断",
  evaluation_case: "评测案例",
  evaluation_run: "效果评测",
  improvement_task: "整改任务",
  knowledge_document: "知识文档",
  knowledge_skill: "知识技能",
  agent_config: "Agent 配置",
  inspection_plan: "巡检计划",
  inspection_run: "巡检批次",
  inspection_item: "巡检结果",
  inspection_data_source: "巡检数据源",
  role: "角色权限",
  access_request: "访问申请",
  backup_record: "备份记录",
  prediction_outcome: "预测结果",
  retention_plan: "数据清理",
  archive: "历史归档",
  model_price: "历史价格配置",
  usage_budget: "历史预算配置",
  server: "服务器",
  container: "容器",
  application: "业务服务",
  dashboard: "看板",
  llm_endpoint: "模型接口",
  knowledge: "知识文档",
  skill: "知识技能",
};

export const operationName = (row: OperationRecord) =>
  actions[row.action] || "其他操作";
export const operationKind = (row: OperationRecord) =>
  kinds[row.resource_kind] || "其他对象";
export const operationActor = (row: OperationRecord) =>
  row.actor_username || (row.user_id ? "账号不可用" : "系统任务");
export function operationTarget(row: OperationRecord) {
  if (row.resource_kind === "agent_config")
    return agentModules[String(row.detail.module)] || "Agent 配置";
  if (row.resource_kind === "role")
    return (
      { admin: "管理员", operator: "运维人员", viewer: "查看者" }[
        String(row.detail.code)
      ] || "角色权限"
    );
  if (row.resource_kind === "notification_policy") {
    if (typeof row.detail.managed !== "boolean") return "通知策略";
    return row.detail.managed
      ? `平台通知 · 邮件${row.detail.email_enabled ? "已开启" : "已关闭"}`
      : "Alertmanager 邮件";
  }
  return row.resource_name || operationKind(row);
}
export function windowState(
  row: { starts_at: string; ends_at: string },
  now = Date.now(),
) {
  if (new Date(row.ends_at).getTime() <= now) return "已结束";
  return new Date(row.starts_at).getTime() > now ? "未开始" : "生效中";
}
