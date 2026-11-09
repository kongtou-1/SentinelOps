# 自动 AI 诊断

2026-10-16。管理员在「系统状态 → AI 自动诊断」设置全局默认，初始为关闭。在告警规则编辑页选择「跟随全局 / 自动诊断 / 仅手动」。规则覆盖即时影响后续自动诊断，不需要重新应用 Prometheus 规则；指标阈值等监控条件的修改仍需显式应用。

## 触发与去重

- Alertmanager 首次送达的 firing 告警完成事故关联后，在同一数据库事务中创建诊断任务；模型调用由现有 ai-worker 完成。
- 同一事故只自动入队一次，重复通知、并发通知、同一事故后续新增告警均不会重复创建自动任务。数据库部分唯一索引再次约束自动任务数量。
- 已有正式诊断任务的事故不追加自动任务，包括手动任务、失败和中断的任务。失败后可以人工重新诊断。
- 已有排队、运行或正在取消的人工诊断会话时，不追加自动任务。
- 开关不扫描历史积压；重复送达的旧告警和首次送达的 resolved 告警不触发。已有事故收到新的符合策略的 firing 告警，且尚无正式诊断任务时，可以触发。
- 一个事故包含多条规则时，任何一条新 firing 告警允许自动诊断即可触发；「仅手动」只约束所属规则，不否决其他允许自动诊断的规则。
- 无 rule_id 的告警跟随全局。已停用或已删除规则的告警不触发自动诊断。

## 授权和失败处理

自动任务记录 `trigger=automatic`，授权人取允许自动诊断的规则配置人，或全局配置人；事故时间线中的触发主体为 system，并保存授权人 ID。配置人必须仍为有效 Admin。人工发起及会话采用的正式诊断记录为 `manual`。

诊断工具调用与结果保存时重新验证策略及授权人。关闭适用策略、停用/删除规则、撤销授权人 Admin 权限，或关闭/合并事故，会阻止后续工具调用或结果保存；已发出的模型请求无法通过此开关即时撤回。规则覆盖为「自动诊断」时，关闭全局默认不取消该规则的授权。

模型关闭、配置缺失或入队异常不会回滚告警及事故，时间线写入 `analysis_auto_skipped` 和固定原因。重复旧通知不重新尝试；之后的新告警可以再次尝试入队。已入队的任务失败或中断后不自动重试。

自动任务复用已有只读工具、资产范围、源码上下文冻结、脱敏、模型超时和证据校验。输出诊断与处置提案，重启等操作仍须人工申请、审批及执行。未增加外部邮件发送或远程部署。

## API

| 方法 | 路径 | 权限 | 数据 |
| --- | --- | --- | --- |
| GET | `/api/v1/system/diagnosis-policy` | `system:read`，Operator/Admin | `enabled/configured_by/updated_at/ai_available` |
| PUT | `/api/v1/system/diagnosis-policy` | `rules:manage`，Admin | 输入 `{"enabled": true}`，返回保存后的配置 |

`ai_available` 仅表示模型开关、地址、模型名和凭据文件已配置，不表示已完成外部 Provider 连通性测试。

规则输入增加 `auto_diagnose: bool | null`，默认 null，分别表示 true 自动、false 手动、null 跟随全局；规则响应返回该字段。`AnalysisResponse` 增加 `trigger: manual | automatic`。模型请求配置与人工诊断一致，没有更改 Provider 或 SDK。

## 升级

新增迁移 `0021_auto_diagnosis`：增加全局配置表、规则覆盖及配置人、诊断触发方式、自动任务唯一索引和触发方式检查约束。新表有 UTC 创建/更新/删除时间及 UPDATE 触发器。历史诊断初始化为 manual，历史规则为跟随全局，全局默认关闭。

按 README 的已有升级流程备份、停止 backend 与 worker、构建镜像、运行 migrate，再启动更新后的 backend、ai-worker 和 frontend；就绪检查要求最新迁移。其他使用共同后端镜像的 worker 同步更新。代码交付未自动修改运行中的业务库或部署服务。

## 验证

后续修复：`0022_service_observability` 已补建下文记录的 `llm_service_bindings` 更新时间触发器，包含直接 SQL 更新回归。当前完整后端回归 652 项通过；服务 API 告警的自动诊断入队也完成隔离实测，见[服务可观测性实施与验收](服务可观测性实施与验收.md)。以下保留自动诊断首次交付时的验证记录。

后端回归使用 `POSTGRES_DB=ops_platform_test`。新增测试覆盖全局及规则组合、角色权限、并发/重复通知、历史通知、手动诊断去重、告警持久化隔离、授权撤销、关闭事故、worker 处理、时间触发器和数据库去重约束。worker 测试使用模拟模型返回值，没有发起真实外部模型调用。测试记录见 `.local/auto-diagnosis-tests.xml`。

| 检查 | 结果 |
| --- | --- |
| 最终自动诊断、结构契约及历史迁移定向回归 | 26 passed，其中自动诊断 22 项 |
| 诊断会话、源码证据、审批执行及修复交接等集成回归 | 139 passed |
| 扩大至认证、告警、事故、AI 及数据库通用检查 | 176 passed，1 failed |
| 前端测试 | 102 passed |
| 前端类型检查与生产构建 | 通过 |
| 修改 Python 文件的 Ruff、OpenAPI/模型元数据契约 | 通过 |

扩大检查的失败为既有 `llm_service_bindings` 表没有 `trg_llm_service_bindings_updated_at`；已查询独立测试库确认，`0020_llm_service_bindings` 迁移确实没有创建该触发器。本次新增的 diagnosis_policy 表时间字段、直接 SQL 更新触发器和迁移往返检查均通过。未修改该无关表的迁移，也没有跳过或放宽失败检查。

最终定向及集成记录分别为 `.local/auto-diagnosis-final-tests.xml`、`.local/auto-diagnosis-integration-tests.xml`。运行中的 Docker 服务及业务数据库尚未升级；没有真实外部 AI 联调，也未进行浏览器交互验收。
