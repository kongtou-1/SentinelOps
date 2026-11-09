# API 请求与响应数据结构

问数会话删除补充（2026-11-07）：`DELETE /api/v1/askdata/conversations/{identifier}` 不需要请求体，成功为 204 空响应；重复删除或非所有者访问返回 404，无运行权限返回 403。会话采用软删除，未结束的运行转为 `cancelled`／`run_cancelled`，已完成结果及已保存分析保留，原会话及其运行的读取、事件和导出入口不可再访问。

2026-11-06 智能问数阶段 1：`AskDataConnectionResponse` 复用 DatabaseResponse 并增加 `askdata_enabled/askdata_version`；`AskDataContextResponse` 返回连接、可选应用、环境、结构与配置版本；`AskDataConfigurationInput` 使用 `enabled/expected_version` 进行乐观并发控制。结构缓存的表对象增加 `primary_key` 和 `foreign_keys`，后者按顺序保存源字段、目标表和目标字段；旧缓存允许缺失，刷新后补齐。完整契约见 OpenAPI。

2026-10-16 服务可观测性增量：增加 `MonitorInput/MonitorResponse/ObservabilityResponse/TrafficInput`；服务器增加 `blackbox_exporter_target`，应用增加 `traffic_target/traffic_config_changed_at`，规则与告警增加 `service_monitor_id`，事故详情增加 `service_monitors`。容器观测增加挂载库存和 CPU/内存限制指标。字段语义、版本隔离、加密配置及指标清单见[服务可观测性实施与验收](服务可观测性实施与验收.md#api-与数据)，全部字段及约束以 [OpenAPI](openapi.json) 为准。

2026-10-16：`RuleInput.auto_diagnose` 为可空布尔值，默认 null（跟随全局），true 自动、false 手动；`RuleResponse` 返回同字段。`AnalysisResponse.trigger` 为 `manual | automatic`。新增 `DiagnosisPolicyInput {enabled: bool=false}` 和 `DiagnosisPolicyResponse {enabled, configured_by: UUID|null, updated_at: datetime|null, ai_available: bool}`，完整行为见[自动 AI 诊断](自动AI诊断.md)。

整理日期：2026-10-05。与 [API 接口汇总](API接口汇总.md) 和 [OpenAPI 快照](openapi.json) 配套。字段表来自 Pydantic/OpenAPI；跨字段校验和状态限制见接口汇总的业务说明。前端类型由该 OpenAPI 生成到 `frontend/src/api/schema.d.ts`。诊断工作台新增结构见下表。

2026-10-15：报告详情与报告列表摘要新增 `resource_context`：`services` 为服务的 `id/name`，`resources` 为资源的 `id/name/kind`（`server/container/llm_endpoint`）；`source=frozen` 表示生成时冻结，`current_assets` 表示旧报告按冻结证据中的目标与现有资产关联展示，不修改原快照。一次事故可涉及多个服务；主机告警不自动归属于同机服务，无法关联时 `services=[]`。

| 结构 | 字段 |
| --- | --- |
| ConversationInput | `title`，默认“事故诊断”，最多 200 字符 |
| MessageInput | 必填 UUID `client_message_id`，必填 `content`（1–16000 字符，不能全空白） |
| ConversationResponse | `id/incident_id/owner_id` UUID，`owner_name/title/last_seq`，`current_run`（RunResponse 或 null），UTC `created_at/updated_at` |
| MessageResponse | `id/conversation_id/run_id`，`role/content/status`，可空 `client_message_id/sdk_message_id`，UTC `created_at/updated_at` |
| RunResponse | `id/conversation_id/incident_id/requested_by/status`，`config/session_id/source_context`，开始/结束时间、`error_code`，`model_requests/tool_count`，`evidence/knowledge/tool_calls`，`candidate`（Diagnosis 或空字典），可空 `adopted_analysis_id`，UTC `created_at/updated_at` |
| EventResponse | `id/conversation_id`，可空 `run_id`，递增 `seq`、`kind`、脱敏 `data`，UTC `created_at/updated_at` |

运行状态为 `queued/running/cancelling/completed/failed/cancelled/interrupted`。消息状态及所有权约束见 [工作台说明](../frontend/AI诊断工作台.md)。`candidate` 现引用 OpenAPI 的 Diagnosis，正式 `AnalysisResponse.result` 仍保持原兼容结构。

“必填”表示 JSON 中要求存在该字段，与“可为 null”独立；类型含 null 的必填响应字段仍会出现。默认值是输入省略时的模型值，不代表客户端可以省略所有响应字段。字典/list 的内部业务内容未由响应模型约束时，标为 object/数组；不会推断成固定字段。

除 OpenAPI 中的模型外，补充 Diagnosis/Cause/Proposal/ProposalParameters：它们描述诊断 result 的业务校验结构，未作为 AnalysisResponse.result 的 OpenAPI 字段声明。

## 模型目录

- [AcknowledgeInput](#acknowledgeinput)
- [AlertResponse](#alertresponse)
- [AnalysisResponse](#analysisresponse)
- [AnalyzeInput](#analyzeinput)
- [ApprovalInput](#approvalinput)
- [ApprovalResponse](#approvalresponse)
- [AssigneeResponse](#assigneeresponse)
- [AuditResponse](#auditresponse)
- [Cause](#cause)
- [CommentInput](#commentinput)
- [ConsoleItem](#consoleitem)
- [ContainerResponse](#containerresponse)
- [CredentialInput](#credentialinput)
- [CredentialResponse](#credentialresponse)
- [DashboardSummary](#dashboardsummary)
- [DecisionInput](#decisioninput)
- [DeliveryResponse](#deliveryresponse)
- [Diagnosis](#diagnosis)
- [DocumentInput](#documentinput)
- [DocumentResponse](#documentresponse)
- [EditReport](#editreport)
- [EndpointCreate](#endpointcreate)
- [EndpointInput](#endpointinput)
- [EndpointResponse](#endpointresponse)
- [ExecuteInput](#executeinput)
- [ExecutionResponse](#executionresponse)
- [GenerateReport](#generatereport)
- [HTTPValidationError](#httpvalidationerror)
- [Improvement](#improvement)
- [IncidentDetail](#incidentdetail)
- [IncidentResponse](#incidentresponse)
- [IncidentUpdate](#incidentupdate)
- [LoginRequest](#loginrequest)
- [MeResponse](#meresponse)
- [MergeInput](#mergeinput)
- [LLMStatusResponse](#llmstatusresponse)
- [LLMStatusBucket](#llmstatusbucket)
- [ProbeResult](#proberesult)
- [Proposal](#proposal)
- [ProposalParameters](#proposalparameters)
- [RefreshRequest](#refreshrequest)
- [ReportCause](#reportcause)
- [ReportContent](#reportcontent)
- [ReportResponse](#reportresponse)
- [ResultResponse](#resultresponse)
- [ReviewReport](#reviewreport)
- [RevisionResponse](#revisionresponse)
- [RoleResponse](#roleresponse)
- [RuleInput](#ruleinput)
- [RuleResponse](#ruleresponse)
- [SendReport](#sendreport)
- [ServerInput](#serverinput)
- [ServerResponse](#serverresponse)
- [SkillInput](#skillinput)
- [SkillResponse](#skillresponse)
- [StatusInput](#statusinput)
- [TaskInput](#taskinput)
- [TaskResponse](#taskresponse)
- [TimelineResponse](#timelineresponse)
- [TokenResponse](#tokenresponse)
- [UserCreate](#usercreate)
- [UserResponse](#userresponse)
- [UserUpdate](#userupdate)
- [ValidationError](#validationerror)
- [WebhookAlert](#webhookalert)
- [WebhookPayload](#webhookpayload)

## AcknowledgeInput

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `comment` | 是 | string | 最短 `1`；最长 `2000` |

## AlertResponse

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `id` | 是 | string (uuid) | — |
| `incident_id` | 是 | string (uuid) / null | — |
| `fingerprint` | 是 | string | — |
| `rule_id` | 是 | string (uuid) / null | — |
| `server_id` | 是 | string (uuid) / null | — |
| `llm_endpoint_id` | 是 | string (uuid) / null | — |
| `prediction_task_id` | 是 | string (uuid) / null | — |
| `severity` | 是 | string | — |
| `status` | 是 | string | — |
| `starts_at` | 是 | string (date-time) | — |
| `ends_at` | 是 | string (date-time) / null | — |
| `labels` | 是 | object | — |
| `annotations` | 是 | object | — |
| `rule_snapshot` | 是 | object | — |
| `recovery_status` | 是 | string | — |
| `recovery_reason` | 是 | string / null | — |
| `recovery_checked_at` | 是 | string (date-time) / null | — |
| `recovery_evidence_at` | 是 | string (date-time) / null | — |
| `acknowledged_at` | 是 | string (date-time) / null | — |
| `acknowledged_by` | 是 | string (uuid) / null | — |
| `comment` | 是 | string / null | — |
| `created_at` | 是 | string (date-time) | — |
| `updated_at` | 是 | string (date-time) | — |

## AnalysisResponse

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `id` | 是 | string (uuid) | — |
| `incident_id` | 是 | string (uuid) | — |
| `requested_by` | 是 | string (uuid) | — |
| `status` | 是 | string | — |
| `config` | 是 | object | — |
| `session_id` | 是 | string / null | — |
| `started_at` | 是 | string (date-time) / null | — |
| `finished_at` | 是 | string (date-time) / null | — |
| `error_code` | 是 | string / null | — |
| `evidence` | 是 | 数组（未声明结构） | — |
| `knowledge` | 是 | 数组（未声明结构） | — |
| `tool_calls` | 是 | 数组（未声明结构） | — |
| `result` | 是 | object | — |
| `created_at` | 是 | string (date-time) | — |
| `updated_at` | 是 | string (date-time) | — |

## AnalyzeInput

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `force` | 否 | boolean | 默认 `false` |

## ApprovalInput

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `analysis_id` | 是 | string (uuid) | — |

## ApprovalResponse

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `id` | 是 | string (uuid) | — |
| `incident_id` | 是 | string (uuid) | — |
| `analysis_id` | 是 | string (uuid) | — |
| `container_id` | 是 | string (uuid) | — |
| `requested_by` | 是 | string (uuid) | — |
| `approver_id` | 是 | string (uuid) / null | — |
| `status` | 是 | string | — |
| `binding` | 是 | object | — |
| `binding_hash` | 是 | string | — |
| `expires_at` | 是 | string (date-time) | — |
| `decided_at` | 是 | string (date-time) / null | — |
| `decision_comment` | 是 | string / null | — |
| `error_code` | 是 | string / null | — |
| `created_at` | 是 | string (date-time) | — |
| `updated_at` | 是 | string (date-time) | — |

## AssigneeResponse

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `id` | 是 | string (uuid) | — |
| `username` | 是 | string | — |
| `role` | 是 | `"operator"`、`"admin"` | — |

## AuditResponse

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `id` | 是 | string (uuid) | — |
| `user_id` | 是 | string (uuid) / null | — |
| `actor_username` | 否 | string / null | 关联账号的当前用户名；系统任务为空 |
| `incident_id` | 是 | string (uuid) | — |
| `incident_title` | 否 | string / null | 关联事故的当前标题，保留软删除对象的可读信息 |
| `approval_id` | 是 | string (uuid) / null | — |
| `report_id` | 是 | string (uuid) / null | — |
| `execution_id` | 是 | string (uuid) / null | — |
| `action` | 是 | string | — |
| `request_id` | 是 | string | — |
| `ip` | 是 | string / null | — |
| `detail` | 是 | object | — |
| `created_at` | 是 | string (date-time) | — |
| `updated_at` | 是 | string (date-time) | — |

## Cause

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `description` | 是 | string | 最短 `1`；最长 `2000` |
| `evidence_ids` | 是 | 数组（string） | 最少项 `1`；最多项 `30` |

## CommentInput

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `comment` | 是 | string | 最短 `1`；最长 `2000`；正则 `"\\S"` |

## ConsoleItem

审批、执行和报告全局列表共用的摘要；详情字段继续通过各自详情接口获取。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `id` | 是 | string (uuid) | 记录 ID |
| `incident_id` | 是 | string (uuid) | 关联事故 |
| `incident_title` | 是 | string | 事故标题 |
| `severity` | 是 | string | 事故级别 |
| `status` | 是 | string | 对应业务状态；审批过期投影为 expired，不修改审批记录 |
| `actor_id` | 是 | string (uuid) | 申请人、执行发起人或报告生成人 |
| `actor_username` | 是 | string | 发起人用户名 |
| `created_at` | 是 | string (date-time) | 创建时间 |
| `finished_at` | 否 | string (date-time) / null | 默认 null；执行/报告完成时间 |
| `expires_at` | 否 | string (date-time) / null | 默认 null；审批有效期 |
| `container_id` | 否 | string (uuid) / null | 默认 null；审批/执行目标 |
| `container_name` | 否 | string / null | 默认 null |
| `action` | 否 | string / null | 默认 null；Skill 名称和版本 |
| `revision` | 否 | integer / null | 默认 null；报告修订号 |
| `error_code` | 否 | string / null | 默认 null；执行/报告错误原因 |

## ContainerResponse

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `id` | 是 | string (uuid) | — |
| `server_id` | 是 | string (uuid) | — |
| `docker_id` | 是 | string | — |
| `name` | 是 | string | — |
| `image` | 是 | string | — |
| `status` | 是 | string | — |
| `labels` | 是 | object | — |
| `last_seen_at` | 是 | string (date-time) | — |
| `created_at` | 是 | string (date-time) | — |
| `updated_at` | 是 | string (date-time) | — |

## CredentialInput

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `name` | 是 | string | 最短 `1`；最长 `100` |
| `auth_type` | 是 | `"password"`、`"private_key"` | — |
| `secret` | 是 | string (password) | 仅请求写入 |
| `passphrase` | 否 | string (password) / null | 仅请求写入 |

## CredentialResponse

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `id` | 是 | string (uuid) | — |
| `name` | 是 | string | — |
| `auth_type` | 是 | string | — |
| `created_at` | 是 | string (date-time) | — |
| `updated_at` | 是 | string (date-time) | — |

## DashboardSummary

普通查询排除软删除记录。容器状态来自最近同步清单，`containers_last_seen_at` 为空表示尚无采样；监控时间序列缺失不由这些计数补零。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `servers_total` | 是 | integer | 当前服务器总数 |
| `servers_monitored` | 是 | integer | 已启用监控的服务器数 |
| `containers_total` | 是 | integer | 容器清单总数 |
| `containers_by_status` | 是 | object: integer | 按最近同步状态计数 |
| `containers_last_seen_at` | 是 | string (date-time) / null | 最近清单采样时间 |
| `llm_total` | 是 | integer | LLM 服务总数 |
| `llm_by_status` | 是 | object: integer | 实际探测状态计数，包括 disabled、unknown、stale |
| `approvals_pending` | 是 | integer | 有效期内 pending 数；过期待审批不计入 |
| `alerts_by_status` | 是 | object: integer | 触发状态计数 |
| `alerts_unacknowledged` | 是 | integer | 触发中且未经人工确认的告警数 |
| `incidents_by_status` | 是 | object: integer | 后端事故状态计数 |

## DecisionInput

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `comment` | 是 | string | 最短 `1`；最长 `2000`；正则 `"\\S"` |

## DeliveryResponse

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `id` | 是 | string (uuid) | — |
| `report_id` | 是 | string (uuid) | — |
| `revision` | 是 | integer | — |
| `requested_by` | 是 | string (uuid) | — |
| `idempotency_key` | 是 | string (uuid) | — |
| `recipients` | 是 | 数组（string） | — |
| `sender` | 是 | string | — |
| `subject` | 是 | string | — |
| `comment` | 是 | string | — |
| `attachment_hash` | 是 | string | — |
| `status` | 是 | string | — |
| `dispatched_at` | 是 | string (date-time) / null | — |
| `finished_at` | 是 | string (date-time) / null | — |
| `error_code` | 是 | string / null | — |
| `created_at` | 是 | string (date-time) | — |
| `updated_at` | 是 | string (date-time) | — |

## Diagnosis

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `summary` | 是 | string | 最短 `1`；最长 `2000` |
| `possible_causes` | 是 | 数组（[Cause](#cause)） | 最多项 `10` |
| `impact` | 是 | string | 最短 `1`；最长 `2000` |
| `evidence_ids` | 是 | 数组（string） | 最少项 `1`；最多项 `30` |
| `knowledge_ids` | 是 | 数组（string） | 最多项 `10` |
| `suggested_checks` | 是 | 数组（string） | 最多项 `10` |
| `data_gaps` | 是 | 数组（string） | 最多项 `30` |
| `proposal` | 否 | [Proposal](#proposal) / null | 默认 `null` |
| `requires_human_approval` | 否 | 固定 `true` | 默认 `true` |

## DocumentInput

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `title` | 是 | string | 最短 `1`；最长 `200`；正则 `"\\S"` |
| `content` | 是 | string | 最短 `1`；最长 `100000`；正则 `"\\S"` |
| `source` | 否 | string | 最长 `500`；默认 `""` |
| `tags` | 否 | 数组（string） | 最多项 `20`；默认 `[]`；每项：最短 `1`；最长 `50` |
| `enabled` | 否 | boolean | 默认 `true` |

## DocumentResponse

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `id` | 是 | string (uuid) | — |
| `title` | 是 | string | — |
| `content` | 是 | string | — |
| `source` | 是 | string | — |
| `tags` | 是 | 数组（string） | — |
| `enabled` | 是 | boolean | — |
| `version` | 是 | integer | — |
| `index_status` | 是 | string | — |
| `error_code` | 是 | string / null | — |
| `created_at` | 是 | string (date-time) | — |
| `updated_at` | 是 | string (date-time) | — |

## EditReport

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `expected_revision` | 是 | integer | 最小值 `1.0` |
| `content` | 是 | [ReportContent](#reportcontent) | — |

## EndpointCreate

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `name` | 是 | string | 最短 `1`；最长 `100` |
| `provider` | 否 | 固定 `"deepseek"` | 默认 `"deepseek"` |
| `base_url` | 是 | string | 最短 `1`；最长 `2000` |
| `model` | 是 | string | 最短 `1`；最长 `100`；正则 `"^\\S+$"` |
| `probe_enabled` | 否 | boolean | 默认 `true` |
| `probe_interval_seconds` | 否 | integer | 最小值 `60.0`；最大值 `86400.0`；默认 `60` |
| `api_key` | 条件 | string (password) / null | 与 `agent_channel_id` 二选一，仅请求写入 |
| `agent_channel_id` | 条件 | string (uuid) / null | 引用已启用的 Agent 模型通道；API 基础地址须与通道一致 |

## EndpointInput

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `name` | 是 | string | 最短 `1`；最长 `100` |
| `provider` | 否 | 固定 `"deepseek"` | 默认 `"deepseek"` |
| `base_url` | 是 | string | 最短 `1`；最长 `2000` |
| `model` | 是 | string | 最短 `1`；最长 `100`；正则 `"^\\S+$"` |
| `probe_enabled` | 否 | boolean | 默认 `true` |
| `probe_interval_seconds` | 否 | integer | 最小值 `60.0`；最大值 `86400.0`；默认 `60` |
| `api_key` | 否 | string (password) / null | 仅请求写入 |
| `agent_channel_id` | 否 | string (uuid) / null | 与非空 `api_key` 互斥。两者均省略则保留原 Key；解除关联时须提交独立 Key |

## EndpointResponse

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `id` | 是 | string (uuid) | — |
| `name` | 是 | string | — |
| `provider` | 是 | string | — |
| `base_url` | 是 | string | — |
| `model` | 是 | string | — |
| `agent_channel_id` | 是 | string (uuid) / null | 关联通道 ID；独立 Key 时为 null |
| `probe_enabled` | 是 | boolean | — |
| `probe_interval_seconds` | 是 | integer | — |
| `last_probe_at` | 是 | string (date-time) / null | — |
| `last_probe_success` | 是 | boolean / null | — |
| `last_error_type` | 是 | string / null | — |
| `last_http_status` | 是 | integer / null | — |
| `last_latency_ms` | 是 | number / null | — |
| `status` | 是 | string | — |
| `created_at` | 是 | string (date-time) | — |
| `updated_at` | 是 | string (date-time) | — |

## ExecuteInput

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |

## ExecutionResponse

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `id` | 是 | string (uuid) | — |
| `approval_id` | 是 | string (uuid) | — |
| `incident_id` | 是 | string (uuid) | — |
| `container_id` | 是 | string (uuid) | — |
| `initiated_by` | 是 | string (uuid) | — |
| `status` | 是 | string | — |
| `binding_hash` | 是 | string | — |
| `started_at` | 是 | string (date-time) / null | — |
| `dispatched_at` | 是 | string (date-time) / null | — |
| `finished_at` | 是 | string (date-time) / null | — |
| `precheck` | 是 | object | — |
| `output` | 是 | object | — |
| `verification` | 是 | object | — |
| `error_code` | 是 | string / null | — |
| `created_at` | 是 | string (date-time) | — |
| `updated_at` | 是 | string (date-time) | — |

## GenerateReport

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `force` | 否 | boolean | 默认 `false` |

## HTTPValidationError

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `detail` | 否 | 数组（[ValidationError](#validationerror)） | — |

OpenAPI 默认校验模型；本项目请求校验错误实际仅保留 loc/msg/type，详见接口汇总。

## Improvement

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `action` | 是 | string | 最短 `1`；最长 `4000`；正则 `"\\S"` |
| `owner` | 否 | string | 最长 `200`；默认 `"待分配"` |
| `deadline` | 否 | string | 最长 `100`；默认 `"待确定"` |

## IncidentDetail

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `id` | 是 | string (uuid) | — |
| `title` | 是 | string | — |
| `severity` | 是 | `"warning"`、`"critical"` | — |
| `status` | 是 | `"open"`、`"in_progress"`、`"recovered"`、`"closed"`、`"merged"` | — |
| `assignee_id` | 是 | string (uuid) / null | — |
| `started_at` | 是 | string (date-time) | — |
| `last_alert_started_at` | 是 | string (date-time) | — |
| `recovered_at` | 是 | string (date-time) / null | — |
| `closed_at` | 是 | string (date-time) / null | — |
| `merged_into_id` | 是 | string (uuid) / null | — |
| `created_at` | 是 | string (date-time) | — |
| `updated_at` | 是 | string (date-time) | — |
| `server_ids` | 是 | 数组（string (uuid)） | — |
| `llm_endpoint_ids` | 是 | 数组（string (uuid)） | — |
| `alert_count` | 是 | integer | — |
| `firing_alert_count` | 是 | integer | — |
| `recovery_pending_alert_count` | 是 | integer | — |
| `recovery_terminated_alert_count` | 是 | integer | — |
| `recovery_verified_alert_count` | 是 | integer | — |
| `timeline` | 是 | 数组（[TimelineResponse](#timelineresponse)） | — |

## IncidentResponse

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `id` | 是 | string (uuid) | — |
| `title` | 是 | string | — |
| `severity` | 是 | `"warning"`、`"critical"` | — |
| `status` | 是 | `"open"`、`"in_progress"`、`"recovered"`、`"closed"`、`"merged"` | — |
| `assignee_id` | 是 | string (uuid) / null | — |
| `started_at` | 是 | string (date-time) | — |
| `last_alert_started_at` | 是 | string (date-time) | — |
| `recovered_at` | 是 | string (date-time) / null | — |
| `closed_at` | 是 | string (date-time) / null | — |
| `closure` | 否 | object | 历史记录默认 `{}`；关闭后含 forced、reason、actor_id、actor_username、closed_at、recovery_verified 及关联告警快照 alerts |
| `merged_into_id` | 是 | string (uuid) / null | — |
| `created_at` | 是 | string (date-time) | — |
| `updated_at` | 是 | string (date-time) | — |

## IncidentUpdate

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `title` | 否 | string / null | 最短 `1`；最长 `200`；正则 `"\\S"` |
| `assignee_id` | 否 | string (uuid) / null | — |

## LoginRequest

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `username` | 是 | string | 最短 `3`；最长 `64`；正则 `"^[a-z0-9_.-]+$"` |
| `password` | 是 | string (password) | 仅请求写入 |

## MeResponse

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `id` | 是 | string (uuid) | — |
| `username` | 是 | string | — |
| `role` | 是 | `"admin"`、`"operator"`、`"viewer"` | — |
| `is_active` | 是 | boolean | — |
| `last_login_at` | 是 | string (date-time) / null | — |
| `created_at` | 是 | string (date-time) | — |
| `updated_at` | 是 | string (date-time) | — |
| `permissions` | 是 | 数组（string） | — |

## MergeInput

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `comment` | 是 | string | 最短 `1`；最长 `2000`；正则 `"\\S"` |
| `target_incident_id` | 是 | string (uuid) | — |

## LLMStatusResponse

| 字段 | 必填 | 类型 / 枚举 | 说明 |
| --- | --- | --- | --- |
| `endpoint_id` | 是 | string (uuid) | 当前端点 |
| `source` | 否 | 固定 `"active_probe"` | 默认主动探测 |
| `status` | 是 | string | 当前探测状态，独立于窗口成功率 |
| `start`、`end` | 是 | string (date-time) | 近 30 分钟半开窗口 |
| `updated_at` | 是 | string (date-time) | 本次统计时间 |
| `last_request_at` | 是 | string (date-time) / null | 窗口内最近探测完成时间 |
| `request_count`、`success_count` | 是 | integer | 完成探测次数、成功次数 |
| `success_rate` | 是 | number / null | 成功数除以总数，范围 0–1；无记录为空 |
| `buckets` | 是 | [LLMStatusBucket](#llmstatusbucket)[] | 按时间升序排列的 60 个区间 |

## LLMStatusBucket

| 字段 | 必填 | 类型 / 枚举 | 说明 |
| --- | --- | --- | --- |
| `start`、`end` | 是 | string (date-time) | 30 秒半开区间 |
| `request_count`、`success_count` | 是 | integer | 区间内完成探测次数、成功次数；无记录均为 0 |

## ProbeResult

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `endpoint_id` | 是 | string (uuid) | — |
| `checked_at` | 是 | string (date-time) | — |
| `success` | 是 | boolean | — |
| `error_type` | 是 | string / null | — |
| `http_status` | 是 | integer / null | — |
| `latency_ms` | 是 | number | — |
| `input_tokens` | 否 | integer / null | 最小值 `0.0` |
| `output_tokens` | 否 | integer / null | 最小值 `0.0` |

## Proposal

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `skill` | 是 | 固定 `"restart_container"` | — |
| `version` | 否 | 固定 `"1"` | 默认 `"1"` |
| `parameters` | 是 | [ProposalParameters](#proposalparameters) | — |
| `risk` | 否 | 固定 `"medium"` | 默认 `"medium"` |
| `rationale` | 是 | string | 最短 `1`；最长 `2000` |
| `impact` | 是 | string | 最短 `1`；最长 `2000` |
| `executable` | 否 | 固定 `false` | 默认 `false` |
| `requires_human_approval` | 否 | 固定 `true` | 默认 `true` |

## ProposalParameters

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `container_id` | 是 | string (uuid) | — |

## RefreshRequest

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `refresh_token` | 是 | string (password) | 仅请求写入 |

## ReportCause

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `description` | 是 | string | 最短 `1`；最长 `4000`；正则 `"\\S"` |
| `confidence` | 是 | `"confirmed"`、`"suspected"`、`"unknown"` | — |
| `evidence_ids` | 是 | 数组（string） | 最多项 `100` |

## ReportContent

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `summary` | 是 | string | 最短 `1`；最长 `4000`；正则 `"\\S"` |
| `impact` | 是 | string | 最短 `1`；最长 `4000`；正则 `"\\S"` |
| `causes` | 是 | 数组（[ReportCause](#reportcause)） | 最多项 `20` |
| `response_and_recovery` | 是 | string | 最短 `1`；最长 `4000`；正则 `"\\S"` |
| `lessons` | 是 | 数组（string） | 最多项 `20`；每项：最短 `1`；最长 `4000`；正则 `"\\S"` |
| `improvements` | 是 | 数组（[Improvement](#improvement)） | 最多项 `30` |
| `data_gaps` | 是 | 数组（string） | 最多项 `30`；每项：最短 `1`；最长 `4000`；正则 `"\\S"` |
| `evidence_ids` | 是 | 数组（string） | 最多项 `100` |

## ReportResponse

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `id` | 是 | string (uuid) | — |
| `incident_id` | 是 | string (uuid) | — |
| `requested_by` | 是 | string (uuid) | — |
| `status` | 是 | string | — |
| `revision` | 是 | integer | — |
| `snapshot` | 是 | object | — |
| `snapshot_hash` | 是 | string | — |
| `config` | 是 | object | — |
| `tool_calls` | 是 | 数组（未声明结构） | — |
| `content` | 否 | object / null | — |
| `started_at` | 是 | string (date-time) / null | — |
| `finished_at` | 是 | string (date-time) / null | — |
| `error_code` | 是 | string / null | — |
| `reviewed_by` | 是 | string (uuid) / null | — |
| `reviewed_at` | 是 | string (date-time) / null | — |
| `review_comment` | 是 | string / null | — |
| `created_at` | 是 | string (date-time) | — |
| `updated_at` | 是 | string (date-time) | — |

## ResultResponse

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `id` | 是 | string (uuid) | — |
| `task_id` | 是 | string (uuid) | — |
| `status` | 是 | string | — |
| `trigger` | 是 | string | — |
| `started_at` | 是 | string (date-time) / null | — |
| `finished_at` | 是 | string (date-time) / null | — |
| `data_end_at` | 是 | string (date-time) / null | — |
| `error_code` | 是 | string / null | — |
| `config` | 是 | object | — |
| `quality` | 是 | object | — |
| `horizons` | 是 | object | — |
| `anomalies` | 是 | 数组（未声明结构） | — |
| `created_at` | 是 | string (date-time) | — |
| `updated_at` | 是 | string (date-time) | — |
| `fresh` | 否 | boolean | 默认 `false` |

## ReviewReport

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `expected_revision` | 是 | integer | 最小值 `1.0` |
| `decision` | 是 | `"approve"`、`"request_changes"` | — |
| `comment` | 是 | string | 最短 `1`；最长 `2000`；正则 `"\\S"` |

## RevisionResponse

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `id` | 是 | string (uuid) | — |
| `report_id` | 是 | string (uuid) | — |
| `revision` | 是 | integer | — |
| `author_id` | 是 | string (uuid) / null | — |
| `content` | 是 | object | — |
| `content_hash` | 是 | string | — |
| `created_at` | 是 | string (date-time) | — |
| `updated_at` | 是 | string (date-time) | — |

## RoleResponse

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `code` | 是 | `"admin"`、`"operator"`、`"viewer"` | — |
| `name` | 是 | string | — |
| `permissions` | 是 | 数组（string） | — |

## RuleInput

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `code` | 是 | string | 最短 `1`；最长 `100`；正则 `"^[a-zA-Z_][a-zA-Z0-9_]*$"` |
| `server_id` | 否 | string (uuid) / null | — |
| `llm_endpoint_id` | 否 | string (uuid) / null | — |
| `prediction_task_id` | 否 | string (uuid) / null | — |
| `prediction_horizon_hours` | 否 | `1`、`6`、`24` / null | — |
| `metric` | 是 | `"host_cpu_percent"`、`"host_memory_percent"`、`"host_disk_percent"`、`"host_load1"`、`"host_iowait_percent"`、`"host_memory_available_bytes"`、`"host_swap_percent"`、`"host_disk_available_bytes"`、`"host_inode_percent"`、`"host_disk_read_bytes"`、`"host_disk_write_bytes"`、`"host_disk_io_wait_seconds"`、`"host_network_receive_errors"`、`"host_network_transmit_errors"`、`"host_network_receive_drops"`、`"host_network_transmit_drops"`、`"http_latency_seconds"`、`"container_restarts_5m"`、`"container_oom_5m"`、`"host_network_receive_bytes"`、`"host_network_transmit_bytes"`、`"container_cpu_percent"`、`"container_memory_bytes"`、`"container_network_receive_bytes"`、`"container_network_transmit_bytes"`、`"target_up"`、`"http_up"`、`"container_up"` / `"llm_up"`、`"llm_latency_seconds"`、`"llm_authentication_failed"`、`"llm_insufficient_balance"`、`"llm_rate_limited"`、`"llm_service_error"`、`"llm_probe_stale"`、`"prediction_risk"`、`"prediction_anomaly"` | — |
| `threshold` | 是 | number | 最小值 `0.0` |
| `for_seconds` | 否 | integer | 最小值 `0.0`；最大值 `86400.0`；默认 `30` |
| `severity` | 否 | `"warning"`、`"critical"` | 默认 `"warning"` |
| `enabled` | 否 | boolean | 默认 `true` |

## RuleResponse

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `id` | 是 | string (uuid) | — |
| `code` | 是 | string | — |
| `server_id` | 是 | string (uuid) / null | — |
| `llm_endpoint_id` | 是 | string (uuid) / null | — |
| `prediction_task_id` | 是 | string (uuid) / null | — |
| `prediction_horizon_hours` | 是 | integer / null | — |
| `metric` | 是 | string | — |
| `threshold` | 是 | number | — |
| `for_seconds` | 是 | integer | — |
| `severity` | 是 | string | — |
| `enabled` | 是 | boolean | — |
| `created_at` | 是 | string (date-time) | — |
| `updated_at` | 是 | string (date-time) | — |

## SendReport

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `expected_revision` | 是 | integer | 最小值 `1.0` |
| `recipients` | 是 | 数组（string） | 最少项 `1`；最多项 `20` |
| `comment` | 是 | string | 最短 `1`；最长 `2000`；正则 `"\\S"` |
| `idempotency_key` | 是 | string (uuid) | — |

## ServerInput

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `name` | 是 | string | 最短 `1`；最长 `100` |
| `host` | 是 | string | 最长 `253`；正则 `"^[a-zA-Z0-9][a-zA-Z0-9.\\-]*$"` |
| `ssh_port` | 否 | integer | 最小值 `1.0`；最大值 `65535.0`；默认 `22` |
| `ssh_user` | 否 | string | 最长 `64`；正则 `"^[a-zA-Z0-9_.\\-]+$"`；默认 `"ops"` |
| `credential_id` | 否 | string (uuid) / null | — |
| `ssh_host_key` | 否 | string / null | 最长 `8192` |
| `environment` | 否 | string | 最短 `1`；最长 `50`；默认 `"test"` |
| `node_exporter_target` | 是 | string | 正则 `"^[a-zA-Z0-9][a-zA-Z0-9.\\-]*:[0-9]{1,5}$"` |
| `cadvisor_target` | 是 | string | 正则 `"^[a-zA-Z0-9][a-zA-Z0-9.\\-]*:[0-9]{1,5}$"` |
| `docker_state_target` | 否 | string / null | 正则 `"^[a-zA-Z0-9][a-zA-Z0-9.\\-]*:[0-9]{1,5}$"` |
| `health_url` | 否 | string / null | 最长 `2000` |
| `monitoring_enabled` | 否 | boolean | 默认 `true` |

## ServerResponse

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `id` | 是 | string (uuid) | — |
| `name` | 是 | string | — |
| `host` | 是 | string | — |
| `ssh_port` | 是 | integer | — |
| `ssh_user` | 是 | string | — |
| `credential_id` | 是 | string (uuid) / null | — |
| `ssh_host_key` | 是 | string / null | — |
| `environment` | 是 | string | — |
| `node_exporter_target` | 是 | string | — |
| `cadvisor_target` | 是 | string | — |
| `docker_state_target` | 是 | string / null | — |
| `collection_config_changed_at` | 是 | string (date-time) | — |
| `health_url` | 是 | string / null | — |
| `monitoring_enabled` | 是 | boolean | — |
| `last_ssh_test_at` | 是 | string (date-time) / null | — |
| `last_ssh_test_status` | 是 | string / null | — |
| `created_at` | 是 | string (date-time) | — |
| `updated_at` | 是 | string (date-time) | — |

## SkillInput

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `code` | 是 | string | 正则 `"^[a-z][a-z0-9_]{0,99}$"` |
| `title` | 是 | string | 最短 `1`；最长 `200`；正则 `"\\S"` |
| `content` | 是 | string | 最短 `1`；最长 `100000`；正则 `"\\S"` |
| `tags` | 否 | 数组（string） | 最多项 `20`；默认 `[]`；每项：最短 `1`；最长 `50` |
| `enabled` | 否 | boolean | 默认 `true` |

## SkillResponse

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `id` | 是 | string (uuid) | — |
| `code` | 是 | string | — |
| `title` | 是 | string | — |
| `content` | 是 | string | — |
| `tags` | 是 | 数组（string） | — |
| `enabled` | 是 | boolean | — |
| `version` | 是 | integer | — |
| `created_at` | 是 | string (date-time) | — |
| `updated_at` | 是 | string (date-time) | — |

## StatusInput

不接受未声明字段。

可选字段 `force: boolean = false`；true 仅用于关闭事故，要求 Admin 的 `incidents:force_close` 权限。关闭原因沿用必填 `comment`。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `status` | 是 | `"open"`、`"in_progress"`、`"closed"` | — |
| `comment` | 是 | string | 最短 `1`；最长 `2000`；正则 `"\\S"` |

## TaskInput

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `name` | 是 | string | 最短 `1`；最长 `100` |
| `server_id` | 否 | string (uuid) / null | — |
| `container_id` | 否 | string (uuid) / null | — |
| `llm_endpoint_id` | 否 | string (uuid) / null | — |
| `metric` | 是 | `"host_cpu_percent"`、`"host_memory_percent"`、`"host_disk_percent"`、`"host_network_receive_bytes"`、`"host_network_transmit_bytes"`、`"container_cpu_percent"`、`"container_memory_bytes"`、`"container_network_receive_bytes"`、`"container_network_transmit_bytes"`、`"llm_latency_avg_seconds"`、`"llm_latency_p95_seconds"`、`"llm_error_rate"` | — |
| `selector` | 否 | 字典（string → string） | — |
| `threshold` | 是 | number | 最小值 `0.0` |
| `enabled` | 否 | boolean | 默认 `false` |
| `interval_seconds` | 否 | integer | 最小值 `300.0`；最大值 `86400.0`；默认 `300` |

## TaskResponse

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `id` | 是 | string (uuid) | — |
| `name` | 是 | string | — |
| `server_id` | 是 | string (uuid) / null | — |
| `container_id` | 是 | string (uuid) / null | — |
| `llm_endpoint_id` | 是 | string (uuid) / null | — |
| `metric` | 是 | string | — |
| `selector` | 是 | object | — |
| `threshold` | 是 | number | — |
| `enabled` | 是 | boolean | — |
| `interval_seconds` | 是 | integer | — |
| `next_run_at` | 是 | string (date-time) | — |
| `created_at` | 是 | string (date-time) | — |
| `updated_at` | 是 | string (date-time) | — |
| `latest_result` | 否 | object / null | — |

## TimelineResponse

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `id` | 是 | string (uuid) | — |
| `incident_id` | 是 | string (uuid) | — |
| `event_type` | 是 | string | — |
| `occurred_at` | 是 | string (date-time) | — |
| `actor_type` | 是 | string | — |
| `actor_id` | 是 | string (uuid) / null | — |
| `content` | 是 | object | — |
| `created_at` | 是 | string (date-time) | — |
| `updated_at` | 是 | string (date-time) | — |

## TokenResponse

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `access_token` | 是 | string | — |
| `refresh_token` | 是 | string | — |
| `token_type` | 是 | string | — |
| `expires_in` | 是 | integer | — |

## UserCreate

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `username` | 是 | string | 最短 `3`；最长 `64`；正则 `"^[a-z0-9_.-]+$"` |
| `password` | 是 | string (password) | 仅请求写入 |
| `role` | 否 | `"admin"`、`"operator"`、`"viewer"` | 默认 `"viewer"` |

## UserResponse

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `id` | 是 | string (uuid) | — |
| `username` | 是 | string | — |
| `role` | 是 | `"admin"`、`"operator"`、`"viewer"` | — |
| `is_active` | 是 | boolean | — |
| `last_login_at` | 是 | string (date-time) / null | — |
| `created_at` | 是 | string (date-time) | — |
| `updated_at` | 是 | string (date-time) | — |

## UserUpdate

不接受未声明字段。

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `role` | 否 | `"admin"`、`"operator"`、`"viewer"` / null | — |
| `is_active` | 否 | boolean / null | — |
| `password` | 否 | string (password) / null | 仅请求写入 |

## ValidationError

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `loc` | 是 | 数组（string / integer） | — |
| `msg` | 是 | string | — |
| `type` | 是 | string | — |
| `input` | 否 | 未声明结构 | — |
| `ctx` | 否 | object | — |

OpenAPI 默认校验模型；本项目请求校验错误实际仅保留 loc/msg/type，详见接口汇总。

## WebhookAlert

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `status` | 是 | `"firing"`、`"resolved"` | — |
| `labels` | 是 | 字典（string → string） | — |
| `annotations` | 否 | 字典（string → string） | — |
| `startsAt` | 是 | string (date-time) | — |
| `endsAt` | 是 | string (date-time) | — |
| `fingerprint` | 是 | string | 最短 `1`；最长 `64`；正则 `"^[a-fA-F0-9]+$"` |

## WebhookPayload

| 字段 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- |
| `version` | 否 | 固定 `"4"` | 默认 `"4"` |
| `alerts` | 是 | 数组（[WebhookAlert](#webhookalert)） | 最多项 `1000` |

## 监控增强首批（2026-10-10）

新增服务器批量摘要、容器健康和固定只读人工诊断 API，历史查询补充断点与可用性。完整字段、权限、状态语义及验收见 [监控增强说明](监控增强说明.md)，OpenAPI 与前端生成类型同步更新。

## 源码诊断第二阶段

源码连接新增 `svn_url`、`svn_revision`（HEAD 或数字）、`svn_username`、写入用 `svn_password` / `clear_svn_password`；响应只返回 `has_svn_password`，不返回密码。Git 和 SVN 地址二选一，可保留本地后备。SyncInput 新增 `source=svn`、可选 `expected_revision`；SnapshotResponse 新增可空 `svn_revision`，ReleaseInput / ReleaseResponse 新增可空 `expected_revision`。版本号使用十进制字符串，与 Git commit 分开保存。

AnalysisResponse 与 RunResponse 增加非空 JSON `source_context`，历史记录为 `{}`。包含 `version/bindings/snapshots/candidates/data_gaps`；含义和只读 MCP 工具参数见[第二阶段说明](开发源码与运维关联第二阶段.md)。正式采用复制本轮上下文；新复盘证据投影冻结该字段。现有 HTTP 路径和角色权限不变。


## 修复交接

HandoffResponse 包含 incident_id、analysis_id、可空 application_id、requested_by、冻结 packet 及 packet_hash、追加的 results/verifications 与 UTC created_at/updated_at。HandoffInput 要求正式 analysis_id 和 acceptance_criteria，可选服务及复现线索；RepairResultInput 保存修改、测试、可选 commit/发布/部署；RepairVerificationInput 绑定 result_id，记录人工结论、证据时间和验收结果。具体约束见[第三阶段说明](开发源码与运维关联第三阶段.md)，字段以 OpenAPI 为准。

## 服务级只读数据库

DatabaseInput 保存结构化连接字段、环境、用途、数据库说明、逐表 `table_notes`、逐表字段白名单 `allowed_tables`，以及连接和业务查询启停状态。`application_id` 可空：独立新建时省略表示不关联服务，编辑时省略保留原关联，显式传 `null` 解除关联。`password`、`ca_certificate` 仅写入；省略时保留，响应只返回 `password_configured`、`ca_configured`。DatabaseResponse 的 `application_id` 可为 `null`，还包含配置版本、结构哈希和刷新时间、最近连接测试及权限核验状态；DatabaseSchemaResponse 返回缓存表结构和人工说明。完整字段以 [OpenAPI](openapi.json) 为准。

AnalysisResponse 与 RunResponse 增加 `database_context`，冻结本轮连接、访问范围、结构和业务说明；历史记录为 `{}`。正式采用和新复盘保留该快照。数据库证据包含服务和连接、配置及结构版本、查询摘要、查询时间、脱敏结果、截断标记和内容哈希。使用与授权边界见[服务级只读数据库接入](服务级只读数据库接入.md)。

## 八项设计补全数据结构

新增结构详见 [OpenAPI](openapi.json) 和[实施说明](../project/八项设计补全实施与验收.md)。ObjectiveInput 绑定有限 operation/监测项，包含明确 target、1/7/30 天窗口、application/platform 来源和 expected_version；ObjectiveResponse 将当前可用性与长期 evaluation 分开。恢复字段追加冻结窗口、稳定起点和最近证据时间，不重写原执行结果。

响应策略使用真实用户 UUID，不解析 owner 文本。NotificationResponse 的 read_at、mail_status 与事故接单状态独立，unknown 不表示已送达。CaseResponse 保存不可变版本和哈希，ReplayResponse 保存模型配置、提示词、工具检查和独立人工结论；未知预测观测不计命中失败。

整改任务保存报告版本/条目、负责人、期限、验收条件、提交与验收记录及版本。知识有效期追加适用服务/版本、负责人、复核及到期时间，无到期值继续有效。BackupResponse 的实测耗时可空，恢复目标独立返回。

CleanupResponse 包含冻结候选、引用保护原因、逐项执行结果与确认版本。ArchiveResponse 包含原对象/所有者、SHA256、归档容量、实际释放文件字节及数据库逻辑可复用字节；内容读取再次校验权限与哈希。用量记录保留来源、用途、模型、提供者类别、可空 token、usage 缺失数及计价版本；价格按生效时间追加，币种独立，预算包含未知费用记录数。

系统容量响应的 `tables` 为各表含索引和 TOAST 的磁盘占用，`attachment_bytes` 为冻结报告附件的有效载荷字节；二者不直接相加。`directories` 分别记录源码快照、缓存、临时目录和归档的文件容量。

案例收录新增 CasePreview（analysis_id、snapshot、content_hash）和 CaseInput 可选 expected_snapshot_hash；前端预览后携带哈希保存，不一致返回 409。采用的正式诊断追加必要的脱敏工具参数及 Skill 依据，私有聊天和未采用输出保持私有。新运行 config 中 diagnosis_mode 为 read_only；历史记录不补写。详见[诊断链路修复](../project/Agent诊断链路修复与验收.md)。

## Agent 智能巡检

当前模型详见[巡检中心](巡检中心.md)，对应迁移 `0035_inspection_center`。

- `InspectionDataSourceInput/Response` 管理可复用 API／数据库读取配置、服务与环境范围、版本和连接测试状态；认证请求头加密保存，仅返回 `headers_configured`，数据库密码继续由已有 `ServiceDatabase` 管理。
- `InspectionPlanInput/Response` 增加 `data_source_id`、`schedule_kind`、`cron_expression`、`schedule_timezone`、`scheduled_at`、`timeout_seconds`、`max_retries`；列表返回最近执行时间、状态和结果。`next_run_at` 对手动、事件及已入队的单次计划可为 null。主源和关联源旧配置兼容保留。
- `InspectionSource.response_format` 支持 `records` 协议及普通 `json`；普通 JSON 用 `items_path`、`object_id_path` 映射对象。自定义查询参数与加密认证约束沿用原实现。
- `InspectionRunResponse` 分开保存执行状态、错误阶段、错误代码、重试时间和次数；`summary.health_counts` 为对象正常／异常／待确认数量，`summary.objects` 为未删除对象数。
- `InspectionItemResponse` 分别保存 `checks`、`rule_verdict`、`agent_status`、`agent_result`；新增 `final_status` 和 `explanation` 供页面直接展示。旧 `verdict` 保留兼容，不作为通用健康状态。模型分析不得修改规则结果；跨批次保留独立观察。
- `InspectionReviewResponse` 继续为追加式人工复核。原有取消和软删除语义保留；对象去重限当前批次，删除不影响其他批次及其证据。
- `InspectionSummary.issues_total` 统计规则未通过的对象记录，`items_total` 为累计对象观察数；其他统计口径不变，排除软删除记录。

## 2026-10-25 权限数据结构

新增 `RolePermission`、`AccessPolicy`、`AccessRequest`、`AccessGrant`，分别保存角色默认分配、资源受限状态、包含权限快照的申请和可回收授权。所有新表保留 UTC 创建、更新时间及软删除时间；授权单独记录到期和撤销时间。`MeResponse` 增加 `default_permissions`、`permissions_version`、`dashboard_access`，`RoleResponse` 增加默认权限版本。请求中的有效期支持 1、7、30 天及 null（长期）；批准期限不能超过申请期限。完整字段以 OpenAPI 为准，语义见[权限与资源授权](权限与资源授权.md)。

## Agent Skill 自主巡检扩展（0036）

- `InspectionPlanInput/Response` 新增 `execution_mode=records|agent`（默认 records）、`agent_source_ids`（其他授权 API 数据源 UUID 列表，最多20）、`agent_parameters`（业务 JSON，最多16 KiB）。agent 模式要求 agent_enabled、skill_id、data_source_id，不配置 rules 或 related_sources。
- `InspectionSource` 新增 `agent_api_enabled`（默认 false）、`request_method=GET|POST`、`path_template`、`allowed_query_params`、`allowed_body_fields`。POST 必须显式开启 Agent 查询授权；接口凭据仍使用加密 headers。
- `SkillInput/Response` 新增 `scripts`：`scripts/name.py` 到 Python 源码的映射，最多10个，每个32 KiB；修改后沿用 Skill version 递增和运行快照绑定。
- 自主巡检结果复用 InspectionItem；工具调用证据存入 evidence.tool_calls，逐项目结论复用 final_status、agent_result 和人工复核接口。批次 summary 包含 workflow_summary、no_matches、source_complete；未完成标记 partial / inspection_workflow_incomplete。
- 详细工具、限制及部署见 [Agent Skill 自主巡检](Agent-Skill自主巡检.md)。

## 智能巡检统一检查结构（2026-10-29）

`InspectionPlanInput` 新增 `check_items`、`object_mode` 和 `fixed_objects`。使用 `check_items` 自动识别为 `execution_mode=checks`，不与旧规则/Agent 开关混填；旧模式保持兼容。检查项定义随任务版本固定，结果单独持久化。

`InspectionCheckResultResponse` 分离 `execution_status=pending/running/completed/error` 与 `status=pass/fail/unknown`，包含对象、检查 ID、范围、严重度、问题及错误。`InspectionTraceResponse` 保存工具、成功状态和脱敏输入输出，Python 轨迹保留代码正文与摘要。

`GET /inspection-runs/{id}/progress` 返回 `InspectionProgressResponse`：`status` 为本轮执行状态，`current` 为正在执行的检查项（没有时为 null），`events` 为按时间升序排列的步骤，`next_cursor` 用于读取更早步骤。默认取最近 60 步，`limit` 为 1–100；将 `next_cursor` 作为 `before` 继续读取，直到游标为空。时间相同的步骤以稳定 ID 排序。

`InspectionProgressEvent` 包含稳定 `id`、`occurred_at`、`kind`、`status`、`object_key`、`check_name`、`tool`、`summary`、`error_code`、脱敏 `data`。类型包括进入队列、开始巡检、发现对象、开始/结束检查、工具结果和巡检结束。统一检查读取检查项及工具证据，旧版自主巡检读取已保存的 `evidence.tool_calls`。接口从已提交事实生成轨迹，不写库、不改变执行流程；工具结果在调用完成并保存后可见，执行中的检查项通过 `current` 展示。

执行汇总新增 `summary.coverage`，包含发现是否完成、对象总量是否可靠、应检查/完成/未完成数量和合并结论。未结束的发现、漏检和执行错误不能形成全部通过；集合检查不计入对象数量。

数据源结构增加共享 HTTP 连接引用、JSON 分页配置和登记 SQL/参数。Skill 增加受限的 `resources` 文本资源。详细字段、枚举和大小限制见 [OpenAPI](openapi.json)；示例见[第一阶段实施与验收](智能巡检第一阶段实施与验收.md)。

## 智能巡检试运行与页面闭环（0041）

`InspectionTrialInput` 为 `{plan: InspectionPlanInput, sample_size: 3}`，仅接受统一检查，样本上限 1–10。`InspectionRunResponse.run_kind` 区分 standard/trial；`summary.coverage.scope_is_sample` 标明覆盖口径，试运行完成不代表全量发现完成。正式任务列表、最近结果、进度与巡检总览排除试运行快照或结果。

任务列表响应增加 `group_total` 与 `last_run_id`；组总数遵循相同服务、环境、搜索、状态和权限筛选。轨迹响应增加 `object_key/check_id/check_name`，只查询当前页关联检查，便于区分多对象的同类工具调用。`InspectionScheduleResponse.times` 最多返回三个 UTC 时间，前端按显示时区格式化。详情与阶段边界见[第二阶段实施与验收](智能巡检第二阶段实施与验收.md)。



## 巡检补检关联

`InspectionRunResponse.snapshot.resume` 包含 `parent_run_id`、`observation=original_evidence`、`original_window_end`。继承完成项的 `InspectionCheckResultResponse.result.inherited_result_id` 指向原结果；原对象 observed_at、证据和原执行不改写。检查当前资料应另行执行任务。

## Agent 结果档案

ResultSummary 包含 kind、id、scope_id、run_id、title、summary、outcome、created_at、historical。ResultPage 返回 items、total、next_offset。ResultDetail 返回 record、section、content、offset、next_offset、total_chars；偏移以 Unicode 字符数计。ResultKnowledgeDraft 接受 title、content、tags，不允许客户端指定启用状态或来源。详见[Agent 结果沉淀与检索](Agent结果沉淀与检索.md)。


## 回收站

`RecycleEntry` 返回 `kind`、`label`、`id`、`name`、`created_at`、`updated_at`、`deleted_at`、`restore_note` 和可空的 `blocked_reason`。`RecycleType` 包含 `kind`、`label`、`count`。`RestoreInput` 必填带时区的 `expected_deleted_at`；`RestoreResult` 返回 `kind`、`id`、`restored_children`。不返回资源配置、凭据或证据正文。

`DeleteInput` 同样必填带时区的 `expected_deleted_at`，用于永久删除时校验所选删除批次。仅清除当前仍在回收站且删除时间完全匹配的记录；成功返回 204，无响应体。关联引用或状态变化返回 409，事务完整回滚。

## 智能问数阶段 5（分析库、治理设置、统计与导入导出）

`AskDataAnalysisResponse` 返回 `connection_id`、`model_version_id`、`owner_id`、`name`、`category`、`question`、`sql_text`、`explanation`、`chart`、`evidence`、`source_run_id` 与最近执行状态（`last_status`、`last_error_code`、`last_row_count`、`last_duration_ms`、`last_executed_at`）。`AskDataAnalysisPage` 额外返回 `categories` 供筛选；`AskDataAnalysisRunResponse` 返回 `status`、`error_code`、`duration_ms`、`executed_at`、`model_version_id` 与 `AskDataResult`（结构与问数运行一致，`certification` 固定 `draft`）。

`AskDataExecutionSettingsInput` 必填 `expected_version`，其余为 `budget_seconds`（30—900）、`reasoning_channel_id`、`light_channel_id`、`vector_model`、`vector_dimension`（1—8192）、`reranker_enabled`、`reranker_endpoint`、`reranker_model`、`reranker_api_key`（`SecretStr`，不回显）。`AskDataExecutionSettingsResponse` 不返回密钥，只返回 `reranker_configured` 与 `reranker_verified_at`，并提供 `channels`（可选通道）、`vector_signature` 与 `version`。

治理资产：`AskDataZoneResponse`（`name`、`description`、`targets`、`enabled`）；`AskDataTagResponse`（`name`、`color`、`description`、`targets: AskDataTagTargetInput[]`，目标为 `object_kind`∈`table|column|term|metric|code_rule` + `object_key`）；`AskDataRuleResponse`（`kind`∈`statistical_scope|time_semantics|unit|filter|soft_delete|status`、`target`、`expression`、`unit`、`priority`、`enabled`）；`AskDataSensitiveFieldResponse`（`object_key`、`category`、`policy`∈`mask|exclude`、`mask_strategy`∈`keep_prefix|keep_suffix|hash|full`、`mask_argument`、`sampling`∈`allow|exclude`）；`AskDataSupplementResponse`（`kind`∈`abbreviation|alias|context`、`term`、`definition`、`aliases`、`applies_to`）。分页统一 `items` + `total`。

导入导出：`AskDataImportInput`（`filename`、`document`）；`AskDataImportPreview` 返回 `import_id`、`format`、`upstream_commit`、`counts`、`items: AskDataImportItem[]`（`asset`、`key`、`action`∈`create|update|skip|conflict`、`reason`）、`warnings`、`model_version_id`；`AskDataImportApplyInput` 只有 `conflict_policy`∈`skip|overwrite`；`AskDataImportResponse` 保存 `status`、`conflict_policy`、`report`、`applied_at`。导出文档不含凭据、查询原始结果与分析记录。

统计：`AskDataStatisticsResponse` 含 `start`、`end`、`days`、`totals: AskDataStatisticsTotals`（`runs`、`completed`、`clarification`、`failed`、`cancelled`、`success_rate`、`average_seconds`、`p50_seconds`、`p95_seconds`）、`daily: AskDataStatisticPoint[]`、`failures: AskDataStatisticFailure[]`、`models: AskDataStatisticModels`（`calls`、`input_tokens`、`output_tokens`）与 `certification: AskDataStatisticCertification`（`certified`、`draft`、`pending_review`、`coverage`）。所有分位与比率在服务端计算，前端不再二次聚合。
