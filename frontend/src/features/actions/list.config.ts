import type { ListConfig } from "@/types/records";
import { title, state, created } from "@/config/listHelpers";
import { date, effectiveApproval, label, short } from "@/utils/display";
import { actionName } from "./catalog";

export const actionsLists: Record<string, ListConfig> = {
  approvals: {
    title: "审批列表",
    subtitle: "审批绑定具体目标和参数；批准后仍需人工发起执行",
    read: "executions:read",
    detail: true,
    states: [
      "pending",
      "approved",
      "rejected",
      "consumed",
      "expired",
      "invalidated",
    ],
    dateFilter: true,
    columns: [
      { key: "id", label: "编号", width: 110, format: (r) => short(r.id) },
      { ...title("action", "动作", 180), format: (r) => actionName(r.action) },
      title("container_name", "目标容器", 180),
      title("actor_username", "申请人", 120),
      { ...state, format: (r) => label(effectiveApproval(r)) },
      {
        key: "expires_at",
        label: "有效期至",
        width: 175,
        format: (r) => date(r.expires_at),
      },
    ],
  },
  executions: {
    title: "动作执行记录",
    subtitle: "人工发起、前置检查、执行、恢复验证与审计",
    read: "executions:read",
    exportColumns: [
      { label: "记录编号", value: (r) => r.id },
      { label: "执行动作", value: (r) => actionName(r.action) },
      { label: "目标容器", value: (r) => r.container_name || "容器名称不可用" },
      { label: "关联事故", value: (r) => r.incident_title },
      { label: "发起人", value: (r) => r.actor_username },
      { label: "状态", value: (r) => label(r.status) },
      { label: "发起时间", value: (r) => date(r.created_at) },
      { label: "完成时间", value: (r) => date(r.finished_at) },
      { label: "错误代码", value: (r) => r.error_code },
    ],
    detail: true,
    states: [
      "queued",
      "prechecking",
      "executing",
      "verifying",
      "succeeded",
      "failed",
      "interrupted",
      "unknown",
    ],
    dateFilter: true,
    columns: [
      { key: "id", label: "编号", width: 110, format: (r) => short(r.id) },
      { ...title("action", "动作", 180), format: (r) => actionName(r.action) },
      title("container_name", "目标容器", 180),
      title("actor_username", "发起人", 120),
      state,
      created,
    ],
  },
};
