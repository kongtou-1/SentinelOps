import { api, download, type Models } from "@/api/client";

export type AskDataConnection = Models["AskDataConnectionResponse"];
export type AskDataVersion = Models["AskDataVersionResponse"];
export type AskDataCatalogSummary = Models["AskDataCatalogSummary"];
export type AskDataTablePage = Models["AskDataTablePage"];
export type AskDataTableDetail = Models["AskDataTableDetail"];
export type AskDataTermPage = Models["AskDataTermPage"];
export type AskDataTermDetail = Models["AskDataTermDetail"];
export type AskDataRelationship = Models["AskDataRelationship"];
export type AskDataRelationshipPage = Models["AskDataRelationshipPage"];
export type AskDataMetricPage = Models["AskDataMetricPage"];
export type AskDataDefinitionPage = Models["AskDataDefinitionPage"];
export type AskDataDefinitionResponse = Models["AskDataDefinitionResponse"];
export type AskDataDefinitionInput = Models["AskDataDefinitionInput"];
export type AskDataVersionDiff = Models["AskDataVersionDiff"];

export type AskDataConversation = Models["AskDataConversationResponse"];
export type AskDataConversationInput = Models["AskDataConversationInput"];
export type AskDataConversationDetail = Models["AskDataConversationDetail"];
export type AskDataRun = Models["AskDataRunResponse"];
export type AskDataRunInput = Models["AskDataRunInput"];
export type AskDataRunDetail = Models["AskDataRunDetail"];
export type AskDataRunSummary = Models["AskDataRunSummary"];
export type AskDataEventPage = Models["AskDataEventPage"];
export type AskDataStepEvent = Models["AskDataStepEvent"];
export type AskDataResult = Models["AskDataResult"];
export type AskDataColumnMeta = Models["AskDataColumnMeta"];
export type AskDataChartSpec = Models["AskDataChartSpec"];
export type AskDataEvidence = Models["AskDataEvidence"];
export type AskDataExecutionContext = Models["AskDataExecutionContext"];
export type AskDataCodeRule = Models["AskDataCodeRuleResponse"];
export type AskDataCodeRuleDetail = Models["AskDataCodeRuleDetail"];
export type AskDataCodeRulePage = Models["AskDataCodeRulePage"];
export type AskDataCodeRuleInput = Models["AskDataCodeRuleInput"];
export type AskDataCodeRuleSummary = Models["AskDataCodeRuleSummary"];
export type AskDataSourceSnapshotPage = Models["AskDataSourceSnapshotPage"];
export type AskDataSourceSnapshotOption = Models["AskDataSourceSnapshotOption"];
export type AskDataAnalysis = Models["AskDataAnalysisResponse"];
export type AskDataAnalysisPage = Models["AskDataAnalysisPage"];
export type AskDataAnalysisCreate = Models["AskDataAnalysisCreate"];
export type AskDataAnalysisUpdate = Models["AskDataAnalysisUpdate"];
export type AskDataAnalysisRun = Models["AskDataAnalysisRunResponse"];
export type AskDataZone = Models["AskDataZoneResponse"];
export type AskDataZoneInput = Models["AskDataZoneInput"];
export type AskDataTag = Models["AskDataTagResponse"];
export type AskDataTagInput = Models["AskDataTagInput"];
export type AskDataRule = Models["AskDataRuleResponse"];
export type AskDataRuleInput = Models["AskDataRuleInput"];
export type AskDataSensitiveField = Models["AskDataSensitiveFieldResponse"];
export type AskDataSensitiveFieldInput = Models["AskDataSensitiveFieldInput"];
export type AskDataSupplement = Models["AskDataSupplementResponse"];
export type AskDataSupplementInput = Models["AskDataSupplementInput"];
export type AskDataExecutionSettings = Models["AskDataExecutionSettingsResponse"];
export type AskDataExecutionSettingsInput = Models["AskDataExecutionSettingsInput"];
export type AskDataConnectionTest = Models["AskDataConnectionTestResponse"];
export type AskDataImportPreview = Models["AskDataImportPreview"];
export type AskDataImportRecord = Models["AskDataImportResponse"];
export type AskDataStatistics = Models["AskDataStatisticsResponse"];

export interface ChatTurn {
  runId: string;
  connectionId: string;
  modelVersionId: string;
  question: string;
  status: string;
  errorCode: string | null;
  clarification: string | null;
  steps: AskDataStepEvent[];
  result: AskDataResult | null;
  streaming: boolean;
  createdAt: string;
}

const root = "/askdata";
const versions = (id: string) => `${root}/versions/${id}`;
const runs = (id: string) => `${root}/runs/${id}`;

function query(params: Record<string, string | number | boolean | undefined>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const askdata = {
  connections: () => api<AskDataConnection[]>(`${root}/settings/connections`),
  versions: (connectionId: string) =>
    api<AskDataVersion[]>(`${root}/connections/${connectionId}/versions`),
  createVersion: (connectionId: string, body: { source_snapshot_id?: string } = {}) =>
    api<AskDataVersion>(`${root}/connections/${connectionId}/versions`, "POST", body),
  version: (id: string) => api<AskDataVersion>(versions(id)),
  publish: (id: string) => api<AskDataVersion>(`${versions(id)}/publish`, "POST"),
  retire: (id: string) => api<AskDataVersion>(`${versions(id)}/retire`, "POST"),
  cancel: (id: string) => api<AskDataVersion>(`${versions(id)}/cancel`, "POST"),

  catalog: (id: string) => api<AskDataCatalogSummary>(`${versions(id)}/catalog`),
  tables: (id: string, params: Record<string, string | number | undefined>) =>
    api<AskDataTablePage>(`${versions(id)}/tables${query(params)}`),
  table: (id: string, tableId: string) =>
    api<AskDataTableDetail>(`${versions(id)}/tables/${encodeURIComponent(tableId)}`),
  terms: (id: string, params: Record<string, string | number | undefined>) =>
    api<AskDataTermPage>(`${versions(id)}/terms${query(params)}`),
  term: (id: string, termId: string) =>
    api<AskDataTermDetail>(`${versions(id)}/terms/${encodeURIComponent(termId)}`),
  relationships: (id: string) =>
    api<AskDataRelationshipPage>(`${versions(id)}/relationships?limit=500`),
  metrics: (id: string, params: Record<string, string | number | undefined>) =>
    api<AskDataMetricPage>(`${versions(id)}/metrics${query(params)}`),

  definitions: (id: string, params: Record<string, string | number | boolean | undefined> = {}) =>
    api<AskDataDefinitionPage>(`${versions(id)}/definitions${query(params)}`),
  reviseDefinition: (id: string, body: AskDataDefinitionInput) =>
    api<AskDataDefinitionResponse>(`${versions(id)}/definitions`, "PUT", body),
  resolveDefinition: (id: string, definitionId: string) =>
    api<AskDataDefinitionResponse>(
      `${versions(id)}/definitions/${definitionId}/resolve`,
      "POST",
    ),
  diff: (id: string, compareId: string) =>
    api<AskDataVersionDiff>(`${versions(id)}/diff${query({ compare: compareId })}`),

  sourceSnapshots: (connectionId: string) =>
    api<AskDataSourceSnapshotPage>(
      `${root}/connections/${connectionId}/source-snapshots`,
    ),
  codeRuleSummary: (id: string) =>
    api<AskDataCodeRuleSummary>(`${versions(id)}/code-rules/summary`),
  codeRules: (
    id: string,
    params: Record<string, string | number | boolean | undefined> = {},
  ) => api<AskDataCodeRulePage>(`${versions(id)}/code-rules${query(params)}`),
  codeRule: (id: string, ruleId: string) =>
    api<AskDataCodeRuleDetail>(
      `${versions(id)}/code-rules/${encodeURIComponent(ruleId)}`,
    ),
  reviseCodeRule: (id: string, ruleId: string, body: AskDataCodeRuleInput) =>
    api<AskDataCodeRule>(
      `${versions(id)}/code-rules/${encodeURIComponent(ruleId)}`,
      "PUT",
      body,
    ),
  resolveCodeRule: (id: string, ruleId: string) =>
    api<AskDataCodeRule>(
      `${versions(id)}/code-rules/${encodeURIComponent(ruleId)}/resolve`,
      "POST",
    ),
  resolveCodeRuleConflict: (
    id: string,
    ruleId: string,
    action: "keep_manual" | "accept_code",
  ) =>
    api<AskDataCodeRule>(
      `${versions(id)}/code-rules/${encodeURIComponent(ruleId)}/conflict`,
      "POST",
      { action },
    ),

  conversations: (
    params: { connection_id?: string; limit?: number; offset?: number } = {},
  ) => api<AskDataConversation[]>(`${root}/conversations${query(params)}`),
  createConversation: (body: AskDataConversationInput) =>
    api<AskDataConversation>(`${root}/conversations`, "POST", body),
  conversation: (id: string) =>
    api<AskDataConversationDetail>(`${root}/conversations/${id}`),
  deleteConversation: (id: string) => api<void>(`${root}/conversations/${id}`, "DELETE"),
  createRun: (conversationId: string, body: AskDataRunInput) =>
    api<AskDataRun>(`${root}/conversations/${conversationId}/runs`, "POST", body),
  run: (id: string) => api<AskDataRunDetail>(runs(id)),
  runEvents: (id: string, afterSeq: number, limit = 200) =>
    api<AskDataEventPage>(`${runs(id)}/events${query({ after_seq: afterSeq, limit })}`),
  runStream: (id: string, afterSeq: number) =>
    `${runs(id)}/stream${query({ after_seq: afterSeq })}`,
  cancelRun: (id: string) => api<AskDataRun>(`${runs(id)}/cancel`, "POST"),
  exportRun: (id: string) => download(`${runs(id)}/export`),

  analyses: (params: Record<string, string | number | undefined> = {}) =>
    api<AskDataAnalysisPage>(`${root}/analyses${query(params)}`),
  analysis: (id: string) => api<AskDataAnalysis>(`${root}/analyses/${id}`),
  createAnalysis: (body: AskDataAnalysisCreate) =>
    api<AskDataAnalysis>(`${root}/analyses`, "POST", body),
  updateAnalysis: (id: string, body: AskDataAnalysisUpdate) =>
    api<AskDataAnalysis>(`${root}/analyses/${id}`, "PUT", body),
  deleteAnalysis: (id: string) => api<void>(`${root}/analyses/${id}`, "DELETE"),
  executeAnalysis: (id: string) =>
    api<AskDataAnalysisRun>(`${root}/analyses/${id}/execute`, "POST"),

  executionSettings: (connectionId: string) =>
    api<AskDataExecutionSettings>(`${root}/settings/${connectionId}/execution`),
  updateExecutionSettings: (connectionId: string, body: AskDataExecutionSettingsInput) =>
    api<AskDataExecutionSettings>(
      `${root}/settings/${connectionId}/execution`,
      "PUT",
      body,
    ),
  testExecution: (connectionId: string) =>
    api<AskDataConnectionTest>(`${root}/settings/${connectionId}/execution/test`, "POST"),

  zones: (connectionId: string) =>
    api<{ items: AskDataZone[]; total: number }>(`${root}/settings/${connectionId}/zones`),
  createZone: (connectionId: string, body: AskDataZoneInput) =>
    api<AskDataZone>(`${root}/settings/${connectionId}/zones`, "POST", body),
  updateZone: (connectionId: string, id: string, body: AskDataZoneInput) =>
    api<AskDataZone>(`${root}/settings/${connectionId}/zones/${id}`, "PUT", body),
  deleteZone: (connectionId: string, id: string) =>
    api<void>(`${root}/settings/${connectionId}/zones/${id}`, "DELETE"),

  tags: (connectionId: string) =>
    api<{ items: AskDataTag[]; total: number }>(`${root}/settings/${connectionId}/tags`),
  createTag: (connectionId: string, body: AskDataTagInput) =>
    api<AskDataTag>(`${root}/settings/${connectionId}/tags`, "POST", body),
  updateTag: (connectionId: string, id: string, body: AskDataTagInput) =>
    api<AskDataTag>(`${root}/settings/${connectionId}/tags/${id}`, "PUT", body),
  deleteTag: (connectionId: string, id: string) =>
    api<void>(`${root}/settings/${connectionId}/tags/${id}`, "DELETE"),

  rules: (connectionId: string) =>
    api<{ items: AskDataRule[]; total: number }>(`${root}/settings/${connectionId}/rules`),
  createRule: (connectionId: string, body: AskDataRuleInput) =>
    api<AskDataRule>(`${root}/settings/${connectionId}/rules`, "POST", body),
  updateRule: (connectionId: string, id: string, body: AskDataRuleInput) =>
    api<AskDataRule>(`${root}/settings/${connectionId}/rules/${id}`, "PUT", body),
  deleteRule: (connectionId: string, id: string) =>
    api<void>(`${root}/settings/${connectionId}/rules/${id}`, "DELETE"),

  sensitiveFields: (connectionId: string) =>
    api<{ items: AskDataSensitiveField[]; total: number }>(
      `${root}/settings/${connectionId}/sensitive-fields`,
    ),
  createSensitiveField: (connectionId: string, body: AskDataSensitiveFieldInput) =>
    api<AskDataSensitiveField>(
      `${root}/settings/${connectionId}/sensitive-fields`,
      "POST",
      body,
    ),
  updateSensitiveField: (
    connectionId: string,
    id: string,
    body: AskDataSensitiveFieldInput,
  ) =>
    api<AskDataSensitiveField>(
      `${root}/settings/${connectionId}/sensitive-fields/${id}`,
      "PUT",
      body,
    ),
  deleteSensitiveField: (connectionId: string, id: string) =>
    api<void>(`${root}/settings/${connectionId}/sensitive-fields/${id}`, "DELETE"),

  supplements: (connectionId: string) =>
    api<{ items: AskDataSupplement[]; total: number }>(
      `${root}/settings/${connectionId}/supplements`,
    ),
  createSupplement: (connectionId: string, body: AskDataSupplementInput) =>
    api<AskDataSupplement>(
      `${root}/settings/${connectionId}/supplements`,
      "POST",
      body,
    ),
  updateSupplement: (connectionId: string, id: string, body: AskDataSupplementInput) =>
    api<AskDataSupplement>(
      `${root}/settings/${connectionId}/supplements/${id}`,
      "PUT",
      body,
    ),
  deleteSupplement: (connectionId: string, id: string) =>
    api<void>(`${root}/settings/${connectionId}/supplements/${id}`, "DELETE"),

  exportModel: (connectionId: string) =>
    download(`${root}/settings/${connectionId}/export`),
  imports: (connectionId: string) =>
    api<AskDataImportRecord[]>(`${root}/settings/${connectionId}/imports`),
  previewImport: (connectionId: string, body: { filename: string; document: unknown }) =>
    api<AskDataImportPreview>(
      `${root}/settings/${connectionId}/imports/preview`,
      "POST",
      body,
    ),
  applyImport: (
    connectionId: string,
    importId: string,
    body: { conflict_policy: "skip" | "overwrite" },
  ) =>
    api<AskDataImportRecord>(
      `${root}/settings/${connectionId}/imports/${importId}/apply`,
      "POST",
      body,
    ),

  statistics: (params: { connection_id?: string; days?: number } = {}) =>
    api<AskDataStatistics>(`${root}/statistics${query(params)}`),
};

export const buildSteps: Record<string, string> = {
  queued: "排队中",
  extracting_source_rules: "抽取源码业务规则",
  ingesting_catalog: "抽取数据目录",
  compiling_semantics: "编译业务语义",
  resolving_relationships: "推断关联关系",
  building_metrics: "生成指标表达式",
  completed: "构建完成",
};

export const buildStatus: Record<string, string> = {
  queued: "排队中",
  running: "构建中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
};

export const versionStatus: Record<string, string> = {
  draft: "草稿",
  ready: "可启用",
  failed: "构建失败",
  retired: "已停用",
};

export const runSteps: Record<string, string> = {
  queued: "排队中",
  planning: "解析问题",
  resolving_context: "匹配上下文",
  retrieving_evidence: "检索口径依据",
  generating_sql: "生成查询",
  validating_sql: "校验查询",
  executing_sql: "执行查询",
  fetching_result: "读取结果",
  analyzing_result: "分析结果",
  summarizing: "生成答案",
  rendering_chart: "生成图表",
  completed: "完成",
  failed: "失败",
  cancelled: "已取消",
};

export const runStatus: Record<string, string> = {
  queued: "排队中",
  running: "执行中",
  completed: "已完成",
  needs_clarification: "需要澄清",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
};

export const runErrorLabels: Record<string, string> = {
  agent_failed: "问数模型处理失败",
  query_failed: "问数执行失败",
  connection_unavailable: "数据库连接不可用",
  model_not_ready: "业务模型尚未就绪",
  permission_revoked: "查询权限已撤销",
  sql_validation_failed: "查询校验未通过",
  sql_execution_failed: "查询执行失败",
  query_timeout: "查询超时",
  result_too_large: "结果集过大",
  llm_unavailable: "推理服务不可用",
  clarification_required: "问题需要进一步澄清",
  run_cancelled: "已取消",
  run_interrupted: "执行进程中断",
  run_failed: "执行失败",
};

export const zoneTargetLabels: Record<string, string> = {
  table: "数据表",
  schema: "Schema",
  database: "数据库",
};

export const tagObjectLabels: Record<string, string> = {
  table: "数据表",
  column: "字段",
  term: "术语",
  metric: "指标",
  code_rule: "代码规则",
};

export const businessRuleKindLabels: Record<string, string> = {
  statistical_scope: "统计范围",
  time_semantics: "时间语义",
  unit: "单位",
  filter: "业务过滤",
  soft_delete: "软删除",
  status: "状态条件",
};

export const sensitiveCategoryLabels: Record<string, string> = {
  mobile: "手机号",
  id_card: "身份证",
  bank_card: "银行卡",
  email: "邮箱",
  address: "地址",
  name: "姓名",
  amount: "金额",
  credential: "凭据",
  other: "其他",
};

export const samplingLabels: Record<string, string> = {
  allow: "允许采样",
  exclude: "禁止采样",
};

export const maskStrategyLabels: Record<string, string> = {
  keep_prefix: "保留前缀",
  keep_suffix: "保留后缀",
  hash: "哈希",
  full: "全部隐藏",
};

export const supplementKindLabels: Record<string, string> = {
  abbreviation: "缩写",
  alias: "别名",
  context: "上下文说明",
};

export const analysisActionLabels: Record<string, string> = {
  create: "新增",
  update: "更新",
  skip: "跳过",
  conflict: "冲突",
};

export const certificationLabels: Record<string, string> = {
  certified: "已认证口径",
  draft: "草稿口径",
  none: "无认证口径",
};

export function stepLabel(step: string) {
  return buildSteps[step] || step || "等待中";
}

export function statusLabel(status: string) {
  return buildStatus[status] || versionStatus[status] || status;
}

export function runStepLabel(step: string) {
  return runSteps[step] || step || "处理中";
}

export function runStatusLabel(status: string) {
  return runStatus[status] || status;
}

export function runErrorLabel(code: string | null | undefined) {
  return code ? runErrorLabels[code] || code : "";
}

export const ruleKindLabels: Record<string, string> = {
  table_mapping: "表映射",
  column_mapping: "字段映射",
  join: "关联关系",
  status_filter: "状态条件",
  soft_delete: "软删除",
  amount_unit: "金额单位",
  time_field: "时间口径",
  metric: "指标计算",
  enum_value: "枚举值",
  filter: "过滤条件",
  sql_fragment: "SQL 片段",
};

export const parseStateLabels: Record<string, string> = {
  static: "静态可解析",
  dynamic: "需人工确认",
};

export const conflictStateLabels: Record<string, string> = {
  none: "—",
  pending: "待处理",
  resolved: "已处理",
};

export const sourceOriginLabels: Record<string, string> = {
  release: "发布版本关联",
  manual: "手动选择的参考版本",
  reference: "参考快照",
  none: "未关联源码",
};

export function isTerminalStatus(status: string) {
  return ["completed", "needs_clarification", "failed", "cancelled", "interrupted"].includes(
    status,
  );
}
