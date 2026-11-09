import type { ListConfig } from "@/types/records";
import { title, state } from "@/config/listHelpers";
import { date, label, short } from "@/utils/display";

export const incidentsLists: Record<string, ListConfig> = {
  incidents: {
    title: "事故中心",
    subtitle: "关联告警、诊断、审批执行与复盘的处理中心",
    read: "incidents:read",
    exportColumns: [
      { label: "记录编号", value: (r) => r.id },
      { label: "事故标题", value: (r) => r.title },
      { label: "级别", value: (r) => label(r.severity) },
      { label: "状态", value: (r) => label(r.status) },
      {
        label: "负责人",
        value: (r) =>
          r.assignee_name || (r.assignee_id ? "账号不可用" : "未分配"),
      },
      { label: "开始时间", value: (r) => date(r.started_at) },
      { label: "恢复时间", value: (r) => date(r.recovered_at) },
      { label: "关闭时间", value: (r) => date(r.closed_at) },
      {
        label: "关闭方式",
        value: (r) =>
          r.status === "closed"
            ? r.closure?.forced
              ? "管理员强制关闭"
              : "正常关闭"
            : "—",
      },
    ],
    detail: true,
    states: ["open", "in_progress", "recovered", "closed", "merged"],
    dateFilter: true,
    columns: [
      { key: "id", label: "编号", width: 120, format: (r) => short(r.id) },
      title("title", "事故标题", 350),
      { key: "severity", label: "级别", status: true, width: 100 },
      state,
      {
        key: "closure",
        label: "关闭方式",
        width: 140,
        format: (r) =>
          r.closure?.forced
            ? "管理员强制关闭"
            : r.status === "closed"
              ? "正常关闭"
              : "—",
      },
      title("assignee_id", "负责人", 160),
      {
        key: "started_at",
        label: "开始时间",
        width: 175,
        format: (r) => date(r.started_at),
      },
    ],
  },
};
