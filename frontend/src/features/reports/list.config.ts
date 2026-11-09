import type { ListConfig, Row } from "@/types/records";
import { title, state, created } from "@/config/listHelpers";
import { short } from "@/utils/display";

export const reportsLists: Record<string, ListConfig> = {
  reports: {
    title: "复盘报告",
    subtitle: "冻结事故证据；人工生成、编辑、审核和发送",
    read: "reports:read",
    manage: "reports:manage",
    detail: true,
    states: ["queued", "running", "draft", "approved", "failed", "interrupted"],
    dateFilter: true,
    columns: [
      { key: "id", label: "报告编号", width: 130, format: (r) => short(r.id) },
      title("incident_title", "关联事故", 330),
      {
        key: "resource_context",
        label: "所属服务 / 关联资源",
        width: 300,
        format: (r) => {
          const context = r.resource_context;
          const services = context?.services
            ?.map((s: Row) => s.name)
            .join("、");
          const resources = context?.resources
            ?.map((s: Row) => s.name)
            .join("、");
          return [services || "未关联服务", resources]
            .filter(Boolean)
            .join(" · ");
        },
      },
      state,
      title("revision", "版本", 90),
      title("actor_username", "生成人", 130),
      created,
    ],
  },
};
