import type { ListConfig, Field } from "@/types/records";
import { title, updated } from "@/config/listHelpers";

const knowledgeFields: Field[] = [
  { key: "title", label: "标题", required: true },
  { key: "content", label: "Markdown 正文", type: "textarea", required: true },
  { key: "tags", label: "标签", type: "tags", default: [] },
  { key: "enabled", label: "启用", type: "switch", default: true },
];

export const knowledgeLists: Record<string, ListConfig> = {
  knowledge: {
    title: "知识文档",
    subtitle: "Markdown 运维知识、SOP 与故障案例，索引就绪后参与检索",
    read: "knowledge:read",
    manage: "knowledge:manage",
    detail: true,
    columns: [
      title("title", "标题", 330),
      {
        key: "tags",
        label: "标签",
        width: 230,
        format: (r) => r.tags?.join(" · ") || "—",
      },
      { key: "index_status", label: "索引状态", status: true, width: 135 },
      title("version", "版本", 90),
      updated,
    ],
    fields: [...knowledgeFields, { key: "source", label: "来源", default: "" }],
  },
  skills: {
    title: "Skill",
    subtitle: "Agent 按任务选择并加载的技能，定义适用场景、诊断步骤和输出要求",
    read: "knowledge:read",
    manage: "knowledge:manage",
    detail: true,
    columns: [
      title("code", "标识", 220),
      title("title", "标题", 300),
      title("version", "版本", 90),
      {
        key: "enabled",
        label: "启用",
        width: 100,
        format: (r) => (r.enabled ? "是" : "否"),
      },
      updated,
    ],
    fields: [
      { key: "code", label: "Skill 标识", required: true },
      ...knowledgeFields.map((field) =>
        field.key === "content"
          ? {
              ...field,
              label: "Skill 指令（Markdown）",
              hint: "说明何时使用、检查步骤、所需工具和输出要求；Agent 按需加载后遵循。",
            }
          : field,
      ),
      {
        key: "scripts",
        label: "Python 脚本（JSON：scripts/check.py → 代码）",
        type: "json",
        default: {},
      },
    ],
  },
};
