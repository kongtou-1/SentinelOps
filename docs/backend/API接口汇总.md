# 后端 API 接口汇总

2026-11-06 智能问数阶段 1：新增 `GET /api/v1/askdata/settings/connections`、`GET/PUT /api/v1/askdata/settings/connections/{id}`，分别读取可见连接、上下文与管理问数启停；读取权限 `askdata:read`，修改权限 `askdata:manage`，同时核验连接资源范围。连接 CRUD 及结构刷新复用已有接口。见[实施进度](智能问数实施进度.md)。

2026-10-25 权限申请调整：`POST /api/v1/access/check` 增加可选 `include_descendants`（默认 false）；传 true 核对申请的下属范围。`POST /api/v1/access-requests` 拒绝已完整拥有的权限组，返回 409，保留原请求幂等行为。规则见[权限与资源授权](权限与资源授权.md)。

2026-10-16 服务可观测性增量：新增 `/api/v1/service-monitors` 列表与详情/修改/删除、`/{id}/probe`、`/{id}/metrics`，以及 `/api/v1/business-services/{id}/observability`、`/monitors`、`/traffic`、`/api/v1/servers/{id}/probe-config`、`/api/v1/monitoring/service-probe-metrics`。方法、角色权限、参数和使用流程见[服务可观测性实施与验收](服务可观测性实施与验收.md#api-与数据)，完整契约以更新后的 OpenAPI 为准。下文总数与旧字段表为 10 月 5 日基线。

2026-10-16：新增 `GET/PUT /api/v1/system/diagnosis-policy`。读取权限 `system:read`，修改权限 `rules:manage`；规则增加三态 `auto_diagnose`，正式诊断增加 `trigger`。行为和升级见[自动 AI 诊断](自动AI诊断.md)。

整理日期：2026-10-05。依据当前 `backend/app` 路由、Pydantic 模型和业务校验，覆盖已实现的十批次、补缺、前端对接和诊断工作台。共 **145 个接口（HTTP 方法 + 路径）**，不包含 Swagger、ReDoc 和 OpenAPI 自身的页面路由。

- [请求与响应数据结构](API数据结构.md)：完整字段、必填项、默认值、枚举与基础约束。
- [OpenAPI JSON](openapi.json)：直接导出的 `app.openapi()` 快照，可导入支持 OpenAPI 的接口工具。
- [AI 诊断工作台 API](../frontend/AI诊断工作台.md#api)：会话、消息、运行、事件和采用接口，以及所有权、SSE、幂等和终态约定；支持所有者删除无活动运行的会话，保留已采用的正式诊断。
- 运行时 [Swagger](http://127.0.0.1:8000/docs)、[ReDoc](http://127.0.0.1:8000/redoc)、[OpenAPI](http://127.0.0.1:8000/openapi.json)。服务启动和配置见 [项目 README](../../README.md)。

本文是代码快照；修改接口后应更新这三份文档。OpenAPI 自动生成的声明不包含所有运行时权限、业务校验、动态成功状态码和非 JSON 响应，相关补充见下文。

## 通用约定

- 本机服务地址为 `http://127.0.0.1:8000`，业务前缀 `/api/v1`；健康检查不带此前缀。JSON 请求设置 `Content-Type: application/json`。
- 业务认证使用 `Authorization: Bearer <access_token>`。三个固定角色为 `viewer`、`operator`、`admin`；下方权限直接按代码的角色权限映射列出。
- 服务接口使用 `Authorization: Bearer <服务密钥>`：监控发现/采集使用 `.local/monitoring.token`，Webhook 使用 `.local/alertmanager.token`（均为默认配置路径）。这两个密钥不能用用户 JWT 替代。OpenAPI 将其标成可选 Header，但运行时必须通过密钥校验。
- 路径 ID 为业务记录 UUID；`container_id` 是平台容器资产 UUID，`docker_id` 才是 Docker 标识。名称为 `{identifier}` 的参数同样是 UUID。
- 时间使用带时区 ISO 8601，例如 `2026-10-03T00:00:00Z`，存储统一 UTC。指标和预测结果的 `start/end` 必须同时提供；事故列表允许单独提供起止时间。
- 支持分页的列表使用 `offset=0`、`limit=50`，`offset ≥ 0`、`1 ≤ limit ≤ 100`；返回裸 JSON 数组，并通过 `X-Total-Count` 返回过滤后、分页前的总数（越过末页仍返回正确总数）。普通查询排除已软删除记录。知识检索是独立的 `limit=5`（1–10），不支持 offset。
- 普通请求模型拒绝额外字段；Webhook 模型允许接收并忽略 Alertmanager 的额外字段。PUT 使用完整输入模型及其默认值，省略可选字段可能重置配置；PATCH 只改显式提交字段。
- DELETE 为业务软删除；`DELETE /containers/{id}` 仅退役平台资产。`GET /servers/{id}/collectors-compose` 仅下载部署模板。两者都不会在目标服务器上执行 Docker 操作。
- 登录和刷新令牌响应使用 `Cache-Control: no-store`。SSH secret/passphrase、LLM API Key 不在资产响应中返回。
- `202` 表示任务已排队或返回已有任务，需读取对应详情的 `status/error_code`；独立 worker 必须运行。手动 LLM probe 则等待探测结果并返回 `200`。

## 错误响应

| HTTP 状态 | 含义与处理 |
| --- | --- |
| 401 | 登录凭据、用户令牌或服务密钥无效；用户认证错误附 `WWW-Authenticate: Bearer` |
| 403 | 已登录但角色无接口权限 |
| 404 | 记录不存在、已软删除，或尚无最新诊断 |
| 409 | 唯一名称保留、引用占用、状态冲突、版本冲突、审批失效等；根据 `detail` 处理 |
| 422 | 类型/字段校验失败，或时间范围、资源选择、指标参数等业务参数不合法 |
| 502 | Prometheus 请求、容器同步或规则应用失败 |
| 503 | 数据库、迁移、服务密钥、加密密钥、向量模型或 AI 配置不可用 |

通常为 `{"detail":"错误说明"}`。请求字段校验为 `{"detail":[{"loc":["body","字段"],"msg":"说明","type":"错误类型"}]}`，不返回输入值。就绪检查失败单独返回 `503 {"status":"not_ready"}`。部分操作失败仍返回 `200`（如 SSH 测试、LLM 探测），需同时判断响应内的 `success/status/error_type`。

## 接口目录

以下编号链接到每个接口的参数和响应。成功码列为路由默认声明；动态返回 `200` 的任务复用情形见业务说明。

### 健康与系统（3 个）

| 方法 | 路径 | 功能 | 权限 | 成功码 |
| --- | --- | --- | --- | --- |
| GET | `/health/live` | [进程存活检查](#api-1) | 公开（无 Bearer 认证） | 200 |
| GET | `/health/ready` | [数据库与迁移就绪检查](#api-2) | 公开（无 Bearer 认证） | 200 |
| GET | `/api/v1/system/status` | [系统运行状态](#api-3) | Operator/Admin；`system:read` | 200 |

### 前端总览与全局摘要（4 个）

| 方法 | 路径 | 功能 | 权限 | 响应 |
| --- | --- | --- | --- | --- |
| GET | `/api/v1/dashboard/summary` | 资源、监控启用数、容器清单采样、LLM 探测状态、有效待审批和告警/事故状态数量 | Viewer/Operator/Admin；`resources:read` | `DashboardSummary` |
| GET | `/api/v1/approvals` | 全局审批分页摘要 | Viewer/Operator/Admin；`executions:read` | `ConsoleItem[]` + `X-Total-Count` |
| GET | `/api/v1/executions` | 全局执行分页摘要 | Viewer/Operator/Admin；`executions:read` | `ConsoleItem[]` + `X-Total-Count` |
| GET | `/api/v1/reports` | 全局复盘分页摘要 | Viewer/Operator/Admin；`reports:read` | `ConsoleItem[]` + `X-Total-Count` |

三个全局摘要共同支持 `status`、`incident_id`、`actor_id`、`start`、`end`、`offset`、`limit`。`actor_id` 分别对应申请人、执行发起人、复盘生成发起人；时间过滤创建时间，允许单边范围，结束时间早于开始时间返回 422。状态使用对应详情的后端枚举；审批可查询 `expired`，将有效期已过的 pending/approved 只读投影为 expired，不因列表读取修改数据库。待办总数仅包含仍在有效期内的 pending。摘要保留历史事故/资源名称，不返回冻结参数和大体积证据；详情与写操作沿用原接口及权限。

总览中的容器状态和 `containers_last_seen_at` 来自最近清单同步，不代表实时 Docker 状态。LLM 分类沿用 `healthy/failed/stale/unknown/disabled`；不把停用探测视为可用。告警确认、告警结束、恢复核验保持独立统计和操作。

前端需要的既有列表过滤补充：服务器 `q` 匹配名称/地址，容器 `q` 匹配名称/镜像，LLM `q` 匹配名称/模型（不区分大小写，`%`、`_` 按字面匹配）；告警支持 `severity=warning|critical`、`acknowledged=true|false`、`start/end`（触发时间）；事故支持 `unassigned=true`；审计支持 `action` 精确匹配及 `start/end`（创建时间）。其他过滤仍见对应接口。

### 认证与用户（9 个）

| 方法 | 路径 | 功能 | 权限 | 成功码 |
| --- | --- | --- | --- | --- |
| POST | `/api/v1/auth/login` | [JSON 登录](#api-4) | 公开（无 Bearer 认证） | 200 |
| POST | `/api/v1/auth/refresh` | [刷新并轮换令牌](#api-5) | 公开（无 Bearer 认证） | 200 |
| POST | `/api/v1/auth/logout` | [退出当前会话](#api-6) | Viewer/Operator/Admin；有效登录会话 | 204 |
| GET | `/api/v1/me` | [当前用户与权限](#api-7) | Viewer/Operator/Admin；有效登录会话 | 200 |
| GET | `/api/v1/roles` | [固定角色列表](#api-8) | Admin；`roles:read` | 200 |
| GET | `/api/v1/users` | [用户列表](#api-9) | Admin；`users:manage` | 200 |
| POST | `/api/v1/users` | [创建用户](#api-10) | Admin；`users:manage` | 201 |
| PATCH | `/api/v1/users/{user_id}` | [修改用户](#api-11) | Admin；`users:manage` | 200 |
| DELETE | `/api/v1/users/{user_id}` | [软删除用户](#api-12) | Admin；`users:manage` | 204 |

### 服务器、凭据与容器（19 个）

| 方法 | 路径 | 功能 | 权限 | 成功码 |
| --- | --- | --- | --- | --- |
| GET | `/api/v1/ssh-credentials` | [SSH 凭据列表](#api-13) | Admin；`credentials:manage` | 200 |
| POST | `/api/v1/ssh-credentials` | [创建 SSH 凭据](#api-14) | Admin；`credentials:manage` | 201 |
| PUT | `/api/v1/ssh-credentials/{credential_id}` | [替换 SSH 凭据](#api-15) | Admin；`credentials:manage` | 200 |
| DELETE | `/api/v1/ssh-credentials/{credential_id}` | [软删除 SSH 凭据](#api-16) | Admin；`credentials:manage` | 204 |
| GET | `/api/v1/servers` | [服务器列表](#api-17) | Viewer/Operator/Admin；`resources:read` | 200 |
| POST | `/api/v1/servers` | [创建服务器资产](#api-18) | Admin；`resources:manage` | 201 |
| GET | `/api/v1/servers/{server_id}` | [服务器详情](#api-19) | Viewer/Operator/Admin；`resources:read` | 200 |
| PUT | `/api/v1/servers/{server_id}` | [更新服务器配置](#api-20) | Admin；`resources:manage` | 200 |
| DELETE | `/api/v1/servers/{server_id}` | [软删除服务器及其容器资产](#api-21) | Admin；`resources:manage` | 204 |
| POST | `/api/v1/servers/{server_id}/test-ssh` | [测试 SSH 连接](#api-22) | Admin；`resources:manage` | 200 |
| GET | `/api/v1/monitoring/targets` | [Prometheus HTTP 服务发现](#api-23) | 服务密钥：monitoring.token | 200 |
| GET | `/api/v1/servers/{server_id}/metrics` | [服务器指标](#api-24) | Viewer/Operator/Admin；`resources:read` | 200 |
| POST | `/api/v1/servers/{server_id}/sync-containers` | [同步容器资产](#api-25) | Operator/Admin；`containers:sync` | 200 |
| GET | `/api/v1/monitoring/container-metrics` | [容器清单指标采集](#api-26) | 服务密钥：monitoring.token | 200 |
| GET | `/api/v1/containers` | [容器列表](#api-27) | Viewer/Operator/Admin；`resources:read` | 200 |
| GET | `/api/v1/containers/{container_id}` | [容器详情](#api-28) | Viewer/Operator/Admin；`resources:read` | 200 |
| DELETE | `/api/v1/containers/{container_id}` | [软删除容器资产](#api-29) | Admin；`resources:manage` | 204 |
| GET | `/api/v1/containers/{container_id}/metrics` | [容器指标](#api-30) | Viewer/Operator/Admin；`resources:read` | 200 |
| GET | `/api/v1/servers/{server_id}/collectors-compose` | [下载采集器 Compose 模板](#api-31) | Admin；`resources:manage` | 200 |

### 告警与规则（9 个）

| 方法 | 路径 | 功能 | 权限 | 成功码 |
| --- | --- | --- | --- | --- |
| GET | `/api/v1/alert-rules` | [告警规则列表](#api-32) | Viewer/Operator/Admin；`alerts:read` | 200 |
| POST | `/api/v1/alert-rules` | [创建告警规则](#api-33) | Admin；`rules:manage` | 201 |
| PUT | `/api/v1/alert-rules/{rule_id}` | [替换告警规则](#api-34) | Admin；`rules:manage` | 200 |
| DELETE | `/api/v1/alert-rules/{rule_id}` | [软删除告警规则](#api-35) | Admin；`rules:manage` | 204 |
| POST | `/api/v1/monitoring/apply-rules` | [应用规则并重载 Prometheus](#api-36) | Admin；`rules:manage` | 200 |
| POST | `/api/v1/hooks/alertmanager` | [接收 Alertmanager 通知](#api-37) | 服务密钥：alertmanager.token | 200 |
| GET | `/api/v1/alerts` | [告警事件列表](#api-38) | Viewer/Operator/Admin；`alerts:read` | 200 |
| GET | `/api/v1/alerts/{alert_id}` | [告警事件详情](#api-39) | Viewer/Operator/Admin；`alerts:read` | 200 |
| POST | `/api/v1/alerts/{alert_id}/ack` | [人工确认告警](#api-40) | Operator/Admin；`alerts:ack` | 200 |

### LLM 监控（8 个）

| 方法 | 路径 | 功能 | 权限 | 成功码 |
| --- | --- | --- | --- | --- |
| GET | `/api/v1/llm-endpoints` | [LLM Endpoint 列表](#api-41) | Viewer/Operator/Admin；`llm:read` | 200 |
| POST | `/api/v1/llm-endpoints` | [创建 LLM Endpoint](#api-42) | Admin；`llm:manage` | 201 |
| GET | `/api/v1/llm-endpoints/{endpoint_id}` | [LLM Endpoint 详情](#api-43) | Viewer/Operator/Admin；`llm:read` | 200 |
| PUT | `/api/v1/llm-endpoints/{endpoint_id}` | [替换 LLM Endpoint 配置](#api-44) | Admin；`llm:manage` | 200 |
| DELETE | `/api/v1/llm-endpoints/{endpoint_id}` | [软删除 LLM Endpoint](#api-45) | Admin；`llm:manage` | 204 |
| POST | `/api/v1/llm-endpoints/{endpoint_id}/probe` | [手动推理探测](#api-46) | Operator/Admin；`llm:probe` | 200 |
| GET | `/api/v1/monitoring/llm-metrics` | [LLM 探测指标采集](#api-47) | 服务密钥：monitoring.token | 200 |
| GET | `/api/v1/llm-endpoints/{endpoint_id}/metrics` | [LLM 时序指标](#api-48) | Viewer/Operator/Admin；`llm:read` | 200 |

### 预测任务与结果（9 个）

| 方法 | 路径 | 功能 | 权限 | 成功码 |
| --- | --- | --- | --- | --- |
| GET | `/api/v1/prediction-tasks` | [预测任务列表](#api-49) | Viewer/Operator/Admin；`predictions:read` | 200 |
| POST | `/api/v1/prediction-tasks` | [创建预测任务](#api-50) | Admin；`predictions:manage` | 201 |
| GET | `/api/v1/prediction-tasks/{task_id}` | [预测任务详情](#api-51) | Viewer/Operator/Admin；`predictions:read` | 200 |
| PUT | `/api/v1/prediction-tasks/{task_id}` | [替换预测任务](#api-52) | Admin；`predictions:manage` | 200 |
| DELETE | `/api/v1/prediction-tasks/{task_id}` | [软删除预测任务](#api-53) | Admin；`predictions:manage` | 204 |
| POST | `/api/v1/prediction-tasks/{task_id}/run` | [发起一次预测](#api-54) | Operator/Admin；`predictions:run` | 202 |
| GET | `/api/v1/predictions` | [预测结果列表](#api-55) | Viewer/Operator/Admin；`predictions:read` | 200 |
| GET | `/api/v1/predictions/{result_id}` | [预测结果详情](#api-56) | Viewer/Operator/Admin；`predictions:read` | 200 |
| GET | `/api/v1/monitoring/prediction-metrics` | [预测风险与异常指标采集](#api-57) | 服务密钥：monitoring.token | 200 |

### 事故中心（9 个）

| 方法 | 路径 | 功能 | 权限 | 成功码 |
| --- | --- | --- | --- | --- |
| GET | `/api/v1/incidents` | [事故列表](#api-58) | Viewer/Operator/Admin；`incidents:read` | 200 |
| GET | `/api/v1/incidents/assignees` | [可选负责人列表](#api-59) | Viewer/Operator/Admin；`incidents:read` | 200 |
| GET | `/api/v1/incidents/{incident_id}` | [事故详情及最近时间线](#api-60) | Viewer/Operator/Admin；`incidents:read` | 200 |
| PATCH | `/api/v1/incidents/{incident_id}` | [修改标题或负责人](#api-61) | Operator/Admin；`incidents:handle` | 200 |
| GET | `/api/v1/incidents/{incident_id}/alerts` | [事故关联告警](#api-62) | Viewer/Operator/Admin；`incidents:read` | 200 |
| GET | `/api/v1/incidents/{incident_id}/timeline` | [事故完整时间线分页](#api-63) | Viewer/Operator/Admin；`incidents:read` | 200 |
| POST | `/api/v1/incidents/{incident_id}/status` | [人工变更事故状态](#api-64) | Operator/Admin；`incidents:handle` | 200 |
| POST | `/api/v1/incidents/{incident_id}/comments` | [添加事故评论](#api-65) | Operator/Admin；`incidents:handle` | 201 |
| POST | `/api/v1/incidents/{incident_id}/merge` | [将当前事故合并至目标事故](#api-66) | Operator/Admin；`incidents:handle` | 200 |

### AI 诊断（4 个）

| 方法 | 路径 | 功能 | 权限 | 成功码 |
| --- | --- | --- | --- | --- |
| POST | `/api/v1/incidents/{incident_id}/analyze` | [发起或复用 AI 诊断](#api-67) | Operator/Admin；`analyses:run` | 202 |
| GET | `/api/v1/incidents/{incident_id}/analysis` | [事故最新诊断](#api-68) | Viewer/Operator/Admin；`analyses:read` | 200 |
| GET | `/api/v1/incidents/{incident_id}/analyses` | [事故诊断历史](#api-69) | Viewer/Operator/Admin；`analyses:read` | 200 |
| GET | `/api/v1/analyses/{analysis_id}` | [诊断详情](#api-70) | Viewer/Operator/Admin；`analyses:read` | 200 |

### 知识库与 Skill（12 个）

| 方法 | 路径 | 功能 | 权限 | 成功码 |
| --- | --- | --- | --- | --- |
| GET | `/api/v1/knowledge` | [知识文档列表](#api-71) | Viewer/Operator/Admin；`knowledge:read` | 200 |
| POST | `/api/v1/knowledge` | [创建文档并排队索引](#api-72) | Admin；`knowledge:manage` | 202 |
| GET | `/api/v1/knowledge/search` | [知识向量检索](#api-73) | Viewer/Operator/Admin；`knowledge:read` | 200 |
| GET | `/api/v1/knowledge/{document_id}` | [知识文档详情](#api-74) | Viewer/Operator/Admin；`knowledge:read` | 200 |
| PUT | `/api/v1/knowledge/{document_id}` | [替换文档并重新索引](#api-75) | Admin；`knowledge:manage` | 202 |
| DELETE | `/api/v1/knowledge/{document_id}` | [软删除文档并撤回索引](#api-76) | Admin；`knowledge:manage` | 204 |
| POST | `/api/v1/knowledge/{document_id}/reindex` | [重新建立文档索引](#api-77) | Admin；`knowledge:manage` | 202 |
| GET | `/api/v1/skills` | [Skill 列表](#api-78) | Viewer/Operator/Admin；`knowledge:read` | 200 |
| POST | `/api/v1/skills` | [创建 Skill](#api-79) | Admin；`knowledge:manage` | 201 |
| GET | `/api/v1/skills/{skill_id}` | [Skill 详情](#api-80) | Viewer/Operator/Admin；`knowledge:read` | 200 |
| PUT | `/api/v1/skills/{skill_id}` | [替换 Skill](#api-81) | Admin；`knowledge:manage` | 200 |
| DELETE | `/api/v1/skills/{skill_id}` | [软删除 Skill](#api-82) | Admin；`knowledge:manage` | 204 |

### 审批、执行与审计（10 个）

| 方法 | 路径 | 功能 | 权限 | 成功码 |
| --- | --- | --- | --- | --- |
| GET | `/api/v1/action-skills` | [可信 Action Skill 目录](#api-83) | Viewer/Operator/Admin；`executions:read` | 200 |
| POST | `/api/v1/incidents/{incident_id}/approvals` | [从诊断提案申请审批](#api-84) | Operator/Admin；`executions:run` | 201 |
| GET | `/api/v1/incidents/{incident_id}/approvals` | [事故审批列表](#api-85) | Viewer/Operator/Admin；`executions:read` | 200 |
| GET | `/api/v1/approvals/{identifier}` | [审批详情](#api-86) | Viewer/Operator/Admin；`executions:read` | 200 |
| POST | `/api/v1/approvals/{identifier}/approve` | [批准审批](#api-87) | Admin；`approvals:decide` | 200 |
| POST | `/api/v1/approvals/{identifier}/reject` | [拒绝审批](#api-88) | Admin；`approvals:decide` | 200 |
| POST | `/api/v1/approvals/{identifier}/execute` | [消费审批并排队执行](#api-89) | Operator/Admin；`executions:run` | 202 |
| GET | `/api/v1/incidents/{incident_id}/executions` | [事故执行列表](#api-90) | Viewer/Operator/Admin；`executions:read` | 200 |
| GET | `/api/v1/executions/{identifier}` | [执行详情](#api-91) | Viewer/Operator/Admin；`executions:read` | 200 |
| GET | `/api/v1/audit-logs` | [审计记录列表](#api-92) | Admin；`audit:read` | 200 |

### 事故复盘（9 个）

| 方法 | 路径 | 功能 | 权限 | 成功码 |
| --- | --- | --- | --- | --- |
| POST | `/api/v1/incidents/{incident_id}/reports` | [生成或复用复盘报告](#api-93) | Operator/Admin；`reports:run` | 202 |
| GET | `/api/v1/incidents/{incident_id}/reports` | [事故复盘报告历史](#api-94) | Viewer/Operator/Admin；`reports:read` | 200 |
| GET | `/api/v1/reports/{identifier}` | [复盘报告详情](#api-95) | Viewer/Operator/Admin；`reports:read` | 200 |
| PUT | `/api/v1/reports/{identifier}` | [编辑复盘草稿](#api-96) | Admin；`reports:manage` | 200 |
| DELETE | `/api/v1/reports/{identifier}` | 软删除复盘报告；保留修订、发送记录及审计；生成或邮件发送中返回 409 | Admin；`reports:manage` | 204 |
| GET | `/api/v1/reports/{identifier}/revisions` | [复盘修订历史](#api-97) | Viewer/Operator/Admin；`reports:read` | 200 |
| POST | `/api/v1/reports/{identifier}/review` | [审核复盘草稿](#api-98) | Admin；`reports:manage` | 200 |
| GET | `/api/v1/reports/{identifier}/export` | [导出已审核 PDF 或 Word](#api-99) | Viewer/Operator/Admin；`reports:read` | 200 |
| POST | `/api/v1/reports/{identifier}/send` | [人工发起复盘邮件](#api-100) | Admin；`reports:manage` | 202 |
| GET | `/api/v1/reports/{identifier}/deliveries` | [复盘邮件发送记录](#api-101) | Admin；`reports:manage` | 200 |

## 业务限制与调用顺序

### 认证与用户

登录提交 JSON `username/password`，不是 OAuth2 表单。用户名为 3–64 位小写字母、数字、下划线、点或连字符；登录密码 1–128 位，新建/重设密码 12–128 位。用户 PATCH 至少提交一个非 null 字段。用户只有一个固定角色，不能删除、禁用或降级最后一个有效管理员。

访问令牌默认 30 分钟，刷新会话默认 7 天，配置可调整。refresh 一次消费并轮换，客户端保存返回的新 token 对；刷新不会延长会话绝对期限。退出、禁用、删除、重设密码会撤销相应会话；角色修改立即影响已有会话权限。软删除的用户名仍被保留。

### 资源和时序指标

创建服务器必须提供 `name/host/node_exporter_target/cadvisor_target`。Exporter target 是 `host:port`；绑定 SSH 凭据必须同时配置固定 OpenSSH 主机公钥。SSH 测试要求凭据和公钥齐全。服务器 PUT 省略 `docker_state_target` 会保留原值，显式 null 才清除；其他字段按完整输入模型更新。已被有效服务器引用的 SSH 凭据不能删除。

容器资产从 cAdvisor 同步，无手工创建接口。同步要求监控启用且采集器可用；采集失败保留资产清单并返回 502。服务器指标可覆盖该服务器上的容器序列；容器指标接口要求 metric 以 `container_` 开头。

即时查询不传 start/end；区间查询要求 `0 < end-start ≤ 31 天`、`(end-start)/step ≤ 11000`，step 15–86400 秒，默认 30。返回 Prometheus 的 data 层，数值通常为字符串，时间戳为 Unix 秒，保留各序列 labels。

资源指标返回 `resultType/result/availability`。`available` 表示存在有效样本；`unavailable + no_valid_samples` 表示没有有效样本；`unconfigured` 可带 `monitoring_disabled`、`docker_state_not_configured` 或 `http_probe_not_configured`。NaN/Inf 样本过滤，无数据不能当作零值。LLM 指标单独返回 `metric/status/resultType/result`，NaN/Inf 值转换为 null，不附资源指标的 availability。

资源 metric 枚举见接口参数表；网络收发、磁盘读写为 bytes/s，错误/丢包为次/s，HTTP 与 I/O 延迟为秒，重启/OOM 为最近 5 分钟次数。容器 CPU 按 CPU 核累计，可超过 100%。新增 docker-state 事件指标依赖完整 5 分钟窗口。

### 告警规则和恢复

规则只接受固定 metric，不接受任意 PromQL。availability 指标 `target_up/http_up/container_up/llm_up` 的 threshold 必须为 1（低于 1 触发）；LLM 错误标志和 stale 的 threshold 为 0（高于 0 触发）。规则接口支持的 LLM metric 子集不同于 LLM 查询 metric，具体以 RuleInput 为准。

预测规则必须只绑定 `prediction_task_id`；`prediction_risk` 要求 horizon=1/6/24 且 threshold=0/1；`prediction_anomaly` 要求 threshold=0 且不设 horizon。其他资源规则可不指定资产 ID，成为该指标的全局规则。

增删改规则只更新业务库，随后由 Admin 调用 `POST /monitoring/apply-rules` 写入配置并重载 Prometheus；成功返回 `{"applied":数量}`。失败应读取 detail 并确认实际生效状态。

Alertmanager 通知以 `(fingerprint, startsAt)` 幂等落库；同一告警发生记录已 resolved 后，延迟 firing 不重新打开它。ack 只记录人工确认，不改变 firing/resolved，也不表示恢复。第一次 ack 保留 comment，后续重复调用返回已有结果。

`status=resolved` 只表示告警通知结束；资源恢复必须检查 `recovery_status=verified` 和 `recovery_evidence_at`。pending 表示待核验，terminated 表示核验终止且未证实恢复。事故详情提供对应告警数量；恢复核验由 prediction-worker 有界执行。

### LLM Endpoint

当前 provider 固定 deepseek，base_url 必须为无账号密码、query、fragment 的 HTTP(S) 地址。创建必须提供 api_key；PUT 省略或置 null 保留原 Key。手动 probe 为真实推理请求，结果通过 success/error_type/http_status/latency_ms 表示，可能消耗 Provider token。监控 Endpoint Key 与 AI 诊断 Provider Key 分别配置。

Endpoint status 为 disabled/stale/unknown/healthy/failed；距最近探测（从未探测则从创建时间算起）超过两倍 probe_interval_seconds 为 stale。探测 error_type 可为 authentication_failed（401）、insufficient_balance（402）、rate_limited（429）、service_error（5xx）、request_error（其他非 200）、timeout、connection_failed、invalid_response；成功时为 null。

### 预测

TaskInput 恰选一个 server_id/container_id/llm_endpoint_id，metric 前缀必须匹配资源。selector 精确匹配一条序列：host_disk_percent 必须有 device/mountpoint/fstype；host_network_* 必须有 device；container_network_* 必须有 interface；其余必须是空字典。threshold 非负；host CPU/内存/磁盘上限 100，llm_error_rate 上限 1。新任务默认 enabled=false，不会自动调度；手动 run 排队，运行中不允许更新或删除。

ResultResponse 的 `horizons` 按 `"1"/"6"/"24"` 小时保存曲线、质量和风险。风险 level 为 normal/possible/expected/unknown；样本或验证不足为 unknown。查询结果还应检查 status、fresh、quality 和 error_code，不能仅凭 HTTP 成功判断预测有效。

有曲线的 horizon 包含 `model/quality/interval_level/points/risk`，points 为 `{timestamp,value,lower,upper}` 数组；无足够数据时可能只有质量与 unknown 风险。时间戳为 Unix 秒，曲线间隔 5 分钟；quality 可为 insufficient_data/provisional/validated/interval_low_confidence。

### 事故和 AI 诊断

事故由关联告警聚合生成，无手工创建/删除接口。负责人必须是有效 Operator/Admin，PATCH assignee_id=null 可取消分配。详情只带最近 50 条时间线，完整历史使用 timeline 分页。

人工状态入口接受 open/in_progress/closed，必须有 comment；open 与 in_progress 可互转。普通关闭要求已 recovered，或关联告警均已 resolved、核验均 verified/terminated 且至少一个 terminated；仍有 firing 时不能普通关闭。Admin 可提交 `status=closed, force=true, comment=关闭原因` 强制关闭可写事故，额外要求 `incidents:force_close`，其他角色返回 403。原因不能为空且最多 2000 字符。closed/merged 事故只读；重复关闭幂等且不覆盖原关闭记录；合并时两边都必须可写，返回目标事故。

关闭快照 `closure` 保存操作人、时间、原因、强制标识及关闭时各告警恢复事实；时间线和审计追加记录。关闭不会解除告警、静默监控或伪造恢复时间。pending/approved 执行授权同步失效，已排队但未派发的任务被 worker 阻止，已派发动作继续记录真实结果。新发生的告警正常建事故，同一次告警的重复或晚到通知不重开事故。新复盘冻结该快照，PDF/Word 明确显示强制关闭及恢复未核验事实。

AI 诊断要求 AI 配置可用及事故可写；active queued/running 任务总是复用。force=false 时，输入未变化且最近 5 分钟内完成的诊断返回 200；新任务或排队/运行任务返回 202。force=true 绕过完成结果缓存，但不会并发创建已有 active 任务。读取 `GET /analyses/{analysis_id}` 获取最终 result、evidence、knowledge、tool_calls 和 error_code。

AI proposal 固定 restart_container v1，medium 风险、requires_human_approval=true、executable=false；返回提案不能直接执行。结果 dict 的业务结构见 [Diagnosis](API数据结构.md#diagnosis)。

### 知识库和两种 Skill

知识文档创建/替换/reindex 返回 202，由 ai-worker 建立索引；index_status=ready 后才进入检索。替换与重新索引提升 version 并撤回旧索引；queued/running 时再次 reindex 返回 409。检索只返回有效、启用、ready 且版本一致的片段，结构为 `{id,document_id,version,title,source,content,similarity}` 数组。

`/skills` 管理 agent 按任务选择、按需加载的 Skill 指令，包含适用场景、诊断步骤、工具说明和输出要求；`/action-skills` 是代码内定义的可信执行动作目录。agent 通过只读 MCP `list_skills` 获取目录（ID、标识、标题、标签、版本，不含正文），再通过 `read_skill(skill_id, version)` 加载指令。停用或软删除返回 `skill_unavailable`，版本不匹配返回 `skill_version_changed`。加载版本与正文保存在诊断工具记录中；Skill 不能扩展工具权限或绕过审批。

### 审批与受控执行

流程：已完成且有效的诊断提案 → `POST /incidents/{id}/approvals`（仅 analysis_id）→ Admin approve/reject（必填 comment）→ Operator/Admin execute → 查询 execution → 查看恢复证据与审计。

申请要求事故为 open/in_progress、目标容器属于事故范围、SSH 已配置。审批冻结目标、参数、Skill 版本、连接和凭据版本，默认 30 分钟有效；同诊断的有效 pending/approved 审批重复申请时返回已有记录（仍为 201）。目标或凭据等绑定变化会使审批 invalidated；过期为 expired。approve 不执行动作；execute 消费有效 approved 审批并返回 202，重复 execute 返回同一执行记录（仍为 202）。execute 请求体可省略或为 `{}`，不能覆盖参数。

执行状态经过 queued/prechecking/executing/verifying，结局需读取 status/error_code、precheck/output/verification。固定动作重启已绑定容器，并检查运行及配置了 HEALTHCHECK 时的健康状态。审批变 consumed 不等于操作成功；平台恢复判定还依赖监控证据。审计只有查询接口，Admin 可按事故、审批、执行、报告或用户筛选。

### 复盘、审核、导出和邮件

生成报告要求事故已 closed，且该事故及被合并源事故没有 active 诊断或执行。已有 queued/running 报告总是复用；force=false 还复用最新 draft/approved。新 queued 返回 202，其余返回 200；快照过大也可能返回 200 的 failed 记录，检查 error_code。

报告基于冻结证据快照。仅 Admin 可编辑/审核，且要求 draft 与 expected_revision 等于当前 revision；编辑追加修订。审核 decision=approve 转 approved；request_changes 留在 draft 并保留评论。已确认原因必须引用证据，引用只能来自报告 snapshot。

所有登录角色可导出 approved 报告，format 为必填 query：pdf/docx。响应为二进制附件，见具体接口。Admin 显式发送当前 approved revision 的 PDF 邮件附件，recipients 为 1–20 个 ASCII 邮箱，必填 comment 与 UUID idempotency_key；复用同一 key、同一请求（含用户）返回原投递，不同内容返回 409。

report-worker 异步发送，生成/审核不会自动发送或写入知识库。发送记录 status=unknown 表示派发后结果不能确认，不能直接认为未发送。实际外部邮件仍须按项目授权范围配置真实凭据与明确收件人。

### 异步状态字段

以下状态来自当前模型及 worker；失败或中断原因读取 error_code。

| 对象 | 状态 |
| --- | --- |
| 预测结果、AI 诊断 | queued / running / completed / failed / interrupted |
| 知识文档索引 | queued / running / ready / failed |
| 审批 | pending / approved / rejected / consumed / expired / invalidated |
| 执行 | queued / prechecking / executing / verifying / succeeded / failed / interrupted / unknown |
| 复盘报告 | queued / running / draft / approved / failed / interrupted |
| 邮件投递 | queued / sending / sent / failed / interrupted / unknown |

## 调用示例

以下是请求格式示例，所有 Token、UUID 和密码都是占位符。

```http
POST /api/v1/auth/login
Content-Type: application/json

{"username":"admin","password":"<你的密码>"}
```

```json
{"access_token":"<access_token>","refresh_token":"<refresh_token>","token_type":"bearer","expires_in":1800}
```

```http
GET /api/v1/servers?offset=0&limit=50
Authorization: Bearer <access_token>
```

```http
GET /api/v1/servers/<server_uuid>/metrics?metric=host_cpu_percent
Authorization: Bearer <access_token>
```

即时指标响应示例（示意样本）：

```json
{"resultType":"vector","result":[{"metric":{"server_id":"<server_uuid>"},"value":[1790985600,"12.5"]}],"availability":"available"}
```

区间查询的序列改为 `values: [[时间戳,"数值"], ...]`，resultType 为 matrix；labels 随指标和采集器变化。

```http
POST /api/v1/prediction-tasks
Authorization: Bearer <admin_access_token>
Content-Type: application/json

{"name":"主机内存风险","server_id":"<server_uuid>","metric":"host_memory_percent","selector":{},"threshold":85,"enabled":false}
```

```http
POST /api/v1/incidents/<incident_uuid>/analyze
Authorization: Bearer <operator_or_admin_access_token>
Content-Type: application/json

{"force":false}
```

任务返回 id 后，查询 `GET /api/v1/analyses/<analysis_uuid>`，等待 status=completed；若提案有效，再申请审批。以下三次写请求按顺序发起。

```http
POST /api/v1/incidents/<incident_uuid>/approvals
Authorization: Bearer <operator_or_admin_access_token>
Content-Type: application/json

{"analysis_id":"<analysis_uuid>"}
```

```http
POST /api/v1/approvals/<approval_uuid>/approve
Authorization: Bearer <admin_access_token>
Content-Type: application/json

{"comment":"已核对目标与影响，批准此绑定动作"}
```

```http
POST /api/v1/approvals/<approval_uuid>/execute
Authorization: Bearer <operator_or_admin_access_token>
Content-Type: application/json

{}
```

执行响应 id 用于 `GET /api/v1/executions/<execution_uuid>`。事故恢复并关闭后，发起 `POST /api/v1/incidents/<incident_uuid>/reports`，body 为 `{"force":false}`；报告 draft 经 Admin 审核 approve 后才能导出或发送。

```http
POST /api/v1/reports/<report_uuid>/review
Authorization: Bearer <admin_access_token>
Content-Type: application/json

{"expected_revision":1,"decision":"approve","comment":"已核对冻结证据与复盘结论"}
```

```http
GET /api/v1/reports/<report_uuid>/export?format=pdf
Authorization: Bearer <access_token>
```

```http
POST /api/v1/reports/<report_uuid>/send
Authorization: Bearer <admin_access_token>
Content-Type: application/json

{"expected_revision":1,"recipients":["operator@example.test"],"comment":"发送本次已审核复盘","idempotency_key":"<本次发送的UUID>"}
```

## 参数与响应详情

模型链接通向完整字段表。路径参数、查询参数和 Header 均分别列出；未声明响应模型的接口按实际返回实现补充。通用错误见上文，不在每个接口重复。

<a id="api-1"></a>

### 1. 进程存活检查

`GET /health/live`

权限：公开（无 Bearer 认证）。实现：[main.live](../../backend/app/main.py#L79)。

路径外无查询参数或 Header 参数声明。

请求体：无。

成功响应：`200`，`{"status":"ok"}`。

<a id="api-2"></a>

### 2. 数据库与迁移就绪检查

`GET /health/ready`

权限：公开（无 Bearer 认证）。实现：[main.ready](../../backend/app/main.py#L84)。

路径外无查询参数或 Header 参数声明。

请求体：无。

成功响应：`200`，`{"status":"ok","database":"ok"}`；失败 503 `{"status":"not_ready"}`。

<a id="api-3"></a>

### 3. 系统运行状态

`GET /api/v1/system/status`

权限：Operator/Admin；`system:read`。实现：[main.system_status](../../backend/app/main.py#L96)。

路径外无查询参数或 Header 参数声明。

请求体：无。

成功响应：`200`，`{service: string, database: "ok", revision: string}`。

<a id="api-4"></a>

### 4. JSON 登录

`POST /api/v1/auth/login`

权限：公开（无 Bearer 认证）。实现：[api.login](../../backend/app/api.py#L91)。

路径外无查询参数或 Header 参数声明。

请求体：必填，`application/json`，[LoginRequest](API数据结构.md#loginrequest)。

成功响应：`200`，[TokenResponse](API数据结构.md#tokenresponse)。

<a id="api-5"></a>

### 5. 刷新并轮换令牌

`POST /api/v1/auth/refresh`

权限：公开（无 Bearer 认证）。实现：[api.refresh](../../backend/app/api.py#L116)。

路径外无查询参数或 Header 参数声明。

请求体：必填，`application/json`，[RefreshRequest](API数据结构.md#refreshrequest)。

成功响应：`200`，[TokenResponse](API数据结构.md#tokenresponse)。

<a id="api-6"></a>

### 6. 退出当前会话

`POST /api/v1/auth/logout`

权限：Viewer/Operator/Admin；有效登录会话。实现：[api.logout](../../backend/app/api.py#L141)。

路径外无查询参数或 Header 参数声明。

请求体：无。

成功响应：`204`，无响应体。

<a id="api-7"></a>

### 7. 当前用户与权限

`GET /api/v1/me`

权限：Viewer/Operator/Admin；有效登录会话。实现：[api.me](../../backend/app/api.py#L147)。

路径外无查询参数或 Header 参数声明。

请求体：无。

成功响应：`200`，[MeResponse](API数据结构.md#meresponse)。

<a id="api-8"></a>

### 8. 固定角色列表

`GET /api/v1/roles`

权限：Admin；`roles:read`。实现：[api.roles](../../backend/app/api.py#L152)。

路径外无查询参数或 Header 参数声明。

请求体：无。

成功响应：`200`，数组（[RoleResponse](API数据结构.md#roleresponse)）。

<a id="api-9"></a>

### 9. 用户列表

`GET /api/v1/users`

权限：Admin；`users:manage`。实现：[api.users](../../backend/app/api.py#L160)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `offset` | query | 否 | integer | 最小值 `0`；默认 `0` |
| `limit` | query | 否 | integer | 最小值 `1`；最大值 `100`；默认 `50` |

请求体：无。

成功响应：`200`，数组（[UserResponse](API数据结构.md#userresponse)）。

<a id="api-10"></a>

### 10. 创建用户

`POST /api/v1/users`

权限：Admin；`users:manage`。实现：[api.create_user](../../backend/app/api.py#L173)。

路径外无查询参数或 Header 参数声明。

请求体：必填，`application/json`，[UserCreate](API数据结构.md#usercreate)。

成功响应：`201`，[UserResponse](API数据结构.md#userresponse)。

<a id="api-11"></a>

### 11. 修改用户

`PATCH /api/v1/users/{user_id}`

权限：Admin；`users:manage`。实现：[api.update_user](../../backend/app/api.py#L186)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `user_id` | path | 是 | string (uuid) | — |

请求体：必填，`application/json`，[UserUpdate](API数据结构.md#userupdate)。

成功响应：`200`，[UserResponse](API数据结构.md#userresponse)。

<a id="api-12"></a>

### 12. 软删除用户

`DELETE /api/v1/users/{user_id}`

权限：Admin；`users:manage`。实现：[api.delete_user](../../backend/app/api.py#L212)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `user_id` | path | 是 | string (uuid) | — |

请求体：无。

成功响应：`204`，无响应体。

<a id="api-13"></a>

### 13. SSH 凭据列表

`GET /api/v1/ssh-credentials`

权限：Admin；`credentials:manage`。实现：[resources_api.credentials](../../backend/app/resources_api.py#L53)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `offset` | query | 否 | integer | 最小值 `0`；默认 `0` |
| `limit` | query | 否 | integer | 最小值 `1`；最大值 `100`；默认 `50` |

请求体：无。

成功响应：`200`，数组（[CredentialResponse](API数据结构.md#credentialresponse)）。

<a id="api-14"></a>

### 14. 创建 SSH 凭据

`POST /api/v1/ssh-credentials`

权限：Admin；`credentials:manage`。实现：[resources_api.create_credential](../../backend/app/resources_api.py#L69)。

路径外无查询参数或 Header 参数声明。

请求体：必填，`application/json`，[CredentialInput](API数据结构.md#credentialinput)。

成功响应：`201`，[CredentialResponse](API数据结构.md#credentialresponse)。

<a id="api-15"></a>

### 15. 替换 SSH 凭据

`PUT /api/v1/ssh-credentials/{credential_id}`

权限：Admin；`credentials:manage`。实现：[resources_api.replace_credential](../../backend/app/resources_api.py#L79)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `credential_id` | path | 是 | string (uuid) | — |

请求体：必填，`application/json`，[CredentialInput](API数据结构.md#credentialinput)。

成功响应：`200`，[CredentialResponse](API数据结构.md#credentialresponse)。

<a id="api-16"></a>

### 16. 软删除 SSH 凭据

`DELETE /api/v1/ssh-credentials/{credential_id}`

权限：Admin；`credentials:manage`。实现：[resources_api.delete_credential](../../backend/app/resources_api.py#L90)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `credential_id` | path | 是 | string (uuid) | — |

请求体：无。

成功响应：`204`，无响应体。

<a id="api-17"></a>

### 17. 服务器列表

`GET /api/v1/servers`

权限：Viewer/Operator/Admin；`resources:read`。实现：[resources_api.servers](../../backend/app/resources_api.py#L108)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `offset` | query | 否 | integer | 最小值 `0`；默认 `0` |
| `limit` | query | 否 | integer | 最小值 `1`；最大值 `100`；默认 `50` |

请求体：无。

成功响应：`200`，数组（[ServerResponse](API数据结构.md#serverresponse)）。

<a id="api-18"></a>

### 18. 创建服务器资产

`POST /api/v1/servers`

权限：Admin；`resources:manage`。实现：[resources_api.create_server](../../backend/app/resources_api.py#L124)。

路径外无查询参数或 Header 参数声明。

请求体：必填，`application/json`，[ServerInput](API数据结构.md#serverinput)。

成功响应：`201`，[ServerResponse](API数据结构.md#serverresponse)。

<a id="api-19"></a>

### 19. 服务器详情

`GET /api/v1/servers/{server_id}`

权限：Viewer/Operator/Admin；`resources:read`。实现：[resources_api.server_detail](../../backend/app/resources_api.py#L130)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `server_id` | path | 是 | string (uuid) | — |

请求体：无。

成功响应：`200`，[ServerResponse](API数据结构.md#serverresponse)。

<a id="api-20"></a>

### 20. 更新服务器配置

`PUT /api/v1/servers/{server_id}`

权限：Admin；`resources:manage`。实现：[resources_api.replace_server](../../backend/app/resources_api.py#L135)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `server_id` | path | 是 | string (uuid) | — |

请求体：必填，`application/json`，[ServerInput](API数据结构.md#serverinput)。

成功响应：`200`，[ServerResponse](API数据结构.md#serverresponse)。

<a id="api-21"></a>

### 21. 软删除服务器及其容器资产

`DELETE /api/v1/servers/{server_id}`

权限：Admin；`resources:manage`。实现：[resources_api.delete_server](../../backend/app/resources_api.py#L158)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `server_id` | path | 是 | string (uuid) | — |

请求体：无。

成功响应：`204`，无响应体。

<a id="api-22"></a>

### 22. 测试 SSH 连接

`POST /api/v1/servers/{server_id}/test-ssh`

权限：Admin；`resources:manage`。实现：[resources_api.test_ssh](../../backend/app/resources_api.py#L169)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `server_id` | path | 是 | string (uuid) | — |

请求体：无。

成功响应：`200`，`{success: boolean, status: "ok"/"host_key_mismatch"/"authentication_failed"/"connection_failed", latency_ms: number}`。

<a id="api-23"></a>

### 23. Prometheus HTTP 服务发现

`GET /api/v1/monitoring/targets`

权限：服务密钥：monitoring.token。实现：[resources_api.targets](../../backend/app/resources_api.py#L182)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `authorization` | header | 否 | string / null | — |

请求体：无。

成功响应：`200`，Prometheus HTTP SD 数组：`[{targets: string[], labels: object}]`。

<a id="api-24"></a>

### 24. 服务器指标

`GET /api/v1/servers/{server_id}/metrics`

权限：Viewer/Operator/Admin；`resources:read`。实现：[resources_api.server_metrics](../../backend/app/resources_api.py#L251)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `server_id` | path | 是 | string (uuid) | — |
| `metric` | query | 是 | `"host_cpu_percent"`、`"host_memory_percent"`、`"host_disk_percent"`、`"host_load1"`、`"host_iowait_percent"`、`"host_memory_available_bytes"`、`"host_swap_percent"`、`"host_disk_available_bytes"`、`"host_inode_percent"`、`"host_disk_read_bytes"`、`"host_disk_write_bytes"`、`"host_disk_io_wait_seconds"`、`"host_network_receive_errors"`、`"host_network_transmit_errors"`、`"host_network_receive_drops"`、`"host_network_transmit_drops"`、`"http_latency_seconds"`、`"container_restarts_5m"`、`"container_oom_5m"`、`"host_network_receive_bytes"`、`"host_network_transmit_bytes"`、`"container_cpu_percent"`、`"container_memory_bytes"`、`"container_network_receive_bytes"`、`"container_network_transmit_bytes"`、`"target_up"`、`"http_up"`、`"container_up"` | — |
| `start` | query | 否 | string (date-time) / null | — |
| `end` | query | 否 | string (date-time) / null | — |
| `step` | query | 否 | integer | 最小值 `15`；最大值 `86400`；默认 `30` |

请求体：无。

成功响应：`200`，`{resultType: "vector"/"matrix", result: object[], availability: string, reason?: string}`。

<a id="api-25"></a>

### 25. 同步容器资产

`POST /api/v1/servers/{server_id}/sync-containers`

权限：Operator/Admin；`containers:sync`。实现：[resources_api.sync_containers](../../backend/app/resources_api.py#L267)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `server_id` | path | 是 | string (uuid) | — |

请求体：无。

成功响应：`200`，数组（[ContainerResponse](API数据结构.md#containerresponse)）。

<a id="api-26"></a>

### 26. 容器清单指标采集

`GET /api/v1/monitoring/container-metrics`

权限：服务密钥：monitoring.token。实现：[resources_api.inventory_metrics](../../backend/app/resources_api.py#L333)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `authorization` | header | 否 | string / null | — |

请求体：无。

成功响应：`200`，`text/plain; version=0.0.4`：platform_container_up、platform_inventory_up。

<a id="api-27"></a>

### 27. 容器列表

`GET /api/v1/containers`

权限：Viewer/Operator/Admin；`resources:read`。实现：[resources_api.containers](../../backend/app/resources_api.py#L378)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `server_id` | query | 否 | string (uuid) / null | — |
| `status` | query | 否 | string / null | 正则 `"^(running\|stopped)$"` |
| `offset` | query | 否 | integer | 最小值 `0`；默认 `0` |
| `limit` | query | 否 | integer | 最小值 `1`；最大值 `100`；默认 `50` |

请求体：无。

成功响应：`200`，数组（[ContainerResponse](API数据结构.md#containerresponse)）。

<a id="api-28"></a>

### 28. 容器详情

`GET /api/v1/containers/{container_id}`

权限：Viewer/Operator/Admin；`resources:read`。实现：[resources_api.container_detail](../../backend/app/resources_api.py#L399)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `container_id` | path | 是 | string (uuid) | — |

请求体：无。

成功响应：`200`，[ContainerResponse](API数据结构.md#containerresponse)。

<a id="api-29"></a>

### 29. 软删除容器资产

`DELETE /api/v1/containers/{container_id}`

权限：Admin；`resources:manage`。实现：[resources_api.retire_container](../../backend/app/resources_api.py#L406)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `container_id` | path | 是 | string (uuid) | — |

请求体：无。

成功响应：`204`，无响应体。

<a id="api-30"></a>

### 30. 容器指标

`GET /api/v1/containers/{container_id}/metrics`

权限：Viewer/Operator/Admin；`resources:read`。实现：[resources_api.container_metrics](../../backend/app/resources_api.py#L414)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `container_id` | path | 是 | string (uuid) | — |
| `metric` | query | 是 | `"host_cpu_percent"`、`"host_memory_percent"`、`"host_disk_percent"`、`"host_load1"`、`"host_iowait_percent"`、`"host_memory_available_bytes"`、`"host_swap_percent"`、`"host_disk_available_bytes"`、`"host_inode_percent"`、`"host_disk_read_bytes"`、`"host_disk_write_bytes"`、`"host_disk_io_wait_seconds"`、`"host_network_receive_errors"`、`"host_network_transmit_errors"`、`"host_network_receive_drops"`、`"host_network_transmit_drops"`、`"http_latency_seconds"`、`"container_restarts_5m"`、`"container_oom_5m"`、`"host_network_receive_bytes"`、`"host_network_transmit_bytes"`、`"container_cpu_percent"`、`"container_memory_bytes"`、`"container_network_receive_bytes"`、`"container_network_transmit_bytes"`、`"target_up"`、`"http_up"`、`"container_up"` | — |
| `start` | query | 否 | string (date-time) / null | — |
| `end` | query | 否 | string (date-time) / null | — |
| `step` | query | 否 | integer | 最小值 `15`；最大值 `86400`；默认 `30` |

请求体：无。

成功响应：`200`，`{resultType: "vector"/"matrix", result: object[], availability: string, reason?: string}`。

<a id="api-31"></a>

### 31. 下载采集器 Compose 模板

`GET /api/v1/servers/{server_id}/collectors-compose`

权限：Admin；`resources:manage`。实现：[resources_api.collectors_compose](../../backend/app/resources_api.py#L433)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `server_id` | path | 是 | string (uuid) | — |

请求体：无。

成功响应：`200`，`application/yaml`：采集器 Docker Compose 模板文本。

<a id="api-32"></a>

### 32. 告警规则列表

`GET /api/v1/alert-rules`

权限：Viewer/Operator/Admin；`alerts:read`。实现：[alerts_api.rules](../../backend/app/alerts_api.py#L44)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `offset` | query | 否 | integer | 最小值 `0`；默认 `0` |
| `limit` | query | 否 | integer | 最小值 `1`；最大值 `100`；默认 `50` |

请求体：无。

成功响应：`200`，数组（[RuleResponse](API数据结构.md#ruleresponse)）。

<a id="api-33"></a>

### 33. 创建告警规则

`POST /api/v1/alert-rules`

权限：Admin；`rules:manage`。实现：[alerts_api.create_rule](../../backend/app/alerts_api.py#L60)。

路径外无查询参数或 Header 参数声明。

请求体：必填，`application/json`，[RuleInput](API数据结构.md#ruleinput)。

成功响应：`201`，[RuleResponse](API数据结构.md#ruleresponse)。

<a id="api-34"></a>

### 34. 替换告警规则

`PUT /api/v1/alert-rules/{rule_id}`

权限：Admin；`rules:manage`。实现：[alerts_api.replace_rule](../../backend/app/alerts_api.py#L71)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `rule_id` | path | 是 | string (uuid) | — |

请求体：必填，`application/json`，[RuleInput](API数据结构.md#ruleinput)。

成功响应：`200`，[RuleResponse](API数据结构.md#ruleresponse)。

<a id="api-35"></a>

### 35. 软删除告警规则

`DELETE /api/v1/alert-rules/{rule_id}`

权限：Admin；`rules:manage`。实现：[alerts_api.delete_rule](../../backend/app/alerts_api.py#L85)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `rule_id` | path | 是 | string (uuid) | — |

请求体：无。

成功响应：`204`，无响应体。

<a id="api-36"></a>

### 36. 应用规则并重载 Prometheus

`POST /api/v1/monitoring/apply-rules`

权限：Admin；`rules:manage`。实现：[alerts_api.synchronize_rules](../../backend/app/alerts_api.py#L92)。

路径外无查询参数或 Header 参数声明。

请求体：无。

成功响应：`200`，`{applied: integer}`。

<a id="api-37"></a>

### 37. 接收 Alertmanager 通知

`POST /api/v1/hooks/alertmanager`

权限：服务密钥：alertmanager.token。实现：[alerts_api.webhook](../../backend/app/alerts_api.py#L105)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `authorization` | header | 否 | string / null | — |

请求体：必填，`application/json`，[WebhookPayload](API数据结构.md#webhookpayload)。

成功响应：`200`，`{accepted: true, received: integer, changed: integer}`。

<a id="api-38"></a>

### 38. 告警事件列表

`GET /api/v1/alerts`

权限：Viewer/Operator/Admin；`alerts:read`。实现：[alerts_api.alerts](../../backend/app/alerts_api.py#L178)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `status` | query | 否 | string / null | 正则 `"^(firing\|resolved)$"` |
| `server_id` | query | 否 | string (uuid) / null | — |
| `llm_endpoint_id` | query | 否 | string (uuid) / null | — |
| `prediction_task_id` | query | 否 | string (uuid) / null | — |
| `incident_id` | query | 否 | string (uuid) / null | — |
| `offset` | query | 否 | integer | 最小值 `0`；默认 `0` |
| `limit` | query | 否 | integer | 最小值 `1`；最大值 `100`；默认 `50` |

请求体：无。

成功响应：`200`，数组（[AlertResponse](API数据结构.md#alertresponse)）。

<a id="api-39"></a>

### 39. 告警事件详情

`GET /api/v1/alerts/{alert_id}`

权限：Viewer/Operator/Admin；`alerts:read`。实现：[alerts_api.alert_detail](../../backend/app/alerts_api.py#L206)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `alert_id` | path | 是 | string (uuid) | — |

请求体：无。

成功响应：`200`，[AlertResponse](API数据结构.md#alertresponse)。

<a id="api-40"></a>

### 40. 人工确认告警

`POST /api/v1/alerts/{alert_id}/ack`

权限：Operator/Admin；`alerts:ack`。实现：[alerts_api.acknowledge](../../backend/app/alerts_api.py#L211)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `alert_id` | path | 是 | string (uuid) | — |

请求体：必填，`application/json`，[AcknowledgeInput](API数据结构.md#acknowledgeinput)。

成功响应：`200`，[AlertResponse](API数据结构.md#alertresponse)。

<a id="api-41"></a>

### 41. LLM Endpoint 列表

`GET /api/v1/llm-endpoints`

权限：Viewer/Operator/Admin；`llm:read`。实现：[llm_api.endpoints](../../backend/app/llm_api.py#L38)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `offset` | query | 否 | integer | 最小值 `0`；默认 `0` |
| `limit` | query | 否 | integer | 最小值 `1`；最大值 `100`；默认 `50` |

请求体：无。

成功响应：`200`，数组（[EndpointResponse](API数据结构.md#endpointresponse)）。

<a id="api-42"></a>

### 42. 创建 LLM Endpoint

`POST /api/v1/llm-endpoints`

权限：Admin；`llm:manage`。实现：[llm_api.create_endpoint](../../backend/app/llm_api.py#L57)。

路径外无查询参数或 Header 参数声明。

请求体：必填，`application/json`，[EndpointCreate](API数据结构.md#endpointcreate)。

提交独立 `api_key` 或选择 `agent_channel_id`。关联后，每次探测读取通道当前的 Key；通道停用、删除或地址不匹配时，探测返回 `credential_error`，不向上游发送请求。

成功响应：`201`，[EndpointResponse](API数据结构.md#endpointresponse)。

<a id="api-43"></a>

### 43. LLM Endpoint 详情

`GET /api/v1/llm-endpoints/{endpoint_id}`

权限：Viewer/Operator/Admin；`llm:read`。实现：[llm_api.endpoint_detail](../../backend/app/llm_api.py#L72)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `endpoint_id` | path | 是 | string (uuid) | — |

请求体：无。

成功响应：`200`，[EndpointResponse](API数据结构.md#endpointresponse)。

<a id="api-44"></a>

### 44. 替换 LLM Endpoint 配置

`PUT /api/v1/llm-endpoints/{endpoint_id}`

权限：Admin；`llm:manage`。实现：[llm_api.update_endpoint](../../backend/app/llm_api.py#L77)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `endpoint_id` | path | 是 | string (uuid) | — |

请求体：必填，`application/json`，[EndpointInput](API数据结构.md#endpointinput)。

成功响应：`200`，[EndpointResponse](API数据结构.md#endpointresponse)。

<a id="api-45"></a>

### 45. 软删除 LLM Endpoint

`DELETE /api/v1/llm-endpoints/{endpoint_id}`

权限：Admin；`llm:manage`。实现：[llm_api.delete_endpoint](../../backend/app/llm_api.py#L121)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `endpoint_id` | path | 是 | string (uuid) | — |

请求体：无。

成功响应：`204`，无响应体。

<a id="api-46"></a>

### 46. 手动推理探测

`POST /api/v1/llm-endpoints/{endpoint_id}/probe`

权限：Operator/Admin；`llm:probe`。实现：[llm_api.probe_endpoint](../../backend/app/llm_api.py#L131)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `endpoint_id` | path | 是 | string (uuid) | — |

请求体：无。

成功响应：`200`，[ProbeResult](API数据结构.md#proberesult)。

<a id="api-47"></a>

### 47. LLM 探测指标采集

`GET /api/v1/monitoring/llm-metrics`

权限：服务密钥：monitoring.token。实现：[llm_api.export_metrics](../../backend/app/llm_api.py#L136)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `authorization` | header | 否 | string / null | — |

请求体：无。

成功响应：`200`，`text/plain; version=0.0.4`：LLM 探测 Prometheus 指标。

<a id="api-48"></a>

### 48. LLM 时序指标

`GET /api/v1/llm-endpoints/{endpoint_id}/metrics`

权限：Viewer/Operator/Admin；`llm:read`。实现：[llm_api.endpoint_metrics](../../backend/app/llm_api.py#L146)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `endpoint_id` | path | 是 | string (uuid) | — |
| `metric` | query | 否 | `"llm_up"`、`"llm_latency_seconds"`、`"llm_success_rate"`、`"llm_latency_avg_seconds"`、`"llm_latency_p95_seconds"`、`"llm_error_rate"`、`"llm_http_status"`、`"llm_authentication_failed"`、`"llm_insufficient_balance"`、`"llm_rate_limited"`、`"llm_service_error"`、`"llm_probe_stale"` | 默认 `"llm_success_rate"` |
| `start` | query | 否 | string (date-time) / null | — |
| `end` | query | 否 | string (date-time) / null | — |
| `step` | query | 否 | integer | 最小值 `15`；最大值 `86400`；默认 `30` |

请求体：无。

成功响应：`200`，`{metric: string, status: string, resultType: "vector"/"matrix", result: object[]}`。

### LLM 近 30 分钟状态

`GET /api/v1/llm-endpoints/{endpoint_id}/status`

权限：`llm:read`，按当前端点执行资源范围校验。只读查询，不发起推理请求。

成功响应：`200`，`LLMStatusResponse`。返回 `source="active_probe"`、当前端点 `status`、窗口 `start/end`、`updated_at`、`last_request_at`、`request_count`、`success_count`、`success_rate` 及 60 个 `buckets`。每个桶包含半开区间 `start/end` 和探测总数、成功数；每桶 30 秒，覆盖近 30 分钟。成功率为 0–1，无记录时为 `null`。

统计复用 `model_usage` 中当前端点、当前探测配置生效后的 `llm_probe` 完成记录，排除软删除记录；包含定时与手动探测，不混入平台 Agent 调用或真实业务流量。次数直接计数，不使用 Prometheus 插值估算。原有 5 分钟滑动窗口时序指标保持原口径。

<a id="api-49"></a>

### 49. 预测任务列表

`GET /api/v1/prediction-tasks`

权限：Viewer/Operator/Admin；`predictions:read`。实现：[prediction_api.tasks](../../backend/app/prediction_api.py#L44)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `offset` | query | 否 | integer | 最小值 `0`；默认 `0` |
| `limit` | query | 否 | integer | 最小值 `1`；最大值 `100`；默认 `50` |

请求体：无。

成功响应：`200`，数组（[TaskResponse](API数据结构.md#taskresponse)）。

<a id="api-50"></a>

### 50. 创建预测任务

`POST /api/v1/prediction-tasks`

权限：Admin；`predictions:manage`。实现：[prediction_api.create_task](../../backend/app/prediction_api.py#L63)。

路径外无查询参数或 Header 参数声明。

请求体：必填，`application/json`，[TaskInput](API数据结构.md#taskinput)。

成功响应：`201`，[TaskResponse](API数据结构.md#taskresponse)。

<a id="api-51"></a>

### 51. 预测任务详情

`GET /api/v1/prediction-tasks/{task_id}`

权限：Viewer/Operator/Admin；`predictions:read`。实现：[prediction_api.task_detail](../../backend/app/prediction_api.py#L70)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `task_id` | path | 是 | string (uuid) | — |

请求体：无。

成功响应：`200`，[TaskResponse](API数据结构.md#taskresponse)。

<a id="api-52"></a>

### 52. 替换预测任务

`PUT /api/v1/prediction-tasks/{task_id}`

权限：Admin；`predictions:manage`。实现：[prediction_api.replace_task](../../backend/app/prediction_api.py#L75)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `task_id` | path | 是 | string (uuid) | — |

请求体：必填，`application/json`，[TaskInput](API数据结构.md#taskinput)。

成功响应：`200`，[TaskResponse](API数据结构.md#taskresponse)。

<a id="api-53"></a>

### 53. 软删除预测任务

`DELETE /api/v1/prediction-tasks/{task_id}`

权限：Admin；`predictions:manage`。实现：[prediction_api.delete_task](../../backend/app/prediction_api.py#L87)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `task_id` | path | 是 | string (uuid) | — |

请求体：无。

成功响应：`204`，无响应体。

<a id="api-54"></a>

### 54. 发起一次预测

`POST /api/v1/prediction-tasks/{task_id}/run`

权限：Operator/Admin；`predictions:run`。实现：[prediction_api.run_task](../../backend/app/prediction_api.py#L96)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `task_id` | path | 是 | string (uuid) | — |

请求体：无。

成功响应：`202`，[ResultResponse](API数据结构.md#resultresponse)。

<a id="api-55"></a>

### 55. 预测结果列表

`GET /api/v1/predictions`

权限：Viewer/Operator/Admin；`predictions:read`。实现：[prediction_api.results](../../backend/app/prediction_api.py#L101)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `task_id` | query | 否 | string (uuid) / null | — |
| `server_id` | query | 否 | string (uuid) / null | — |
| `container_id` | query | 否 | string (uuid) / null | — |
| `llm_endpoint_id` | query | 否 | string (uuid) / null | — |
| `status` | query | 否 | `"queued"`、`"running"`、`"completed"`、`"failed"`、`"interrupted"` / null | — |
| `start` | query | 否 | string (date-time) / null | — |
| `end` | query | 否 | string (date-time) / null | — |
| `offset` | query | 否 | integer | 最小值 `0`；默认 `0` |
| `limit` | query | 否 | integer | 最小值 `1`；最大值 `100`；默认 `50` |

请求体：无。

成功响应：`200`，数组（[ResultResponse](API数据结构.md#resultresponse)）。

<a id="api-56"></a>

### 56. 预测结果详情

`GET /api/v1/predictions/{result_id}`

权限：Viewer/Operator/Admin；`predictions:read`。实现：[prediction_api.result_detail](../../backend/app/prediction_api.py#L142)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `result_id` | path | 是 | string (uuid) | — |

请求体：无。

成功响应：`200`，[ResultResponse](API数据结构.md#resultresponse)。

<a id="api-57"></a>

### 57. 预测风险与异常指标采集

`GET /api/v1/monitoring/prediction-metrics`

权限：服务密钥：monitoring.token。实现：[prediction_api.metrics](../../backend/app/prediction_api.py#L149)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `authorization` | header | 否 | string / null | — |

请求体：无。

成功响应：`200`，`text/plain; version=0.0.4`：预测风险、异常及可用性指标。

<a id="api-58"></a>

### 58. 事故列表

`GET /api/v1/incidents`

权限：Viewer/Operator/Admin；`incidents:read`。实现：[incident_api.incidents](../../backend/app/incident_api.py#L49)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `status` | query | 否 | `"open"`、`"in_progress"`、`"recovered"`、`"closed"`、`"merged"` / null | — |
| `severity` | query | 否 | string / null | 正则 `"^(warning\|critical)$"` |
| `assignee_id` | query | 否 | string (uuid) / null | — |
| `server_id` | query | 否 | string (uuid) / null | — |
| `llm_endpoint_id` | query | 否 | string (uuid) / null | — |
| `start` | query | 否 | string (date-time) / null | — |
| `end` | query | 否 | string (date-time) / null | — |
| `offset` | query | 否 | integer | 最小值 `0`；默认 `0` |
| `limit` | query | 否 | integer | 最小值 `1`；最大值 `100`；默认 `50` |

请求体：无。

成功响应：`200`，数组（[IncidentResponse](API数据结构.md#incidentresponse)）。

<a id="api-59"></a>

### 59. 可选负责人列表

`GET /api/v1/incidents/assignees`

权限：Viewer/Operator/Admin；`incidents:read`。实现：[incident_api.assignees](../../backend/app/incident_api.py#L88)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `offset` | query | 否 | integer | 最小值 `0`；默认 `0` |
| `limit` | query | 否 | integer | 最小值 `1`；最大值 `100`；默认 `50` |

请求体：无。

成功响应：`200`，数组（[AssigneeResponse](API数据结构.md#assigneeresponse)）。

<a id="api-60"></a>

### 60. 事故详情及最近时间线

`GET /api/v1/incidents/{incident_id}`

权限：Viewer/Operator/Admin；`incidents:read`。实现：[incident_api.detail](../../backend/app/incident_api.py#L102)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `incident_id` | path | 是 | string (uuid) | — |

请求体：无。

成功响应：`200`，[IncidentDetail](API数据结构.md#incidentdetail)。

<a id="api-61"></a>

### 61. 修改标题或负责人

`PATCH /api/v1/incidents/{incident_id}`

权限：Operator/Admin；`incidents:handle`。实现：[incident_api.update](../../backend/app/incident_api.py#L166)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `incident_id` | path | 是 | string (uuid) | — |

请求体：必填，`application/json`，[IncidentUpdate](API数据结构.md#incidentupdate)。

成功响应：`200`，[IncidentResponse](API数据结构.md#incidentresponse)。

<a id="api-62"></a>

### 62. 事故关联告警

`GET /api/v1/incidents/{incident_id}/alerts`

权限：Viewer/Operator/Admin；`incidents:read`。实现：[incident_api.alerts](../../backend/app/incident_api.py#L135)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `incident_id` | path | 是 | string (uuid) | — |
| `offset` | query | 否 | integer | 最小值 `0`；默认 `0` |
| `limit` | query | 否 | integer | 最小值 `1`；最大值 `100`；默认 `50` |

请求体：无。

成功响应：`200`，数组（[AlertResponse](API数据结构.md#alertresponse)）。

<a id="api-63"></a>

### 63. 事故完整时间线分页

`GET /api/v1/incidents/{incident_id}/timeline`

权限：Viewer/Operator/Admin；`incidents:read`。实现：[incident_api.timeline](../../backend/app/incident_api.py#L152)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `incident_id` | path | 是 | string (uuid) | — |
| `offset` | query | 否 | integer | 最小值 `0`；默认 `0` |
| `limit` | query | 否 | integer | 最小值 `1`；最大值 `100`；默认 `50` |

请求体：无。

成功响应：`200`，数组（[TimelineResponse](API数据结构.md#timelineresponse)）。

<a id="api-64"></a>

### 64. 人工变更事故状态

`POST /api/v1/incidents/{incident_id}/status`

权限：Operator/Admin；`incidents:handle`。实现：[incident_api.status](../../backend/app/incident_api.py#L201)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `incident_id` | path | 是 | string (uuid) | — |

请求体：必填，`application/json`，[StatusInput](API数据结构.md#statusinput)。

成功响应：`200`，[IncidentResponse](API数据结构.md#incidentresponse)。

<a id="api-65"></a>

### 65. 添加事故评论

`POST /api/v1/incidents/{incident_id}/comments`

权限：Operator/Admin；`incidents:handle`。实现：[incident_api.comment](../../backend/app/incident_api.py#L239)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `incident_id` | path | 是 | string (uuid) | — |

请求体：必填，`application/json`，[CommentInput](API数据结构.md#commentinput)。

成功响应：`201`，[TimelineResponse](API数据结构.md#timelineresponse)。

<a id="api-66"></a>

### 66. 将当前事故合并至目标事故

`POST /api/v1/incidents/{incident_id}/merge`

权限：Operator/Admin；`incidents:handle`。实现：[incident_api.merge](../../backend/app/incident_api.py#L273)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `incident_id` | path | 是 | string (uuid) | — |

请求体：必填，`application/json`，[MergeInput](API数据结构.md#mergeinput)。

成功响应：`200`，[IncidentResponse](API数据结构.md#incidentresponse)。

<a id="api-67"></a>

### 67. 发起或复用 AI 诊断

`POST /api/v1/incidents/{incident_id}/analyze`

权限：Operator/Admin；`analyses:run`。实现：[analysis_api.analyze](../../backend/app/analysis_api.py#L19)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `incident_id` | path | 是 | string (uuid) | — |

请求体：必填，`application/json`，[AnalyzeInput](API数据结构.md#analyzeinput)。

成功响应：`202`，[AnalysisResponse](API数据结构.md#analysisresponse)。

复用已完成且有效的缓存诊断返回 `200`；其他情形返回 `202`。

<a id="api-68"></a>

### 68. 事故最新诊断

`GET /api/v1/incidents/{incident_id}/analysis`

权限：Viewer/Operator/Admin；`analyses:read`。实现：[analysis_api.current](../../backend/app/analysis_api.py#L27)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `incident_id` | path | 是 | string (uuid) | — |

请求体：无。

成功响应：`200`，[AnalysisResponse](API数据结构.md#analysisresponse)。

<a id="api-69"></a>

### 69. 事故诊断历史

`GET /api/v1/incidents/{incident_id}/analyses`

权限：Viewer/Operator/Admin；`analyses:read`。实现：[analysis_api.history](../../backend/app/analysis_api.py#L36)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `incident_id` | path | 是 | string (uuid) | — |
| `offset` | query | 否 | integer | 最小值 `0`；默认 `0` |
| `limit` | query | 否 | integer | 最小值 `1`；最大值 `100`；默认 `50` |

请求体：无。

成功响应：`200`，数组（[AnalysisResponse](API数据结构.md#analysisresponse)）。

<a id="api-70"></a>

### 70. 诊断详情

`GET /api/v1/analyses/{analysis_id}`

权限：Viewer/Operator/Admin；`analyses:read`。实现：[analysis_api.detail](../../backend/app/analysis_api.py#L54)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `analysis_id` | path | 是 | string (uuid) | — |

请求体：无。

成功响应：`200`，[AnalysisResponse](API数据结构.md#analysisresponse)。

<a id="api-71"></a>

### 71. 知识文档列表

`GET /api/v1/knowledge`

权限：Viewer/Operator/Admin；`knowledge:read`。实现：[knowledge_api.documents](../../backend/app/knowledge_api.py#L23)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `offset` | query | 否 | integer | 最小值 `0`；默认 `0` |
| `limit` | query | 否 | integer | 最小值 `1`；最大值 `100`；默认 `50` |

请求体：无。

成功响应：`200`，数组（[DocumentResponse](API数据结构.md#documentresponse)）。

<a id="api-72"></a>

### 72. 创建文档并排队索引

`POST /api/v1/knowledge`

权限：Admin；`knowledge:manage`。实现：[knowledge_api.create_document](../../backend/app/knowledge_api.py#L45)。

路径外无查询参数或 Header 参数声明。

请求体：必填，`application/json`，[DocumentInput](API数据结构.md#documentinput)。

成功响应：`202`，[DocumentResponse](API数据结构.md#documentresponse)。

<a id="api-73"></a>

### 73. 知识向量检索

`GET /api/v1/knowledge/search`

权限：Viewer/Operator/Admin；`knowledge:read`。实现：[knowledge_api.retrieve](../../backend/app/knowledge_api.py#L34)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `q` | query | 是 | string | 最短 `1`；最长 `1000` |
| `tag` | query | 否 | string / null | 最长 `50` |
| `limit` | query | 否 | integer | 最小值 `1`；最大值 `10`；默认 `5` |

请求体：无。

成功响应：`200`，`[{id: UUID, document_id: UUID, version: integer, title: string, source: string, content: string, similarity: number}]`。

<a id="api-74"></a>

### 74. 知识文档详情

`GET /api/v1/knowledge/{document_id}`

权限：Viewer/Operator/Admin；`knowledge:read`。实现：[knowledge_api.document](../../backend/app/knowledge_api.py#L50)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `document_id` | path | 是 | string (uuid) | — |

请求体：无。

成功响应：`200`，[DocumentResponse](API数据结构.md#documentresponse)。

<a id="api-75"></a>

### 75. 替换文档并重新索引

`PUT /api/v1/knowledge/{document_id}`

权限：Admin；`knowledge:manage`。实现：[knowledge_api.replace_document](../../backend/app/knowledge_api.py#L55)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `document_id` | path | 是 | string (uuid) | — |

请求体：必填，`application/json`，[DocumentInput](API数据结构.md#documentinput)。

成功响应：`202`，[DocumentResponse](API数据结构.md#documentresponse)。

<a id="api-76"></a>

### 76. 软删除文档并撤回索引

`DELETE /api/v1/knowledge/{document_id}`

权限：Admin；`knowledge:manage`。实现：[knowledge_api.delete_document](../../backend/app/knowledge_api.py#L79)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `document_id` | path | 是 | string (uuid) | — |

请求体：无。

成功响应：`204`，无响应体。

<a id="api-77"></a>

### 77. 重新建立文档索引

`POST /api/v1/knowledge/{document_id}/reindex`

权限：Admin；`knowledge:manage`。实现：[knowledge_api.reindex](../../backend/app/knowledge_api.py#L67)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `document_id` | path | 是 | string (uuid) | — |

请求体：无。

成功响应：`202`，[DocumentResponse](API数据结构.md#documentresponse)。

<a id="api-78"></a>

### 78. Skill 列表

`GET /api/v1/skills`

权限：Viewer/Operator/Admin；`knowledge:read`。实现：[knowledge_api.skills](../../backend/app/knowledge_api.py#L88)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `offset` | query | 否 | integer | 最小值 `0`；默认 `0` |
| `limit` | query | 否 | integer | 最小值 `1`；最大值 `100`；默认 `50` |

请求体：无。

成功响应：`200`，数组（[SkillResponse](API数据结构.md#skillresponse)）。

<a id="api-79"></a>

### 79. 创建 Skill

`POST /api/v1/skills`

权限：Admin；`knowledge:manage`。实现：[knowledge_api.create_skill](../../backend/app/knowledge_api.py#L99)。

路径外无查询参数或 Header 参数声明。

请求体：必填，`application/json`，[SkillInput](API数据结构.md#skillinput)。

成功响应：`201`，[SkillResponse](API数据结构.md#skillresponse)。

<a id="api-80"></a>

### 80. Skill 详情

`GET /api/v1/skills/{skill_id}`

权限：Viewer/Operator/Admin；`knowledge:read`。实现：[knowledge_api.skill](../../backend/app/knowledge_api.py#L104)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `skill_id` | path | 是 | string (uuid) | — |

请求体：无。

成功响应：`200`，[SkillResponse](API数据结构.md#skillresponse)。

<a id="api-81"></a>

### 81. 替换 Skill

`PUT /api/v1/skills/{skill_id}`

权限：Admin；`knowledge:manage`。实现：[knowledge_api.replace_skill](../../backend/app/knowledge_api.py#L109)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `skill_id` | path | 是 | string (uuid) | — |

请求体：必填，`application/json`，[SkillInput](API数据结构.md#skillinput)。

成功响应：`200`，[SkillResponse](API数据结构.md#skillresponse)。

<a id="api-82"></a>

### 82. 软删除 Skill

`DELETE /api/v1/skills/{skill_id}`

权限：Admin；`knowledge:manage`。实现：[knowledge_api.delete_skill](../../backend/app/knowledge_api.py#L119)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `skill_id` | path | 是 | string (uuid) | — |

请求体：无。

成功响应：`204`，无响应体。

<a id="api-83"></a>

### 83. 可信 Action Skill 目录

`GET /api/v1/action-skills`

权限：Viewer/Operator/Admin；`executions:read`。实现：[execution_api.skills](../../backend/app/execution_api.py#L31)。

路径外无查询参数或 Header 参数声明。

请求体：无。

成功响应：`200`，固定 restart_container v1 定义数组，字段：`code/version/risk/requires_human_approval/parameters/precheck/action/verify/rollback`。

<a id="api-84"></a>

### 84. 从诊断提案申请审批

`POST /api/v1/incidents/{incident_id}/approvals`

权限：Operator/Admin；`executions:run`。实现：[execution_api.request_approval](../../backend/app/execution_api.py#L36)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `incident_id` | path | 是 | string (uuid) | — |

请求体：必填，`application/json`，[ApprovalInput](API数据结构.md#approvalinput)。

成功响应：`201`，[ApprovalResponse](API数据结构.md#approvalresponse)。

<a id="api-85"></a>

### 85. 事故审批列表

`GET /api/v1/incidents/{incident_id}/approvals`

权限：Viewer/Operator/Admin；`executions:read`。实现：[execution_api.approvals](../../backend/app/execution_api.py#L57)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `incident_id` | path | 是 | string (uuid) | — |
| `offset` | query | 否 | integer | 最小值 `0`；默认 `0` |
| `limit` | query | 否 | integer | 最小值 `1`；最大值 `100`；默认 `50` |

请求体：无。

成功响应：`200`，数组（[ApprovalResponse](API数据结构.md#approvalresponse)）。

<a id="api-86"></a>

### 86. 审批详情

`GET /api/v1/approvals/{identifier}`

权限：Viewer/Operator/Admin；`executions:read`。实现：[execution_api.approval](../../backend/app/execution_api.py#L64)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `identifier` | path | 是 | string (uuid) | — |

请求体：无。

成功响应：`200`，[ApprovalResponse](API数据结构.md#approvalresponse)。

<a id="api-87"></a>

### 87. 批准审批

`POST /api/v1/approvals/{identifier}/approve`

权限：Admin；`approvals:decide`。实现：[execution_api.approve](../../backend/app/execution_api.py#L69)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `identifier` | path | 是 | string (uuid) | — |

请求体：必填，`application/json`，[DecisionInput](API数据结构.md#decisioninput)。

成功响应：`200`，[ApprovalResponse](API数据结构.md#approvalresponse)。

<a id="api-88"></a>

### 88. 拒绝审批

`POST /api/v1/approvals/{identifier}/reject`

权限：Admin；`approvals:decide`。实现：[execution_api.reject](../../backend/app/execution_api.py#L76)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `identifier` | path | 是 | string (uuid) | — |

请求体：必填，`application/json`，[DecisionInput](API数据结构.md#decisioninput)。

成功响应：`200`，[ApprovalResponse](API数据结构.md#approvalresponse)。

<a id="api-89"></a>

### 89. 消费审批并排队执行

`POST /api/v1/approvals/{identifier}/execute`

权限：Operator/Admin；`executions:run`。实现：[execution_api.execute](../../backend/app/execution_api.py#L83)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `identifier` | path | 是 | string (uuid) | — |

请求体：可省略，`application/json`，[ExecuteInput](API数据结构.md#executeinput) / null。

成功响应：`202`，[ExecutionResponse](API数据结构.md#executionresponse)。

<a id="api-90"></a>

### 90. 事故执行列表

`GET /api/v1/incidents/{incident_id}/executions`

权限：Viewer/Operator/Admin；`executions:read`。实现：[execution_api.executions](../../backend/app/execution_api.py#L94)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `incident_id` | path | 是 | string (uuid) | — |
| `offset` | query | 否 | integer | 最小值 `0`；默认 `0` |
| `limit` | query | 否 | integer | 最小值 `1`；最大值 `100`；默认 `50` |

请求体：无。

成功响应：`200`，数组（[ExecutionResponse](API数据结构.md#executionresponse)）。

<a id="api-91"></a>

### 91. 执行详情

`GET /api/v1/executions/{identifier}`

权限：Viewer/Operator/Admin；`executions:read`。实现：[execution_api.execution](../../backend/app/execution_api.py#L101)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `identifier` | path | 是 | string (uuid) | — |

请求体：无。

成功响应：`200`，[ExecutionResponse](API数据结构.md#executionresponse)。

<a id="api-92"></a>

### 92. 审计记录列表

`GET /api/v1/audit-logs`

权限：Admin；`audit:read`。实现：[execution_api.audits](../../backend/app/execution_api.py#L106)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `q` | query | 否 | string / null | 按事故标题包含匹配，最长 200 字符；与其他条件同时生效 |
| `incident_id` | query | 否 | string (uuid) / null | — |
| `approval_id` | query | 否 | string (uuid) / null | — |
| `execution_id` | query | 否 | string (uuid) / null | — |
| `report_id` | query | 否 | string (uuid) / null | — |
| `user_id` | query | 否 | string (uuid) / null | — |
| `offset` | query | 否 | integer | 最小值 `0`；默认 `0` |
| `limit` | query | 否 | integer | 最小值 `1`；最大值 `100`；默认 `50` |

请求体：无。

成功响应：`200`，数组（[AuditResponse](API数据结构.md#auditresponse)）。

<a id="api-93"></a>

### 93. 生成或复用复盘报告

`POST /api/v1/incidents/{incident_id}/reports`

权限：Operator/Admin；`reports:run`。实现：[report_api.create](../../backend/app/report_api.py#L38)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `incident_id` | path | 是 | string (uuid) | — |

请求体：必填，`application/json`，[GenerateReport](API数据结构.md#generatereport)。

成功响应：`202`，[ReportResponse](API数据结构.md#reportresponse)。

仅新建 queued 返回 `202`，复用记录或创建 failed 记录返回 `200`。

<a id="api-94"></a>

### 94. 事故复盘报告历史

`GET /api/v1/incidents/{incident_id}/reports`

权限：Viewer/Operator/Admin；`reports:read`。实现：[report_api.history](../../backend/app/report_api.py#L52)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `incident_id` | path | 是 | string (uuid) | — |
| `offset` | query | 否 | integer | 最小值 `0`；默认 `0` |
| `limit` | query | 否 | integer | 最小值 `1`；最大值 `100`；默认 `50` |

请求体：无。

成功响应：`200`，数组（[ReportResponse](API数据结构.md#reportresponse)）。

<a id="api-95"></a>

### 95. 复盘报告详情

`GET /api/v1/reports/{identifier}`

权限：Viewer/Operator/Admin；`reports:read`。实现：[report_api.get_report](../../backend/app/report_api.py#L57)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `identifier` | path | 是 | string (uuid) | — |

请求体：无。

成功响应：`200`，[ReportResponse](API数据结构.md#reportresponse)。

<a id="api-96"></a>

### 96. 编辑复盘草稿

`PUT /api/v1/reports/{identifier}`

权限：Admin；`reports:manage`。实现：[report_api.update](../../backend/app/report_api.py#L76)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `identifier` | path | 是 | string (uuid) | — |

请求体：必填，`application/json`，[EditReport](API数据结构.md#editreport)。

成功响应：`200`，[ReportResponse](API数据结构.md#reportresponse)。

<a id="api-97"></a>

### 97. 复盘修订历史

`GET /api/v1/reports/{identifier}/revisions`

权限：Viewer/Operator/Admin；`reports:read`。实现：[report_api.revisions](../../backend/app/report_api.py#L62)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `identifier` | path | 是 | string (uuid) | — |
| `offset` | query | 否 | integer | 最小值 `0`；默认 `0` |
| `limit` | query | 否 | integer | 最小值 `1`；最大值 `100`；默认 `50` |

请求体：无。

成功响应：`200`，数组（[RevisionResponse](API数据结构.md#revisionresponse)）。

<a id="api-98"></a>

### 98. 审核复盘草稿

`POST /api/v1/reports/{identifier}/review`

权限：Admin；`reports:manage`。实现：[report_api.review_report](../../backend/app/report_api.py#L81)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `identifier` | path | 是 | string (uuid) | — |

请求体：必填，`application/json`，[ReviewReport](API数据结构.md#reviewreport)。

成功响应：`200`，[ReportResponse](API数据结构.md#reportresponse)。

<a id="api-99"></a>

### 99. 导出已审核 PDF 或 Word

`GET /api/v1/reports/{identifier}/export`

权限：Viewer/Operator/Admin；`reports:read`。实现：[report_api.export](../../backend/app/report_api.py#L88)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `identifier` | path | 是 | string (uuid) | — |
| `format` | query | 是 | `"pdf"`、`"docx"` | — |

请求体：无。

成功响应：`200`，PDF：`application/pdf`；Word：`application/vnd.openxmlformats-officedocument.wordprocessingml.document`；二进制响应，带 `Content-Disposition: attachment; filename="postmortem-<id>-v<revision>.<format>"`。

<a id="api-100"></a>

### 100. 人工发起复盘邮件

`POST /api/v1/reports/{identifier}/send`

权限：Admin；`reports:manage`。实现：[report_api.send](../../backend/app/report_api.py#L110)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `identifier` | path | 是 | string (uuid) | — |

请求体：必填，`application/json`，[SendReport](API数据结构.md#sendreport)。

成功响应：`202`，[DeliveryResponse](API数据结构.md#deliveryresponse)。

<a id="api-101"></a>

### 101. 复盘邮件发送记录

`GET /api/v1/reports/{identifier}/deliveries`

权限：Admin；`reports:manage`。实现：[report_api.deliveries](../../backend/app/report_api.py#L115)。

| 参数 | 位置 | 必填 | 类型 / 枚举 | 默认值与约束 |
| --- | --- | --- | --- | --- |
| `identifier` | path | 是 | string (uuid) | — |
| `offset` | query | 否 | integer | 最小值 `0`；默认 `0` |
| `limit` | query | 否 | integer | 最小值 `1`；最大值 `100`；默认 `50` |

请求体：无。

成功响应：`200`，数组（[DeliveryResponse](API数据结构.md#deliveryresponse)）。

## 监控增强首批（2026-10-10）

新增服务器批量摘要、容器健康和固定只读人工诊断 API，历史查询补充断点与可用性。完整字段、权限、状态语义及验收见 [监控增强说明](监控增强说明.md)，OpenAPI 与前端生成类型同步更新。
## 开发源码与运维关联第一阶段

新增应用、源码同步、快照浏览、发布和部署接口。完整路径、权限、状态和配置见[第一阶段说明](开发源码与运维关联第一阶段.md)；请求响应以同目录 OpenAPI JSON 为准。源码工具现已由第二阶段接入 AI 诊断。

上述接口同时支持 SVN HTTPS：应用配置保存 SVN 地址和加密密码，同步可指定数字 `expected_revision`，快照与发布响应返回 SVN 版本。目录、文件、搜索和差异接口复用原路径与权限。


## 开发源码与运维关联第二阶段

现有分析与会话运行响应增加 `source_context`，新增四个受控只读 MCP 源码工具，不新增 HTTP 路径。上下文冻结、降级和证据引用规则见[第二阶段说明](开发源码与运维关联第二阶段.md)。


## 开发源码与运维关联第三阶段

新增六个修复交接接口：事故下生成/列表，交接详情，追加修复结果、追加验证以及 Markdown/JSON 导出。权限、冻结规则与部署说明见[第三阶段说明](开发源码与运维关联第三阶段.md)。

## 服务级只读数据库

新增服务下连接列表和新建、连接详情和修改及软删除、草稿连接测试、缓存结构读取与手动刷新接口。路径、请求响应及部署要求见[服务级只读数据库接入](服务级只读数据库接入.md)与 [OpenAPI](openapi.json)。Viewer/Operator/Admin 按 `resources:read` 查看，Admin 按 `resources:manage` 管理、测试和刷新。业务数据查询仅经诊断工具执行，沿用 `analyses:run` 和自动诊断授权，不提供通用查询 HTTP 接口。

## 八项设计补全

以下资源统一使用 `/api/v1` 前缀、现有分页与错误格式；完整方法和字段以 [OpenAPI](openapi.json) 为准，启用与权限见[实施与验收](../project/八项设计补全实施与验收.md)。

| 能力 | 资源路径 |
| --- | --- |
| 服务治理 | `/business-services/{id}/objectives`、`/operations-policy`、`/impact`（后两项同服务前缀） |
| 事故响应 | `/incidents/{id}/accept`、`/response`、`/relations`（后两项同事故前缀）；`/incident-relations/{id}/decision` |
| 维护与通知 | `/maintenance-windows`、`/alert-inhibitions`、`/notifications`、`/notifications/{id}/read`、`/system/notification-policy` |
| 效果评价 | `/analyses/{id}/reviews`、`/evaluation-cases`、`/evaluation-cases/{id}/replays`、`/evaluation-runs`、`/evaluations/summary`、`/predictions/{id}/outcomes`、`/prediction-outcomes/{id}/incidents` |
| 持续改进 | `/improvement-tasks`、报告下改进项及知识草稿入口、知识文档/Skill 下有效期入口 |
| 平台与灾备 | `/system/components`、`/system/backup-records` |
| 数据管理 | `/system/storage`、`/system/cleanup-plans`、`/archives`、`/system/model-usage` |

策略、整改与清理确认校验版本。回放、通知、改进项导入、灾备登记及清理执行有幂等约束。`/metrics` 使用既有监控令牌鉴权；业务结果与模型用量标签受限，不接受自由 Shell、PromQL 或任意宿主文件路径。

`DELETE /api/v1/notifications/{identifier}` 供登录用户删除自己的单条待办，成功返回 204；不存在、已删除或属于其他用户时返回 404。仅软删除独立 `notifications` 表中的记录并记录审计，不修改关联事故等业务数据，也不影响其他用户的待办。已删除记录从列表、分页总数及未读数量中排除，保留去重键避免同一事件重新生成该待办。

案例收录增加 Admin 专用 `GET /api/v1/analyses/{id}/evaluation-preview`，返回实际冻结材料与哈希；保存时可携带 expected_snapshot_hash 校验预览版本。只接受已完成正式诊断，不接受私有运行 ID。见[诊断链路修复](../project/Agent诊断链路修复与验收.md)。

## Agent 智能巡检

完整接口和状态见[巡检中心](巡检中心.md)，请求响应以当前 OpenAPI 为准，前缀 `/api/v1`。

| 方法与路径 | 用途 |
| --- | --- |
| GET `/inspections/summary` | 全局巡检统计 |
| GET / POST `/inspection-data-sources` | 筛选分页／新增可复用数据源 |
| GET / PUT / DELETE `/inspection-data-sources/{id}` | 详情／版本校验更新／引用保护软删除 |
| POST `/inspection-data-sources/{id}/test` | 实际连接与读取测试 |
| GET `/inspection-data-sources/{id}/plans` | 引用计划分页 |
| GET / POST `/inspection-plans` | 搜索、启停筛选、列表／新建 |
| GET / PUT / DELETE `/inspection-plans/{id}` | 计划详情／编辑／软删除 |
| PUT `/inspection-plans/{id}/state` | 版本校验启停 |
| POST `/inspection-plans/{id}/runs` | 立即执行，暂停时也可调用，返回 202；已有进行中的记录则返回该记录 |
| POST `/inspection-plans/{id}/trigger` | 事件或扫描触发，返回 202 |
| GET `/inspection-runs`、GET `/inspection-runs/{id}` | 批次筛选、分页及详情；列表可传 `has_objects=true/false` 按是否发现检查对象筛选，不传则返回全部；总数与分页按筛选结果计算 |
| POST `/inspection-runs/{id}/cancel` | 停止当前批次 |
| DELETE `/inspection-runs/{id}` | 软删除批次和所属对象，活跃批次须先停止 |
| GET `/inspection-runs/{id}/items` | 对象名称／编号搜索、健康状态筛选及分页 |
| GET `/inspection-items`、GET `/inspection-items/{id}` | 兼容跨批次对象查询及单条详情 |
| DELETE `/inspection-items/{id}` | 软删除单条结果，活跃批次须先停止 |
| GET / POST `/inspection-items/{id}/reviews` | 查询／追加人工复核 |
| GET `/inspection-connections` | 配置计划可选的现有只读数据库连接 |
| GET / POST `/service-databases` | 独立数据库连接列表／新建，服务关联可选 |
| PUT `/service-databases/{id}` | 修改连接；显式 `application_id: null` 解除服务关联，省略保留原关联 |

列表分页沿用数组及 `X-Total-Count`。数据源和计划管理需 `inspections:manage`，运行和取消需 `inspections:run`，复核需 `inspections:review`，读取需 `inspections:read`；资源范围权限同步应用于数据源。事件接口由授权调用方主动调用，不自动订阅外部事件。

## 2026-10-25 权限与资源授权

新增权限目录、角色默认权限修改、资源申请目录、批量申请、审批/拒绝/撤回、授权撤销、访问策略及批量检查接口。`/me` 增加默认权限、权限版本及看板访问状态，原业务接口统一检查对象范围。接口及运行时规则见[权限与资源授权](权限与资源授权.md)，请求响应以更新后的 OpenAPI 为准。

### 2026-10-28 界面简化

- 移除模型价格、预算配置接口及预算提醒；模型调用列表和汇总仅提供请求结果、Token、耗时，不再计算费用或接收币种参数。既有历史表保留，不执行数据删除迁移。
- `/system/operations-audit-logs` 保留分页与 `X-Total-Count`，增加 `actor_username`、`resource_name`；名称来自关联记录当前信息，支持已软删除对象，未改写历史审计内容。空 `user_id` 表示系统任务，用户名缺失不冒充人工账号。可选 `scope=notifications` 在数据库分页前筛选通知策略、维护、抑制及邮件投递记录，返回筛选后的总数；省略时保留全平台操作记录。

### 平台统一发件邮箱

`GET /system/mail-settings`（system:read）、`PUT /system/mail-settings`、`POST /system/mail-settings/test`（users:manage）分别读取、保存平台发件设置并显式发送测试邮件。配置字段和权限见[平台邮箱](平台邮箱.md)。告警和复盘统一使用此发件配置；服务接收人仍由服务响应策略管理。
## 智能巡检第一阶段新增接口（2026-10-29）

沿用 `/api/v1` 前缀、现有巡检权限及服务/用户资源范围。接口契约与生成类型以 [OpenAPI](openapi.json) 为准。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET/POST | `/inspection-http-connections` | 分页查询/创建共享 HTTP 连接，管理权限 |
| PUT/DELETE | `/inspection-http-connections/{id}` | 版本校验更新/引用保护软删除 |
| POST | `/inspection-data-sources/{id}/preview` | 只读预览与下一页游标，不推进任务进度 |
| GET | `/inspection-runs/{id}/check-results` | 分页查询逐对象/集合检查项结果 |
| GET | `/inspection-runs/{id}/traces` | 分页查询工具、代码及输入输出证据 |
| GET | `/inspection-runs/{id}/progress` | 按时间读取整轮运行轨迹、当前检查项和更早步骤游标；`inspections:read` 及原任务资源范围 |

任务创建和更新继续使用 `/inspection-plans`，通过 `check_items` 提交独立规则或 Agent 检查项。配置示例、限制和阶段边界见[第一阶段实施与验收](智能巡检第一阶段实施与验收.md)。

## 智能巡检第二阶段（0041）

新增 POST `/inspection-trials`（当前表单独立试运行，manage + run）、POST `/inspection-plans/{id}/copy`（复制为暂停任务，manage）、POST `/inspection-schedule-preview`（后续最多三次安排，manage）。统一任务可在暂停状态下显式手动执行；旧模式保持原有约束。

GET `/inspection-plans` 增加环境筛选和分组总数/最近报告引用；GET `/inspection-runs` 支持 run_kind 筛选；逐项结果支持 item_id/scope，轨迹支持 check_result_id，数据源列表支持 http_connection_id。全部列表继续采用数组、X-Total-Count 与 offset/limit；资源权限同步应用于试运行、对象、轨迹和证据读取。完整接口与隔离验收见[第二阶段实施与验收](智能巡检第二阶段实施与验收.md)。



## 巡检恢复与留存

任务暂停仅停止后续调度；执行报告支持对发现完整的正式统一任务补检未完成项，使用原观察证据并生成关联报告。接口 `POST /inspection-runs/{id}/resume`。中断识别、补检边界、试运行清理及升级回退见[第三阶段实施与验收](智能巡检第三阶段实施与验收.md)。

## Agent 结果档案

新增 GET /agent-results（按事故或巡检任务检索摘要）、GET /agent-results/{kind}/{id}（分段读取结果、证据、配置和私有对话）、POST /agent-results/{kind}/{id}/knowledge-draft（保存停用知识草稿）。业务读取、私有会话和知识管理权限分别核验。分页及上下文预算见[Agent 结果沉淀与检索](Agent结果沉淀与检索.md)。


## 统一回收站

新增 `GET /api/v1/system/recycle-bin/types`、`GET /api/v1/system/recycle-bin` 与 `POST /api/v1/system/recycle-bin/{kind}/{identifier}/restore`。公共资源恢复仅管理员可操作；私人通知/会话仅原所有者且遵循现有权限。分页使用 `X-Total-Count`；恢复请求绑定 `expected_deleted_at`，关联、状态或唯一性冲突返回 409。范围与恢复行为见[回收站与资源恢复](回收站与资源恢复.md)。

`DELETE /api/v1/system/recycle-bin/{kind}/{identifier}` 永久删除单条已软删除资源，请求体为 `DeleteInput`（`expected_deleted_at`），成功返回 204。权限与恢复一致，关联引用、仍在处理或删除批次变化返回 409。仅清除显式允许的附属明细，不级联清除审批、审计或其他业务记录；不操作远程资源。

## 智能问数阶段 2（0045—0046）

2026-11-06 新增 `/api/v1/askdata` 下的版本、目录、业务模型与定义接口。读取使用 `askdata:read`；构建、启用／停用、编辑、认证和复核使用 `askdata:manage`，并继续核验连接资源范围。

- 版本与构建：`GET /connections/{id}/versions`、`GET /connections/{id}/configuration`、`POST /connections/{id}/versions`（202）、`GET /versions/{id}`、`POST /versions/{id}/publish|retire|cancel`。
- 目录与业务模型：`GET /versions/{id}/catalog`、`/tables`、`/tables/{table_id}`、`/terms`、`/terms/{term_id}`、`/relationships`、`/metrics`。
- 定义与版本对比：`GET|PUT /versions/{id}/definitions`、`POST /versions/{id}/definitions/{definition_id}/resolve`、`GET /versions/{id}/diff?compare={id}`。

列表使用数组与 `offset/limit`；`PUT definitions` 按 `(版本, 对象类型, 对象标识)` 覆盖人工字段，回写当前版本的上游对象。完整字段见 [OpenAPI](openapi.json)，阶段范围与验收见[实施进度](智能问数实施进度.md)。

## 智能问数阶段 3

2026-11-06 新增 `/api/v1/askdata` 下的会话、运行、事件、取消与导出接口，形成中文问数闭环。读取使用 `askdata:read`；创建会话、提交问题和取消运行使用 `askdata:run`，并继续核验连接资源范围与私有会话所有者。

- 会话：`GET /conversations`、`POST /conversations`（201）、`GET /conversations/{id}`（含运行列表）、`DELETE /conversations/{id}`（204，需 `askdata:run` 和连接资源权限，仅所有者可删除）。删除采用软删除，取消该会话中排队／运行的任务；详情、事件和 CSV 随会话隐藏，历史记录与已保存分析保留。停用连接的历史会话仍可删除；删除与新问题提交串行处理。
- 运行：`POST /conversations/{id}/runs`（202，`idempotency_key` 保证重复提交不生成重复运行）、`GET /runs/{id}`（答案、SQL、列类型、数据行、图表描述、引用依据、截断状态、查询时间、执行上下文与步骤事件）、`POST /runs/{id}/cancel`。
- 事件与导出：`GET /runs/{id}/events?after_seq=`、`GET /runs/{id}/stream?after_seq=`（SSE，仅重放持久化事件，断线不重新执行查询）、`GET /runs/{id}/export`（CSV，仅包含本次实际返回的授权结果）。

单条 SQL 沿用 5 秒／200 行／128 KiB 限制，问数总体预算默认 180 秒（`ASKDATA_QUERY_BUDGET_SECONDS`），问数并发默认 2（`ASKDATA_QUERY_CONCURRENCY`），构建仍为单队列。完整字段见 [OpenAPI](openapi.json)，阶段范围与验收见[实施进度](智能问数实施进度.md)。

## 智能问数阶段 4（2026-11-07）

新增源码业务规则表 `code_rules`（`0048_askdata_code_rules`）与 Python／Java 规则抽取、代码出处、版本关联、修订／认证／复核／冲突处理。读取使用 `askdata:read`；修订、复核与冲突处理使用 `askdata:manage`，并继续核验连接资源范围。

- 代码依据：`GET /versions/{id}/code-rules/summary`、`GET /versions/{id}/code-rules`（支持 `language`/`kind`/`parse_state`/`review_required`/`conflict_state` 过滤与分页）、`GET /versions/{id}/code-rules/{rule_id}`。
- 修订与复核：`PUT /versions/{id}/code-rules/{rule_id}`（人工修订与认证）、`POST /versions/{id}/code-rules/{rule_id}/resolve`（确认复核）、`POST /versions/{id}/code-rules/{rule_id}/conflict`（冲突处理，可采纳代码口径）。
- 快照来源：`GET /connections/{id}/source-snapshots`（连接下源码快照选项与推荐来源，优先目标环境当前发布版本关联快照）。

构建编排新增 `extracting_source_rules` 步骤；`PlatformDatabase.get_queries` 消费授权静态代码 SQL 片段并送入上游指标表达式生成；问数 `AskDataEvidence` 扩展 `kind="code_rule"` 的出处字段（`path`/`line`/`commit`/`snapshot_id`/`language`/`parse_state`）。完整字段见 [OpenAPI](openapi.json)，阶段范围与验收见[实施进度](智能问数实施进度.md)。

## 智能问数阶段 5（2026-11-07）

新增 `analyses`、`zones`、`tags`、`tag_targets`、`rules`、`sensitive_fields`、`supplements`、`imports` 八张表（`0049_askdata_stage5`，askdata schema 共 48 张），形成分析库、使用统计、完整治理设置与模型导入导出。读取使用 `askdata:read`；执行分析使用 `askdata:run`；新增／修改／删除资产、导入执行使用 `askdata:manage`，并继续核验连接资源范围。

- 分析库：`GET /analyses`（`connection_id`/`search`/`category`/`offset`/`limit`，返回 `items`、`total`、`categories`）、`POST /analyses`（201）、`GET|PUT|DELETE /analyses/{id}`、`POST /analyses/{id}/execute`。保存绑定 `connection_id`、`model_version_id`、`source_run_id`；重跑先取当前启用版本并重新校验授权与向量签名，结果重新脱敏，失败返回 422／502，成功与失败均写审计。
- 模型与执行：`GET|PUT /settings/{connection_id}/execution`（`expected_version` 乐观并发，409 冲突）、`POST /settings/{connection_id}/execution/test`（重排端点测试，通过后写 `reranker_verified_at`）。
- 治理资产：`GET|POST /settings/{connection_id}/zones`、`/tags`、`/rules`、`/sensitive-fields`、`/supplements`，`PUT|DELETE /settings/{connection_id}/{asset}/{id}`。敏感字段策略（`mask`/`exclude`、`keep_prefix`/`keep_suffix`/`hash`/`full`、`sampling` 排除）同时作用于采样 SQL、采样结果、模型输入与结果／CSV 脱敏。
- 导入导出：`GET /settings/{connection_id}/export`（JSON 模型包，`format=sentinelops.askdata.model`，不含凭据、查询原始结果与分析）、`GET /settings/{connection_id}/imports`、`POST /settings/{connection_id}/imports/preview`（逐项 `create|update|skip|conflict` 计划与计数、告警）、`POST /settings/{connection_id}/imports/{import_id}/apply`（`conflict_policy=skip|overwrite`）。
- 统计：`GET /statistics?connection_id=&days=`，返回次数／成功率／平均与 P50／P95 耗时、失败码 Top10、每日时间点、模型调用 token 与认证覆盖；仅聚合，不返回问题、SQL 或结果行。

完整字段见 [OpenAPI](openapi.json)，阶段范围与验收见[实施进度](智能问数实施进度.md)。
