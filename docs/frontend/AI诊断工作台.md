# AI 诊断工作台

事故详情“AI 诊断”内嵌工作台，全屏路径为 `/incidents/{id}/diagnosis`。每人可以建立多个独立会话，默认打开本人最近一次会话。会话列表、对话与工具过程、候选诊断和证据共用同一组件；1280 宽度可以展开右侧详情，1440/1920 显示三栏，窄屏切换会话、对话和详情。

## 操作与权限

- Operator/Admin 新建自己的会话、发送文字或粘贴日志、停止本轮、明确继续、采用候选结论。
- 本人会话支持确认后删除；有排队、运行或停止中的诊断时，先停止并等待本轮结束。删除后从列表移除，完整对话、运行和事件不再可访问；已采用的正式诊断、审批与事故记录保留。已关闭或合并事故的本人会话仍可删除。
- Admin 可切换查看全部用户的完整会话；他人会话只读，不能代发、停止或采用。
- Viewer 只能查看已发布的事故诊断和证据，没有私有会话工作台入口。
- “已保存”表示消息已入库，“等待 Agent 接收”表示已提交 SDK，“已接收”以 `agent/inbox/spliced` 为准。终态后未消费的消息等待下一轮；发送不延长运行预算。
- “采用为事故诊断”创建独立 `AgentAnalysis` 快照。后续聊天和候选修改不改写已采用版本或审批。有效提案才显示申请审批，批准后仍由人工单独执行 `restart_container v1`。
- 普通解释性回复可以结束本轮而没有候选诊断。用户陈述不会变成工具核验证据，私有聊天不自动进入事故时间线或复盘。

工具卡片区分系统预采集和 Agent 自主调用，显示名称、目标、时间、耗时、结果摘要、错误与脱敏输出。证据保留采样时间，历史观测不能冒充当前状态。顶部状态来自模型请求、SDK 状态、工具事件和终态，展示模型、耗时、请求及工具计数。显示的是公开运行事件和可见回复，不读取模型隐藏思考。

“诊断与证据”新增本轮开发资料：告警对应的部署版本、变更说明、固定 Git 或本地参考源码、缺口及可点击文件证据。文件抽屉展示诊断时保存的片段、行号与哈希，代码更新不会改写它。源码未准备好时继续原有运行诊断，不要求补齐配置。版本选择、工具边界和部署挂载见[源码诊断第二阶段说明](../backend/开发源码与运维关联第二阶段.md)。

## 持久化与实时恢复

迁移 `0012_analysis_conversations` 新增 `analysis_conversations`、`analysis_messages`、`analysis_runs`、`analysis_events`。四表均含 UTC 时区的三个时间字段及更新触发器；事件按会话递增序号，先入库再订阅推送。回复片段短暂合并后保存，工具结果和终态立即保存。

同一会话只能有一轮活动运行。消息的 `client_message_id` 防止重复发送启动多轮；采用和停止也支持重复调用。运行中补充通过 SDK `session_prompt` 排队，SDK 接收回执与数据库状态分别记录。每轮从数据库重建完整已提交对话、最近候选、证据和知识版本；Provider 返回容量不足时提示新建会话，不静默删掉历史。

SSE 使用 Bearer 认证的 fetch，复用单次并发令牌刷新。客户端按序号补取、重连和去重；页面隐藏或离开时关闭订阅，服务端继续。回来或刷新后从数据库恢复。Nginx `/api/` 关闭代理缓冲。查看历史不强制滚到底部，更新不覆盖输入。

Harness 维持隔离子进程、`sdk-minimal`、只读 MCP 白名单和单并发 worker。默认预算 180 秒、13 次模型请求、12 次 Agent 工具调用；系统预采集单独标注。网关逐段转发 Provider 响应并采集脱敏回复，`submit_diagnosis` 工具提交结构化候选，不执行动作。停止关闭本轮进程；worker 重启将运行中记录标为中断，需明确继续。事故关闭/合并、发起人权限失效、配置变化阻止继续调用工具。SDK 暂无单会话取消或跨进程加载接口，下一轮依据数据库重建。

候选提交校验失败时，工具返回具体无效证据 ID、知识片段 ID 或字段错误，Agent 可在原有预算内修正后重交。无效提交不生成候选；未修正便结束本轮仍记为失败，越权调用继续立即阻断。工具读取失败保留原始错误记录，并自动写入成功候选的 `data_gaps`，不能作为观测事实引用。

`get_server_metrics` 和 `get_container_metrics` 默认 `detail=summary`：每条曲线保留全部标签、采样数量、无效采样数量，以及带原始采样时间的首值、末值、最小值和最大值；保留数据可用性与过期状态，末值缺失不以旧值填补。需要逐点检查时传 `detail=raw` 获取原有一小时时序；原始采样仍由 Prometheus 保存，已有历史证据不改写。LLM 与业务指标工具维持现有返回。

参考：[官方 Python SDK](https://github.com/deepseek-ai/deepseek-harness/blob/master/python/sdk/README.md)、[SDK 协议](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/sdk/protocol/README.md)。

本次使用的 [sdk-minimal 组合](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/bundle/sdk-minimal/README.md)不启用自动上下文压缩，容量错误在网关明确转为 `context_limit`。

## API

以下路径均加 `/api/v1`；读取须所有者或 Admin，写入只允许所有者且具备 `analyses:run`。Admin 新增权限 `analysis_conversations:read_all`，由 `/me.permissions` 返回。分页列表保留数组并返回 `X-Total-Count`；软删除不返回。

| 方法与路径 | 请求和结果 |
| --- | --- |
| POST `/incidents/{id}/analysis-conversations` | `{title?}`；201 会话详情 |
| GET `/incidents/{id}/analysis-conversations` | `offset/limit`，Admin 可按 `owner_id`；本人列表或全部 |
| GET `/analysis-conversations/{id}` | 创建者、最近状态、当前运行 |
| DELETE `/analysis-conversations/{id}` | 所有者软删除；204，无响应体；有活动运行返回 409 |
| POST `/analysis-conversations/{id}/messages` | `{client_message_id: UUID, content: 1–16000字符}`；202 持久化消息 |
| GET `/analysis-conversations/{id}/messages` | `offset/limit`，按创建时间递增 |
| GET `/analysis-conversations/{id}/runs` | `offset/limit`，最近运行在前 |
| GET `/analysis-conversations/{id}/events` | `after_seq>=0`、`limit<=1000`；递增事件数组 |
| GET `/analysis-conversations/{id}/stream` | `after_seq>=0`；SSE `id:seq`、`event:event`、`data:EventResponse`，带心跳 |
| GET `/analysis-runs/{id}` | 配置快照、状态、证据、引用、候选及采用记录 |
| POST `/analysis-runs/{id}/cancel` | 请求停止，返回真实取消状态 |
| POST `/analysis-runs/{id}/adopt` | 已完成且校验通过才返回独立正式诊断；重复返回原记录 |

`auth_required` 提示重连刷新，`permission_lost` 终止无权订阅，`connection_error` 触发游标重连。200/202 不能代替真实完成状态。非所有者写入返回 403；已关闭事故、失效证据或无有效候选采用返回 409；输入校验返回 422。精确字段见 [OpenAPI](../backend/openapi.json)，前端由此生成类型。

## 启动与复验

正常启动和 AI 凭据配置沿用 [启动说明](启动与开发.md) 和 README。升级已有库先备份，再停止 API/worker、构建镜像、执行迁移并重建服务。AI 默认关闭；本次验收使用真实 SDK 配合本地 HTTP 模拟 Provider，不能据此判断真实 DeepSeek 的诊断质量。

```powershell
docker compose build backend frontend
docker compose run --rm migrate
docker compose --profile monitoring --profile ai --profile execution --profile reports up -d --wait

$env:POSTGRES_DB='ops_platform_test'
$env:REPORT_FONT_FILE=(Resolve-Path .local/report-font/wqy-zenhei.ttc).Path
& backend/.venv/Scripts/python.exe -m pytest backend/tests/test_conversations.py backend/tests/test_ai.py backend/tests/test_backend.py backend/tests/test_executions.py backend/tests/test_reports.py backend/tests/test_console.py -q -p no:cacheprovider
Remove-Item Env:POSTGRES_DB
Remove-Item Env:REPORT_FONT_FILE

& backend/.venv/Scripts/python.exe backend/scripts/accept_workbench.py
```

闭环脚本创建 `ops_workbench_*_test` 独立库，复制本机管理员哈希，真实本地 Docker 故障与 OpenSSH 执行，模拟 Provider 和 Mailpit 收信，最后恢复正常服务与业务库告警规则。记录在 `.local/batch12-acceptance.json`。期间不要并行重建 API/worker。`--browser` 为开发浏览器验收暂停模式，临时账号只存在隔离库，凭据和完成标记保存在 `.local/workbench-browser-*`，不打印或提交。

本次测试、浏览器与部署结果见 [验收记录](AI诊断工作台验收.md)。
