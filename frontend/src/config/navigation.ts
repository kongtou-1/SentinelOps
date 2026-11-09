import {
  Monitor,
  DataLine,
  Bell,
  Connection,
  Document,
  Collection,
  Setting,
  MagicStick,
  DocumentChecked,
  Lock,
} from "@element-plus/icons-vue";

export const navigation = [
  {
    title: "总览",
    icon: Monitor,
    items: [["总览", "/", "dashboards:read"]],
  },
  {
    title: "资源中心",
    icon: Connection,
    items: [
      ["服务器", "/servers", "servers:read"],
      ["Docker 容器", "/containers", "containers:read"],
      ["应用与服务", "/applications", "sources:read"],
      ["LLM 服务", "/llm-endpoints", "llm:read"],
    ],
  },
  {
    title: "预测中心",
    icon: DataLine,
    items: [
      ["预测中心", "/predictions", "predictions:read"],
      ["预测任务", "/prediction-tasks", "predictions:read"],
    ],
  },
  {
    title: "告警与事件",
    icon: Bell,
    items: [
      ["事件看板", "/incidents", "incidents:read"],
      ["告警事件", "/alerts", "alerts:read"],
      ["告警规则", "/alert-rules", "alerts:read"],
    ],
  },
  {
    title: "诊断Agent",
    icon: MagicStick,
    items: [
      ["诊断Agent", "/approvals", "executions:read"],
      ["动作执行记录", "/executions", "executions:read"],
      ["执行动作目录", "/action-skills", "executions:read"],
      ["效果评价", "/evaluations", "analyses:read"],
    ],
  },
  {
    title: "巡检Agent",
    icon: DocumentChecked,
    items: [
      ["巡检任务", "/inspections", "inspections:read"],
      ["数据源管理", "/inspections/sources", "inspections:read"],
    ],
  },
  {
    title: "问数Agent",
    icon: DataLine,
    items: [
      ["问数对话", "/askdata/chat", "askdata:read"],
      ["数据目录", "/askdata/catalog", "askdata:read"],
      ["业务模型", "/askdata/models", "askdata:read"],
      ["分析库", "/askdata/analyses", "askdata:read"],
      ["使用统计", "/askdata/statistics", "askdata:manage"],
      ["问数设置", "/askdata/settings", "askdata:read"],
    ],
  },
  {
    title: "复盘报告",
    icon: Document,
    items: [
      ["复盘报告", "/reports", "reports:read"],
      ["复盘整改", "/improvements", "reports:read"],
    ],
  },
  {
    title: "知识与 Skill",
    icon: Collection,
    items: [
      ["知识文档", "/knowledge", "knowledge:read"],
      ["知识检索", "/knowledge/search", "knowledge:read"],
      ["Skill", "/skills", "knowledge:read"],
      ["知识有效期", "/knowledge-validity", "knowledge:read"],
    ],
  },
  {
    title: "权限管理",
    icon: Lock,
    items: [
      ["我的权限", "/access", "profile:read"],
      ["角色与权限", "/roles", "roles:read"],
    ],
  },
  {
    title: "系统管理",
    icon: Setting,
    items: [
      ["用户管理", "/users", "users:manage"],
      ["SSH 凭据", "/ssh-credentials", "credentials:manage"],
      ["审计日志", "/audit-logs", "audit:read"],
      ["系统状态", "/system", "system:read"],
      ["Agent 配置", "/agent-configs", "system:read"],
      ["通知与维护", "/notification-settings", "system:read"],
      ["数据管理", "/data-governance", "system:read"],
      ["回收站", "/recycle-bin", "profile:read"],
    ],
  },
];
