import type { RouteRecordRaw } from "vue-router";
import { configs } from "@/config/lists";

export const routes: RouteRecordRaw[] = [
  {
    path: "/askdata",
    redirect: "/askdata/chat",
  },
  {
    path: "/askdata/chat",
    component: () => import("@/features/askdata/pages/Chat.vue"),
    meta: { title: "问数对话", permission: "askdata:read" },
  },
  {
    path: "/askdata/settings",
    component: () => import("@/features/askdata/pages/Settings.vue"),
    meta: { title: "问数设置", permission: "askdata:read" },
  },
  {
    path: "/askdata/catalog",
    component: () => import("@/features/askdata/pages/Catalog.vue"),
    meta: { title: "数据目录", permission: "askdata:read" },
  },
  {
    path: "/askdata/models",
    component: () => import("@/features/askdata/pages/Models.vue"),
    meta: { title: "业务模型", permission: "askdata:read" },
  },
  {
    path: "/askdata/analyses",
    component: () => import("@/features/askdata/pages/Analyses.vue"),
    meta: { title: "分析库", permission: "askdata:read" },
  },
  {
    path: "/askdata/statistics",
    component: () => import("@/features/askdata/pages/Statistics.vue"),
    meta: { title: "使用统计", permission: "askdata:manage" },
  },
  {
    path: "/recycle-bin",
    component: () => import("@/features/system/pages/RecycleBin.vue"),
    meta: { title: "回收站", permission: "profile:read" },
  },
  {
    path: "/agent-configs",
    component: () => import("@/features/system/pages/AgentConfigs.vue"),
    meta: { title: "Agent 配置", permission: "system:read" },
  },
  {
    path: "/access",
    component: () => import("@/features/access/pages/Access.vue"),
    meta: { title: "我的权限", permission: "profile:read" },
  },
  { path: "/inspections/runs", redirect: "/inspections" },
  ...["/inspections/plans/new", "/inspections/plans/:planId/edit"].map(
    (path) => ({
      path,
      component: () => import("@/features/inspections/pages/PlanEdit.vue"),
      meta: { title: "配置巡检任务", permission: "inspections:manage" },
    }),
  ),
  ...[
    { path: "/inspections", title: "巡检任务" },
    { path: "/inspections/services/:serviceId", title: "巡检任务" },
    { path: "/inspections/plans/:planId/runs", title: "本任务巡检记录" },
    { path: "/inspections/runs/:id", title: "巡检结果" },
    { path: "/inspections/plans", title: "巡检任务" },
    { path: "/inspections/plans/:planId", title: "巡检任务详情" },
    { path: "/inspections/sources", title: "数据源管理" },
  ].map(({ path, title }) => ({
    path,
    component: () => import("@/features/inspections/pages/Inspections.vue"),
    meta: { title, permission: "inspections:read" },
  })),
  {
    path: "/data-governance",
    component: () => import("@/features/system/pages/DataGovernance.vue"),
    meta: { title: "数据管理", permission: "system:read" },
  },
  {
    path: "/improvements",
    component: () => import("@/features/operations/pages/Improvements.vue"),
    meta: { title: "复盘整改", permission: "reports:read" },
  },
  {
    path: "/knowledge-validity",
    component: () =>
      import("@/features/operations/pages/KnowledgeValidity.vue"),
    meta: { title: "知识有效期", permission: "knowledge:read" },
  },
  {
    path: "/evaluations",
    component: () => import("@/features/operations/pages/Evaluations.vue"),
    meta: { title: "效果评价", permission: "analyses:read" },
  },
  {
    path: "/notifications",
    component: () => import("@/features/operations/pages/Notifications.vue"),
    meta: { title: "我的待办", permission: "profile:read" },
  },
  {
    path: "/notification-settings",
    component: () => import("@/features/system/pages/NotificationSettings.vue"),
    meta: { title: "通知与维护", permission: "system:read" },
  },
  {
    path: "/project-intro",
    component: () => import("@/features/project-intro/pages/ProjectIntro.vue"),
    meta: {
      title: "项目介绍",
      permission: "resources:read",
      introduction: true,
    },
  },
  { path: "/discover", redirect: "/discover/skills" },
  ...(["skills", "tools"] as const).map((tab) => ({
    path: `/discover/${tab}`,
    component: () => import("@/features/market/pages/DiscoverCatalogue.vue"),
    props: { kind: tab === "skills" ? "skill" : "tool" },
    meta: {
      title: tab === "skills" ? "Skill 市场" : "实用工具",
      permission: "market:read",
      discover: true,
    },
  })),
  {
    path: "/discover/mine",
    component: () => import("@/features/market/pages/DiscoverManage.vue"),
    meta: { title: "我的提交", permission: "market:submit", discover: true },
  },
  {
    path: "/discover/manage",
    component: () => import("@/features/market/pages/DiscoverManage.vue"),
    props: { manage: true },
    meta: { title: "内容管理", permission: "market:review", discover: true },
  },
  ...["/discover/items/new", "/discover/items/:id/edit"].map((path) => ({
    path,
    component: () => import("@/features/market/pages/DiscoverEditor.vue"),
    meta: { title: "提交内容", permission: "market:submit", discover: true },
  })),
  {
    path: "/discover/items/:id",
    component: () => import("@/features/market/pages/DiscoverDetail.vue"),
    meta: { title: "内容详情", permission: "market:read", discover: true },
  },
  {
    path: "/login",
    component: () => import("@/features/auth/pages/Login.vue"),
    meta: { title: "登录" },
  },
  {
    path: "/",
    component: () => import("@/features/dashboard/pages/Dashboard.vue"),
    meta: { title: "总览", permission: "dashboards:read" },
  },
  ...Object.entries(configs).map(([key, c]) => ({
    path: "/" + key,
    component: () => import("@/views/RecordList.vue"),
    props: { kind: key },
    meta: { title: c.title, permission: c.read },
  })),
  ...["/applications", "/applications/:id"].map((path) => ({
    path,
    component: () => import("@/features/applications/pages/Applications.vue"),
    meta: { title: "应用与服务", permission: "sources:read" },
  })),
  {
    path: "/knowledge/search",
    component: () => import("@/views/RecordList.vue"),
    props: { kind: "knowledge" },
    meta: { title: "知识检索", permission: "knowledge:read" },
  },
  ...["servers", "containers", "llm-endpoints"].map((key) => ({
    path: `/${key}/:id`,
    component: () => import("@/features/resources/pages/ResourceDetail.vue"),
    props: { kind: key },
    meta: { title: "资源详情", permission: configs[key]!.read },
  })),
  {
    path: "/incidents/:id/diagnosis",
    component: () => import("@/features/diagnosis/pages/DiagnosisPage.vue"),
    meta: { title: "AI 诊断工作台", permission: "analyses:run" },
  },
  {
    path: "/incidents/:id",
    component: () => import("@/features/incidents/pages/IncidentDetail.vue"),
    meta: { title: "事故详情", permission: "incidents:read" },
  },
  {
    path: "/alerts/:id",
    component: () => import("@/features/alerts/pages/AlertDetail.vue"),
    meta: { title: "告警详情", permission: "alerts:read" },
  },
  {
    path: "/predictions/:id",
    component: () =>
      import("@/features/predictions/pages/PredictionDetail.vue"),
    meta: { title: "预测详情", permission: "predictions:read" },
  },
  {
    path: "/prediction-tasks/:id",
    component: () => import("@/features/predictions/pages/TaskDetail.vue"),
    meta: { title: "预测任务详情", permission: "predictions:read" },
  },
  ...["approvals", "executions"].map((key) => ({
    path: `/${key}/:id`,
    component: () => import("@/features/actions/pages/ActionDetail.vue"),
    props: { kind: key },
    meta: {
      title: key === "approvals" ? "审批详情" : "执行详情",
      permission: "executions:read",
    },
  })),
  {
    path: "/reports/:id",
    component: () => import("@/features/reports/pages/ReportDetail.vue"),
    meta: { title: "复盘报告详情", permission: "reports:read" },
  },
  ...["knowledge", "skills"].map((key) => ({
    path: `/${key}/:id`,
    component: () => import("@/features/knowledge/pages/KnowledgeDetail.vue"),
    props: { kind: key },
    meta: {
      title: key === "knowledge" ? "知识文档" : "Skill",
      permission: "knowledge:read",
    },
  })),
  {
    path: "/action-skills",
    component: () => import("@/features/actions/pages/ActionSkills.vue"),
    meta: { title: "执行动作目录", permission: "executions:read" },
  },
  {
    path: "/roles",
    component: () => import("@/features/system/pages/Roles.vue"),
    meta: { title: "角色与权限", permission: "roles:read" },
  },
  {
    path: "/system",
    component: () => import("@/features/system/pages/SystemStatus.vue"),
    meta: { title: "系统状态", permission: "system:read" },
  },
  {
    path: "/forbidden",
    component: () => import("@/views/EmptyPage.vue"),
    props: {
      title: "无访问权限",
      description: "当前账号不能访问此页面，可返回总览。",
    },
  },
  {
    path: "/:pathMatch(.*)*",
    component: () => import("@/views/EmptyPage.vue"),
    props: { title: "页面不存在", description: "请从左侧菜单选择页面。" },
  },
];
