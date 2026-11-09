import type { ListConfig } from "@/types/records";
import { options, title, updated, created } from "@/config/listHelpers";
import { date, label } from "@/utils/display";
import {
  auditActionName,
  auditActorName,
  auditIncidentName,
} from "./auditDisplay";

export const systemLists: Record<string, ListConfig> = {
  users: {
    title: "用户管理",
    subtitle: "每个账号一个固定角色；最后一个有效管理员受保护",
    read: "users:manage",
    manage: "users:manage",
    columns: [
      title("username", "用户名"),
      { key: "notification_email", label: "通知邮箱", width: 220 },
      { key: "role", label: "角色", width: 130, format: (r) => label(r.role) },
      {
        key: "is_active",
        label: "状态",
        width: 110,
        format: (r) => (r.is_active ? "已启用" : "已停用"),
      },
      {
        key: "last_login_at",
        label: "最近登录",
        width: 175,
        format: (r) => date(r.last_login_at),
      },
      created,
    ],
    fields: [
      {
        key: "username",
        label: "用户名",
        required: true,
        hint: "3–64 位小写字母、数字、下划线、点、连字符",
      },
      {
        key: "password",
        label: "密码",
        type: "password",
        hint: "12–128 位；编辑留空保留原密码",
      },
      { key: "notification_email", label: "通知邮箱", default: "" },
      {
        key: "role",
        label: "角色",
        required: true,
        type: "select",
        options: options(["viewer", "operator", "admin"]),
        default: "viewer",
      },
      { key: "is_active", label: "启用账号", type: "switch", default: true },
    ],
  },
  "ssh-credentials": {
    title: "SSH 凭据",
    subtitle: "凭据加密保存；接口不返回密码或私钥",
    read: "credentials:manage",
    manage: "credentials:manage",
    columns: [
      title("name", "名称"),
      {
        key: "auth_type",
        label: "认证方式",
        width: 140,
        format: (r) => (r.auth_type === "private_key" ? "私钥" : "密码"),
      },
      created,
      updated,
    ],
    fields: [
      { key: "name", label: "名称", required: true },
      {
        key: "auth_type",
        label: "认证方式",
        type: "select",
        options: [
          { label: "私钥", value: "private_key" },
          { label: "密码", value: "password" },
        ],
        default: "private_key",
        required: true,
      },
      {
        key: "secret",
        label: "密码或完整私钥",
        type: "textarea",
        required: true,
        hint: "轮换时必须提交新凭据；提交后不再显示",
      },
      {
        key: "passphrase",
        label: "私钥口令",
        type: "password",
        nullable: true,
      },
    ],
  },
  "audit-logs": {
    title: "审计日志",
    subtitle: "只追加写入，可按事故、审批、执行、报告及操作人追溯",
    read: "audit:read",
    exportColumns: [
      { label: "记录编号", value: (r) => r.id },
      { label: "操作时间", value: (r) => date(r.created_at) },
      { label: "操作", value: (r) => auditActionName(r.action) },
      {
        label: "操作人",
        value: (r) =>
          auditActorName({
            actor_username: r.actor_username,
            user_id: r.user_id,
          }),
      },
      {
        label: "关联事故",
        value: (r) => auditIncidentName({ incident_title: r.incident_title }),
      },
      { label: "审批编号", value: (r) => r.approval_id },
      { label: "执行编号", value: (r) => r.execution_id },
      { label: "报告编号", value: (r) => r.report_id },
    ],
    dateFilter: true,
    search: true,
    columns: [
      {
        key: "created_at",
        label: "时间",
        width: 175,
        format: (r) => date(r.created_at),
      },
      {
        key: "action",
        label: "操作",
        width: 230,
        format: (r) => auditActionName(r.action),
      },
      {
        key: "actor_username",
        label: "操作人",
        width: 140,
        format: (r) =>
          auditActorName({
            actor_username: r.actor_username,
            user_id: r.user_id,
          }),
      },
      {
        key: "incident_title",
        label: "关联事故",
        width: 340,
        format: (r) => auditIncidentName({ incident_title: r.incident_title }),
      },
    ],
  },
};
