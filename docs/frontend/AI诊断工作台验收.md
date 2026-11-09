# AI 诊断工作台验收

验收日期：2026-10-05。实现与操作说明见 [AI 诊断工作台](AI诊断工作台.md)。真实 Harness SDK 0.1.5rc1 配合本地模拟 Provider；未调用真实 DeepSeek、未操作远程服务器、未发送外部邮件。

## 自动检查

相关后端回归 189 项全部通过；最后补充权限失效、上下文超限和超时后，会话测试 17 项再次全部通过。范围包括认证与角色、分页、软删除、UTC 时间字段和更新触发器、迁移回滚重建与 ORM 无差异，以及旧诊断、审批、执行和复盘回归。原始结果为 `.local/workbench-tests.xml`、`.local/workbench-conversation-tests.xml`。

会话验证覆盖：两位用户与 Admin 的所有权、Viewer 限制、降级后的私有读取与工具权限失效、并发发送与采用幂等、停止幂等、采用快照独立且不发布私聊、提案不能作为证明、范围外与伪造证据不能采用、自然语言回复无须候选、SDK 运行中补充真实接收、流式回复在终态前保存、Provider 拒绝、上下文超限、本轮超时、运行中停止、配置变化，以及重启后部分回复和待消费输入保留。

前端 22 项测试通过，覆盖单次共享刷新、退出/认证变化边界、SSE 的 UTF-8/CRLF 跨片段解析、订阅建立与鉴权失效、事件序号去重、真实运行阶段、指标转换和异步查询。TypeScript 检查与 Docker 生产构建通过；未增加依赖，保留现有锁文件。

## 浏览器

使用本机隔离库的 Admin、两位 Operator 和 Viewer 验证：Admin 查看他人只读，无输入/采用按钮；Operator 只返回本人会话；第二位 Operator 先为空会话，并检查 Provider 401 的真实失败提示；Viewer 查看正式诊断与证据，直接访问全屏工作台进入无权页面。

检查嵌入页和全屏页、排队、实时阶段、停止终态、明确继续、刷新恢复、草稿不被更新覆盖、1280/1440/1920/390 视口和窄屏详情切换。各视口页面宽度未溢出，控制台无错误。截图在 `.local/workbench-screenshots/`；1920 的布局由实际 DOM 视口与宽度核对，当前内嵌浏览器截图受面板可见区域限制。

浏览器发现全屏组件省略 Boolean `visible` 时默认 false，造成未订阅而终态只能刷新看到。已显式设默认 true，复验模型阶段、停止终态和草稿保留均通过；订阅成功即显示“实时连接”。工具结果摘要调整为独立省略行，详细输出可展开。

## 真实本地闭环

独立库 `ops_workbench_8cd85963_test`，通过脚本 `backend/scripts/accept_workbench.py --browser` 完成：真实 Docker 停止 → Prometheus/Alertmanager 告警与事故 → SDK 自主只读取证 → 运行中用户补充 → 手动采用 → 拒绝/批准边界 → 人工发起固定重启动作 → OpenSSH/Docker HEALTHCHECK → 监控独立恢复核验 → 关闭 → 冻结证据复盘 → Admin 编辑/退回/审核 → PDF/Word → 显式 Mailpit 收信。

首轮 12 次模型请求、10 次 Agent 工具调用、137 个持久化事件；系统预采集独立记录。Nginx SSE 在运行结束前收到事件，补充由 `agent/inbox/spliced` 确认，助手回复包含补充。游标回放与序号连续性通过，重复发送和采用复用原记录。实际强制重启 ai-worker 后运行变为 interrupted，明确继续新建一轮并完成，已采用诊断不改变。

未批准和已拒绝不能执行；重复执行不重复重启，守卫容器保持不变；重复邮件只收到一封，附件哈希与导出一致。API/执行/复盘 worker 重启后冻结记录、版本和投递状态保留。日志证据不可用时展示真实工具错误，不作为成功证据。

`.local/batch12-acceptance.json`：`status=passed`、`cleanup.complete=true`、`cleanup.errors=[]`、`normal_services_restored=true`、`normal_rules_applied=6`。测试 SSH 私钥、允许目标文件和临时容器已清理；账号与完整记录仅保留在隔离测试库。业务库升级前备份为 `.local/pre-workbench-20261005.dump`。

## 最终部署

业务库迁移为 `0012_analysis_conversations`，共 29 张表；API、前端、PostgreSQL 和四类 worker 健康。8080 仅绑定本机。正常配置恢复，AI 默认关闭；开启模型前按启动说明配置本地模拟或正式 Provider。真实 DeepSeek 的诊断质量和外部 SMTP 尚未联调。原 PPT 未修改。

## 2026-10-18 诊断提交修复部署

诊断提交返回具体无效证据、知识引用或字段错误，允许在原预算内修正重交；失败观测自动写入数据缺口，未修正的提交不能采用。服务器和容器指标默认返回摘要，支持 `detail=raw`。以本次事故保存的三次服务器指标计算，时序返回从 215246 字节降为 48904 字节，减少 77.3%。

独立测试库 `ops_platform_test` 的会话、AI、源码诊断及项目契约回归共 107 项通过，Ruff 检查与格式检查通过。真实 Harness SDK 配合本地模拟 Provider 验证了错误反馈、修正重交、未修正终态和调用预算；本次部署未发起真实 DeepSeek 诊断。

只重建并更新 `backend`、`ai-worker`，两者使用镜像 `sentinelops-backend:diagnosis-20261018`（`0a5cc7140a48`），均为 healthy、重启次数 0。8000 和 8080 的 `/health/ready` 返回 200，事故页面返回 200，经前端访问未登录 `/api/v1/me` 返回预期的 401。容器内已核对修复逻辑及源码哈希。部署时没有活动诊断，本次不需要数据库迁移，业务库保持 `0022_service_observability`，历史诊断未改写。

回滚镜像保留为 `sentinelops-backend:rollback-diagnosis-20261018-backend` 和 `sentinelops-backend:rollback-diagnosis-20261018-ai`，分别对应更新前的后端与 AI worker。
