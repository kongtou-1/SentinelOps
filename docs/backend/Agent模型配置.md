# Agent 模型配置

## 使用

系统管理 → Agent 配置，分别管理 Agent 诊断、巡检、复盘、效果评测。自动诊断与对话诊断使用同一套诊断配置。每个模块的第一条启用通道为主用，其余启用通道按列表顺序备用；停用通道不会参与调用。

页面顶部沿用系统管理子导航；内容区通过分段按钮切换四个 Agent 模块，模型通道、调用统计和请求明细随所选模块更新。

每条通道设置名称、API Base URL、模型名、API Key 和请求超时。接口采用 OpenAI Chat Completions 协议，Base URL 例如 `https://api.example.com/v1`，程序在其后追加 `/chat/completions`。模型需要支持 Agent 使用的工具调用和流式响应。配置与连接测试仅 Admin 可操作；查看需要 `system:read`。

Key 加密保存在 PostgreSQL，解密主密钥继续通过 `CREDENTIAL_KEY_FILE` 管理。接口不返回 Key；编辑时留空保留原值。修改、排序、停用和删除使用模块 `expected_version` 防止覆盖，并写入脱敏审计。连接测试会产生少量实际用量，单独归为“连接测试”。

平台 AI 总开关、模块开关和通道开关分别生效。模块显示“模块已启用”时，仍须至少有一条启用且连接测试通过的通道。问数在“问数设置 → 调用配置”选择已有诊断通道，不另存一份密钥。

DeepSeek 官方端点的连接测试显式关闭思考模式，输出上限为 64 token，避免默认思考耗尽短测试额度后误报“输出额度耗尽”；其它兼容端点不附加该参数。依据 [DeepSeek 思考模式说明](https://api-docs.deepseek.com/guides/thinking_mode/)。正常业务任务的推理设置不由连接测试修改。

## 公开源码与新环境配置

API Key 通过页面或上述配置 API 保存，不写入 Python、前端、Compose 或文档。新环境先配置独立的解密主密钥，再在页面配置模型通道；已有数据库及其解密主密钥属于部署数据，不随源码发布。

仓库 `.gitignore` 排除了 `.env`、`.local/`、虚拟环境和构建产物。本机凭据、一次性接入脚本和验收状态放在 `.local/`。上传 GitHub 前检查实际暂存文件，不使用强制添加绕过这些规则；数据卷、数据库备份和包含运行配置的历史交付包不属于公开源码。

“LLM 服务”仍用于被监控业务的模型资源，与平台 Agent 通道分开。知识检索的本地向量模型继续使用原配置。

## 故障切换

每个任务从主用通道开始。发生网络、超时、认证、余额、限流、服务端错误、模型不存在、上下文超限、无效响应或断流时，按顺序尝试后续通道。切换成功后本任务继续使用该通道，新任务重新从主用开始。每轮每条通道最多请求一次，不重复执行已完成工具。

网关暂存并校验当前完整响应，再交给 Harness。前端显示的生成内容是临时投影；断流后的内容标为“已中断”，不作为后续对话上下文。备用重发当前请求及其中已有的对话、工具结果。失败和备用请求均单独记录。

配置变化、授权变化、取消、工具越权、任务总时限耗尽和业务结论验证失败会停止任务。配置变化只影响使用该模块的任务。每通道超时默认 60 秒，且受任务剩余时限限制；没有可用备用或全部尝试失败时明确失败。

## 用量与费用

Agent 配置页默认展示本月各通道调用数、成功/失败数、备用尝试、输入/输出 Token、耗时及明细；可选择时间、通道、实际模型和币种。连接测试独立汇总。数据与成本页也可按模块筛选，包含巡检用量。

Token 只记录供应商返回值。未返回的部分显示未知，不推算为零。数据与成本 → 模型价格，可设置通用模型价格或指定 Agent 通道的价格；按调用发生时有效价格计费，同通道价格优先。价格单位为每百万输入/输出 Token，费用是平台估算，不是供应商账单或余额。历史记录不补造通道归属。

## 升级

1. 备份业务数据库，停止 AI、巡检、复盘等相关 worker。
2. 使用新代码运行 `uv run --project backend alembic -c backend/alembic.ini upgrade head`，升级至 `0037_agent_model_channels`。
3. 运行 `uv run --project backend python -m app.cli.import_agent_config`，从当前 `AI_BASE_URL`、`AI_MODEL`、`AI_KEY_FILE` 向四个模块各导入一个独立主用通道。
4. 更新并启动 API、前端、AI、巡检及复盘 worker。在页面补充各模块备用通道并进行连接测试。

导入不输出密钥，可重复执行；已经配置或主动清空的模块不会被覆盖。导入后模型地址、模型名和 Key 由数据库配置控制；保留 `AI_ENABLED` 总开关及现有任务总时限。此版本不再将环境中的模型配置作为隐式备用。

新增表均含 UTC `created_at`、`updated_at`、`deleted_at`。通道删除为软删除，历史用量和价格仍保留。回退旧版本时，停止相关 worker 并恢复升级前备份；存在通道专属价格时拒绝直接降级，避免丢失价格信息。

## API

- `GET /api/v1/agent-configs`：四个模块及其通道。
- `PUT /api/v1/agent-configs/{module}`：启停模块。
- `POST /api/v1/agent-configs/{module}/channels`：创建通道。
- `PUT /api/v1/agent-configs/{module}/channels/{id}`：修改通道。
- `DELETE /api/v1/agent-configs/{module}/channels/{id}`：软删除通道，请求体传 `expected_version`。
- `PUT /api/v1/agent-configs/{module}/channels/order`：传完整 `channel_ids` 顺序及 `expected_version`。
- `POST /api/v1/agent-configs/{module}/channels/{id}/test`：单通道连接测试，传 `expected_version`。
- 原 `/system/model-usage` 及 `/summary` 增加 `agent_module`、`agent_channel_id`、`model` 筛选。价格接口增加可空 `agent_channel_id`。

模块标识为 `diagnosis`、`inspection`、`report`、`evaluation`。完整字段及响应以 OpenAPI 为准。
