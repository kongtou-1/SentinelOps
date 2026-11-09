import type { Models } from "@/api/client";
import { label } from "@/utils/display";

export type AuditRecord = Models["AuditResponse"];
export const auditActions: Record<string, string> = {
  approval_created: "创建执行审批",
  approval_requested: "申请执行审批",
  approval_approved: "批准执行审批",
  approval_rejected: "驳回执行审批",
  approval_invalidated: "执行审批失效",
  approval_denied: "审批操作被拒绝",
  execution_requested: "发起执行",
  execution_queued: "执行任务进入队列",
  execution_prechecking: "执行前置检查",
  execution_executing: "执行运维动作",
  execution_verifying: "验证恢复结果",
  execution_succeeded: "运维动作执行成功",
  execution_failed: "运维动作执行失败",
  execution_denied: "执行请求被拒绝",
  execution_interrupted: "执行任务中断",
  execution_unknown: "执行结果待确认",
  report_queued: "发起复盘报告生成",
  report_generated: "复盘报告生成完成",
  report_failed: "复盘报告生成失败",
  report_interrupted: "复盘报告生成中断",
  report_edited: "编辑复盘报告",
  report_approved: "审核通过复盘报告",
  report_changes_requested: "要求修改复盘报告",
  report_deleted: "删除复盘报告",
  report_mail_queued: "发起复盘邮件发送",
  report_mail_dispatching: "发送复盘邮件",
  report_mail_sent: "复盘邮件发送成功",
  report_mail_failed: "复盘邮件发送失败",
  report_mail_interrupted: "复盘邮件发送中断",
  report_mail_unknown: "复盘邮件发送结果待确认",
  repair_handoff_created: "创建修复交接单",
  repair_result_recorded: "登记修复结果",
  repair_verification_recorded: "登记修复验证结果",
};
export const auditActionName = (action: string) =>
  auditActions[action] || "其他操作";
export const auditActorName = (
  row: Pick<AuditRecord, "actor_username" | "user_id">,
) => row.actor_username || (row.user_id ? "账号不可用" : "系统任务");
export const auditIncidentName = (row: Pick<AuditRecord, "incident_title">) =>
  row.incident_title || "事故名称未保留";

const reasons: Record<string, string> = {
  invalid_state: "审批状态已变化",
  not_approved: "尚未获得有效审批",
  container_busy: "目标容器已有执行任务",
  approval_expired: "审批已过期",
  approval_unavailable: "审批已失效或不可用",
  approver_unavailable: "审批人权限已失效",
  initiator_unavailable: "执行发起人权限已失效",
  target_unavailable: "执行目标不可用",
  binding_changed: "审批绑定的目标或参数已变化",
  host_key_mismatch: "服务器身份校验失败",
  ssh_timeout: "服务器连接超时",
  ssh_unavailable: "服务器连接不可用",
  invalid_container_state: "容器状态不符合执行条件",
  report_generation_failed: "报告生成失败",
  report_context_too_large: "报告资料超过容量限制",
  smtp_rejected: "邮件服务器拒绝发送",
  smtp_unavailable: "邮件服务器不可用",
  smtp_result_unknown: "邮件发送结果未确认",
  authorization_changed: "发送授权已变化",
};
export function auditReason(row: Pick<AuditRecord, "detail">) {
  const code = row.detail.error_code || row.detail.reason;
  if (!code) return "";
  return (
    reasons[String(code)] ||
    (label(code) !== String(code) ? label(code) : "详细原因见排查信息")
  );
}
