# SentinelOps 项目协作指南

## 适用范围

- 本文件适用于当前仓库。子目录存在 `AGENTS.md` 时，遵循其补充约定；用户最新明确指令优先。
- 使用当前检出的仓库根目录，不依赖历史机器上的绝对路径。
- 先检查现有代码、相关文档和未完成修改，再动手。关键歧义先询问；小歧义采用最小合理假设并说明。
- 只完成当前请求所需修改，保持现有风格；不顺手重构、增加功能或引入框架。
- 发现更简单的方法或实际风险时直接说明。回答与文档简洁、具体，区分实际验证与尚未验证的内容。

## 项目与资料入口

SentinelOps 面向 Docker 部署的 AI 应用，提供“感知—预测—诊断与提案—人工审批—受控执行—验证—复盘”闭环。

| 路径 | 职责 |
| --- | --- |
| `backend/` | Python 后端，项目名 `sentinelops-backend`，应用包 `app` |
| `frontend/` | Vue 3 控制台，目录与开发规范见 `frontend/AGENTS.md` |
| `monitoring/` | Prometheus、Alertmanager 与采集配置 |
| `deployment/` | 部署和交接相关工具 |
| `testlab/` | 本机隔离实测资源与使用说明 |
| `docs/backend/`、`docs/frontend/` | 接口、设计、开发与验收文档 |
| `artifacts/` | 导出包、截图和历史验收产物，不作为源码修改目标 |
| `.local/` | 本机配置与运行资料，不提交凭据或当作交付源码 |

- 启动与部署入口：`README.md`；前端开发：`docs/frontend/启动与开发.md`。
- 后端组织与迁移：`docs/backend/项目组织与迁移.md`。
- API 契约：`docs/backend/API接口汇总.md`、`docs/backend/API数据结构.md`、`docs/backend/openapi.json`。
- 需求依据：`docs/plan.txt` 与 `docs/面向 Docker 部署 AI 应用的人机协同智能预测运维平台：落地技术方案.pdf`。目录、表结构、API 清单和估时是参考，按已实现功能和当前请求调整。
- 既有授权记录：`docs/project/已确认实施范围.md`。保留原授权边界，不因历史记录自动开展当前请求之外的工作。

## 开发与验证

在仓库根目录执行后端命令；优先使用本机已有的 `.tools/uv.exe`，其他环境使用 `uv`：

```powershell
uv sync --project backend --frozen
uv run --project backend uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

前端在 `frontend/` 目录执行，使用 `package.json` 指定的 pnpm 版本：

```powershell
pnpm install --frozen-lockfile
pnpm dev
pnpm test
pnpm build
```

后端测试必须显式指定独立测试库，数据库名以 `_test` 结尾。例如：

```powershell
$env:POSTGRES_DB = 'ops_platform_test'
uv run --project backend python -m pytest backend/tests/test_project_structure.py -q
Remove-Item Env:POSTGRES_DB
```

- 测试连接、初始化和专用配置遵循 `backend/tests/conftest.py`；禁止对业务库运行测试。
- 先运行与修改直接相关的测试、类型检查或构建；失败后定位并修复，不绕过问题。
- 后端结构变更检查 OpenAPI、模型元数据、独立测试库及 worker/子进程入口；前端结构变更检查路由、权限、导入、现有测试及生产构建。
- 后端接口变更时先更新 OpenAPI，再在前端运行 `pnpm api:types`，核对生成类型与调用方。
- 长时间训练或实验确认进程、日志及资源正常后放在后台；只在预计完成、出现异常或用户要求时检查。

## 技术与架构

- 采用模块化单体：FastAPI、SQLAlchemy、PostgreSQL；前端采用 Vue 3、TypeScript、Vite、Vue Router、Pinia、Element Plus、ECharts。
- Python 环境与依赖由 uv 管理，维护 `pyproject.toml` 和 `uv.lock`；前端维护 pnpm 锁文件，不向系统全局安装项目依赖。
- 数据库与基础组件通过 Docker Compose 运行，不在宿主机安装数据库服务。主平台 Compose 项目名为 `sentinelops`，实测项目名为 `sentinelops-testlab`。
- Prometheus 保存原始时序，PostgreSQL 保存资产、事件、预测、决策和审计，不复制整套原始指标。
- 日常采集使用 exporter 与 LLM Probe；SSH 用于初始化、只读诊断和经审批的执行，不用于高频轮询。
- DeepSeek Harness 通过适配层接入，不接管业务；接入或升级时核对官方 SDK、版本与接口。
- 当前不引入 Kubernetes、CI/CD、微服务、多 Agent、多租户或无人值守自动修复；Redis、Celery 按实际需要引入。

## 后端分层

- `app/main.py` 只负责应用创建、生命周期和异常处理。接口由 `app/api/router.py` 注册，具体路由放 `app/api/routes/`，请求依赖放 `app/api/dependencies.py`。
- `core` 保存配置、基础路径和安全定义；`models` 按业务拆分 ORM；`schemas` 保存数据结构；`db/base.py` 定义唯一 Base 与时间字段，`db/session.py` 管理连接和会话。
- `services` 负责业务校验、流程和事务，不导入路由或请求依赖。
- `repositories` 使用传入的 Session 执行数据库操作，不自行提交、回滚，不依赖 API、服务层或 FastAPI。
- `integrations` 保存外部适配；`workers` 保存后台任务入口；`cli` 保存管理命令；`utils` 仅放通用函数；保留现有 `skills`。
- 按业务归类，不向 `app` 根目录增加业务模块，不引入通用仓储基类。ORM 类保留 `app.models` 的公开身份及原有写入排序。
- 结构调整保持接口、权限、事务、审批绑定与恢复行为。项目改名不改变数据库名称、账号、JWT issuer/audience 或密钥，不重写历史证据。

## 数据库规则

- 所有表，包括关联、日志、审计及框架维护表，必须包含 `created_at`、`updated_at`、`deleted_at`。
- 时间使用带时区类型并统一存储 UTC；创建与更新时间非空，创建时初始化；删除时间未删除时为空。
- 修改记录时维护 `updated_at`；业务删除采用软删除，同时更新删除与修改时间。普通业务查询排除已删除记录。
- 唯一约束、关联和保留策略按当前功能设计。审计仅追加，保留时间字段不代表开放编辑或删除接口。
- 模型和迁移变更逐表检查字段、默认值、可空性及更新时间行为；自动生成的迁移也必须检查。

## 权限与执行边界

- Alert、Incident、Approval 分别表示监控事件、业务事故与执行授权，保持分离。
- AI 只能进行受控只读诊断、知识检索及允许的提案，不生成任意 Shell 交给 SSH 执行；诊断 Harness 无直接执行权限。
- 改变被管理服务器状态的操作，包括采集器部署，必须获得明确人工授权。未指定并授权远程目标时，不部署远程采集器或改变远程状态。
- Action Skill 执行以平台持久化审批为事实源。审批绑定具体目标、Skill 版本及参数；执行时核验授权、有效期和执行状态，防止替换与重复消费。
- 使用预定义动作及经资产校验的参数，保留前置检查、结果、恢复验证和审计；独立执行 worker 只消费有效审批。
- medium 风险动作仅 Admin 审批；Operator/Admin 可申请与发起执行。`restart_container` 诊断提案保持不可直接执行。
- 复盘由 Admin 编辑、审核并显式发起发送，不自动发邮件或写入知识库。没有真实凭据及明确收件人时不发送外部邮件。
- 凭据不得明文入库；解密主密钥与数据库分离。代码、日志、接口和提交内容不得暴露 API Key、SSH 私钥或密码。
- 可在既有授权内安装项目依赖、运行本地隔离 Docker、模拟 Provider、迁移及验收；模拟通过不等于真实外部联调通过。不新增自动发布或远程执行权限。

## 文档与写作

- 说明变更内容、验证结果和实际限制；同步更新受影响的开发或部署说明。
- 保留作者原有论点和表达意图，不扩大结论。依据充分时直接下结论，提高信息密度与逻辑清晰度，不堆套话或重复总结。
- UI 通过清楚的标签、状态和布局表达信息，不添加解释性小字。
