# Agent Skill 自主巡检

## 使用方式

巡检计划提供两种模式：原有“采集后逐条检查”和新增“Agent Skill 自主巡检”。旧计划默认保留原模式。

1. 在数据源中登记列表、详情等查询 API，开启“授权 Agent 调用查询接口”，填写允许的方法、动态路径模板和动态参数名称。凭据继续配置在加密请求头中。
2. 在 Skill 中填写业务流程、筛选标记、接口用途、判断标准。需要复用 Python 时，在 scripts 中保存 `scripts/name.py` 到代码字符串的映射；也可以让 Agent 临时生成 Python。
3. 新建自主巡检计划，选择列表数据源、其他授权 API、Skill、JSON 业务参数和调度方式。无需配置主从字段关联或逐条规则。
4. 执行时，Agent 读取 Skill 并组织接口调用和计算。列表分页和筛选由 Agent 按 Skill 与实际响应处理，每个项目独立保存结果及证据。

示例 Skill 见 `backend/app/skills/api-project-inspection/SKILL.md`，需要根据实际 API 和业务标准调整，不会自动创建业务计划。

## API 配置示例

列表数据源：

```json
{
  "url": "https://business.example.com/projects",
  "agent_api_enabled": true,
  "request_method": "GET",
  "allowed_query_params": ["page", "pageSize", "status"]
}
```

详情数据源：

```json
{
  "url": "https://business.example.com/api",
  "agent_api_enabled": true,
  "request_method": "GET",
  "path_template": "projects/{id}/detail"
}
```

POST 查询接口需要显式启用 Agent 查询授权，配置 `request_method=POST` 和 `allowed_body_fields`。只允许固定接口和声明的顶层请求体字段；不开放任意 URL、请求头或业务写入工具。管理员应只登记实际只读的查询接口，HTTP 方法本身不能证明只读。

动态路径参数必须是单个路径段，不允许斜线、百分号、查询片段或 `.`/`..`。动态查询和请求体字段必须在数据源白名单内，禁止动态传入凭据。固定查询参数继续使用 query_params。

## Python 工具

`execute_inspection_python` 接受 code 或 script（二选一），以及 input_ids、inputs。

- `data["inputs"]`：本次输入。
- `data["evidence"]`：input_ids 对应的成功工具结果，按证据 ID 映射。
- `result`：程序赋值的 JSON 兼容输出。

每次启动独立容器：非 root、无网络、无宿主机挂载、只读根目录、16 MiB 临时目录、128 MiB 内存、0.5 CPU、16 进程、8 秒墙钟限制、5 秒 CPU 限制、32 KiB 结果。仅提供 Python 标准库，无自动安装依赖功能。脚本临时文件在容器销毁时清除。沙箱服务不可用时明确失败，不回退到宿主机执行。

每轮最多 64 次工具调用，单次 API 响应不超过 64 KiB、请求参数不超过 16 KiB，累计工具证据不超过 512 KiB；自主模式最多返回 200 个项目（同时受计划 max_records 限制），Agent 会话最多 300 秒并受计划总超时约束。超过限制会停止或记录未完成，不能视为全量通过。

## 结果与证据

工具结果持久化后才交给模型，包括证据 ID、时间、数据源版本、请求参数、程序摘要和输出。请求头不进入证据，响应做字段及已配置凭据值脱敏。Skill 版本、计划及数据源版本绑定本轮快照，每次工具操作前后重新核验授权。

Agent 输出 summary、complete、no_matches、evidence_ids 和 findings。每个 finding 包含 record_id、status（pass/fail/unknown）、summary、evidence_ids。平台拒绝伪造、失败证据引用、重复项目或超出数量限制的结果；失败工具调用使本轮 complete=false。模型对业务结论和完整性的判断仍依赖 Skill、接口事实和校验代码，工具协议不能证明模型判断正确。

逐项目结果进入原巡检记录与复核界面，Python 证据保留其输入 API 的来源链。编排记录软删除但保留审计证据。没有匹配项目单独标记，不生成虚构的正常业务项目。不完整流程标记为部分完成，下次调度重新执行流程，不使用旧模式的分页续传。

## 本地部署

本次迁移为 `0036_inspection_agent_skills`，增加计划模式/参数/授权源和 Skill scripts，不新增表，不修改现有业务证据。就绪检查与迁移同步升级。按既有流程先备份业务库与主密钥，停止相关 worker，构建后迁移，再一起启动相同版本的 backend 和 worker。

```powershell
.tools/uv.exe run --project backend python deployment/inspection-sandbox/prepare.py
docker compose -f compose.yaml -f compose.inspection-agent.yaml --profile inspections build
docker compose -f compose.yaml -f compose.inspection-agent.yaml --profile inspections run --rm migrate
docker compose -f compose.yaml -f compose.inspection-agent.yaml --profile inspections up -d
```

保留本机原有 Compose 覆盖文件、profiles 和端口配置，不用上述示例替换本机现有部署参数。第一次构建正常需要获取 Python 基础镜像。无法访问镜像仓库时，可先使用本机已安装的后端 Python 运行时构建沙箱：

```powershell
docker build -f deployment/inspection-sandbox/Dockerfile.offline -t sentinelops-inspection-sandbox:local .
```

离线镜像仅复制 Python 和系统运行库，不复制 `/app`、平台配置或密钥。已构建离线镜像时，后续 compose 操作不要重新构建 sandbox 服务。

沙箱 broker 是可信基础组件，只有它挂载 Docker socket；普通业务 worker、生成的代码均不持有 socket。broker 不发布宿主机端口，使用内部网络和独立令牌认证，协议只接受 code/data，不接受镜像、挂载、命令或 Docker 参数。Docker socket 本身具有较高权限，应按可信平台组件部署；容器隔离也依赖宿主机和 Docker 的安全性，不能等同于虚拟机隔离。[Docker Engine 安全说明](https://docs.docker.com/engine/security/)

宿主机开发测试可把测试 broker 端口绑定到 127.0.0.1，再配置 `INSPECTION_SANDBOX_URL` 和 `INSPECTION_SANDBOX_TOKEN_FILE`。后端测试仍必须使用独立 `_test` 数据库，不对业务库执行测试。

回滚先停自主计划及 worker。恢复业务库备份或在保留新增字段的情况下恢复旧镜像；主动降级至 0035 会移除新增模式及脚本字段，应先导出自主计划和 scripts。后端/worker/前端需同时恢复兼容版本。
