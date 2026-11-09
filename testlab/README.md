# 本机实测资源

准备日期：2026-10-14。平台与测试服务均在当前电脑的 Docker Desktop 中运行，无须云服务器或 GPU。

开发验收残留已在备份后清理，当前资源保留。处理清单、恢复依据及未删除的受保护缓存见 [实测前清理记录](../docs/backend/实测前清理记录-20261014.md)。

## 简单 LLM 对话服务（2026-10-30）

页面入口：<http://127.0.0.1:18081>。输入问题后点击“发送”，等待完整回答；每次只发送当前问题，不携带历史上下文，不使用流式响应。

独立服务复用根目录 `.env` 的 `AI_BASE_URL`、`AI_MODEL` 及现有 `.local/ai-secrets/deepseek.key`，密钥仅挂载至服务端。使用平台已有后端镜像，无需安装依赖或修改平台。

```powershell
# 在当前仓库根目录启动 / 停止本服务
docker compose -f compose.llm-chat.yaml up -d --no-deps --wait llm-chat
docker compose -f compose.llm-chat.yaml stop llm-chat
```

| 接口 | 用途 |
| --- | --- |
| `GET /` | 对话页面 |
| `POST /ask` | 请求体 `{"question":"你的问题"}`，返回 `id`、`question`、`answer`、`model` 和时间字段 |
| `GET /health` | 进程健康，不调用模型 |
| `GET /records` | 条件查询问答记录，支持是否已检查、ID、关键词、检查结论、时间与分页 |
| `POST /records/checks` | 单条或批量标记已检查，同时保存每条记录的检查结果 |
| `GET /docs` | 本服务的接口文档 |

成功问答保存在 SQLite 数据库 `.local/llm-chat/chat.sqlite3` 的 `conversations` 表，字段为 `id`、`question`、`answer`、`model`、`created_at`、`updated_at`、`deleted_at`、`checked`、`checked_at`、`check_result`。时间以带时区的 UTC ISO 8601 文本保存。数据库挂载到本机目录，重建或重启容器后保留；失败请求不写入问答记录。升级自动补充检查字段，旧记录和新问答默认未检查；首次升级会自动迁入已有 `records.jsonl`，按 ID 去重，原文件保留但不再追加。

`/records` 返回 `items`、`next_cursor`，每条包含 `id`、`version`、`updated_at`、`facts`，问题和回答位于 `facts.question`、`facts.answer`。时间查询窗口为起点包含、终点排除，分页与时间筛选直接查询数据库。

#### 查询与标记检查结果

`GET /records` 的查询条件可组合使用：

| 参数 | 含义 |
| --- | --- |
| `checked` | `false` 仅未检查，`true` 仅已检查；不传则全部 |
| `record_id` | 精确匹配一条对话 ID |
| `q` | 问题或回答包含的文字，按原文匹配，最长 200 字符 |
| `check_status` | `pass` / `fail` / `unknown`，筛选最近一次检查结论 |
| `updated_after` / `updated_before` | 更新时间范围，必须带时区 |
| `limit` / `cursor` | 每页 1–50 条，默认 50；游标默认 0 |

例如：`GET /records?checked=false&limit=10`。`facts` 包含布尔值 `checked`、`checked_at` 和对象 `check_result`；未检查时后两者为 `null`。

单条或批量回写都使用 `POST /records/checks`，`records` 数组接受 1–100 条，每条分别提供 ID 和评估：

```json
{
  "records": [
    {
      "id": "从查询结果取得的对话ID",
      "result": {
        "status": "pass",
        "summary": "回答切题，内容准确",
        "details": "具体判断依据，可省略"
      }
    }
  ]
}
```

批量时在 `records` 中增加其他 ID 及各自结果即可。`status` 必须为 `pass`、`fail` 或 `unknown`，`summary` 必填，`details` 可选。成功返回 `updated` 数量及 `items`（本批完整对话及已保存检查结果）。原 QA 与创建时间不变，同时更新 `checked=true`、`checked_at` 和 `updated_at`；重复提交同一记录覆盖最近一次检查结果，不保存历次检查历史。`unknown` 也表示本次检查已完成，归入已检查。

同批重复 ID、空批、无效结果返回 422；任一 ID 不存在或已删除返回 404，整批不修改。查询和回写均不调用对话模型，也不新增 QA。

Agent 的处理顺序：查询 `checked=false` → 评估原 QA → 回写结果；只有回写成功才视为已检查。每轮处理完成后重新从 `cursor=0` 查询未检查记录，不延用标记前的分页游标。接口不会锁定或认领待检记录，适用于当前单路串行处理。

巡检连接信息（仅说明，不自动配置）：记录接口 `GET http://testlab-llm-chat:8080/records`，固定查询参数 `checked=false`；回写接口 `POST http://testlab-llm-chat:8080/records/checks`，动态请求体允许字段为 `records`。查询全部待检记录时不要附加狭窄时间窗口，以免旧的待检记录落在窗口外。

平台 Docker 网络内可访问 `http://testlab-llm-chat:8080/records`。巡检由用户自行配置，本服务不创建巡检计划或自动调用模型。SQLite 仅供这个独立小型测试服务使用，无需新增数据库容器或安装依赖。

验证命令（使用独立临时 SQLite 测试库，不连接平台数据库、不调用真实模型）：

```powershell
backend/.venv/Scripts/python.exe -m unittest discover -s testlab/llm_chat -p test_chat.py -v
```

2026-10-30 已通过七项测试，覆盖单轮问答、数据库持久化/分页、输入校验、上游失败、旧数据迁移、条件查询与单条/批量回写、无效批次不部分写入。此前从页面完成一次真实 `deepseek-flash` 问答；本次检查标记接口验收不调用真实模型。

### 一小时 QA 轮询实测

`poll_qa_quality.py` 复用现有「QA 回答质量巡检」任务及其 Skill、查询与回写配置。默认启用平台每 120 秒的周期调度，在第 0、12、24、36、48 分钟分别调用一次真实 `/ask`，一小时后暂停调度，等待已开始的一轮结束，再恢复原先的手动、暂停配置。

```powershell
backend/.venv/Scripts/python.exe -u testlab/poll_qa_quality.py --output .local/llm-chat/polling-20261101
```

运行前要求 QA 服务和平台正常、该任务已暂停且没有活动执行、没有遗留未检查问答。脚本使用本机 `.local/admin-credentials.txt` 登录，不记录凭据；会调用现有真实模型产生费用，不重复提交失败或超时的问题。运行期间不要编辑该任务；版本变化时脚本停止覆盖配置，并在结果中报告冲突。

输出目录必须尚不存在。`progress.json` 保存运行进度；`summary.json`、`qa-final.json`、`runs-final.json` 与 `inspection-evidence.json` 保存最终问答、自动调度、逐项检查及工具轨迹；`plan-before.json` 与 `plan-restored.json` 记录配置恢复。通过条件为五条问答全部回写已检查、恰好五项检查执行完成、至少两轮自动调度且所有执行正常结束、配置恢复成功。问答的质量结论 `pass/fail/unknown` 单独保留，不以回答必须通过作为链路通过条件。强制终止宿主进程或关闭电脑会中断脚本，需人工确认任务已暂停。

## 入口

| 资源 | 入口 / 名称 |
| --- | --- |
| 运维平台 | http://127.0.0.1:8080，使用原管理员账号 |
| 问答业务页面 | http://127.0.0.1:18080 |
| 本地业务请求 | http://127.0.0.1:18080/work |
| 进程健康 / 业务就绪 | `/health` / `/ready` |
| 服务状态 | http://127.0.0.1:18080/status |
| Prometheus | http://127.0.0.1:19090 |
| Alertmanager | http://127.0.0.1:19093，原 9093 被 Windows 保留 |
| 测试邮件箱 | http://127.0.0.1:8025，只收本地测试邮件 |
| 本机 OpenSSH | `127.0.0.1:12222`，平台内部使用 `testlab-ssh:2222` |
| 平台服务器资产 | `local-docker-test`，复用已有 Docker Desktop Linux VM 资产 |
| 平台业务容器 | `sentinelops-testlab-business-1` |
| 平台应用 | `实测问答服务` / `testlab-qa` |
| 平台 LLM Endpoint | `testlab-deepseek-flash` |
| 预测任务 | `testlab-business-memory`，每 30 分钟执行一次 |

宿主机指标代表 Docker Desktop 的 Linux VM。cAdvisor 会发现同一 Docker Engine 中的其他容器；SSH 网关独立限制为当前业务测试容器，不能借此操作平台或其他容器。SSH 是真实 OpenSSH、密钥认证和固定主机公钥校验；仅开放固定 inspect、logs、restart 指令，不提供交互式 Shell。

## 已配置的 LLM 与流量

使用用户提供的真实 DeepSeek Key，模型为官方当前名称 `deepseek-flash`，接口为 `https://api.deepseek.com`。已验证短请求、业务问答和平台推理探测，来源见 [DeepSeek 官方说明](https://api-docs.deepseek.com/)。

平台诊断和复盘直接使用真实 Provider。业务服务通过本地网关调用同一 Provider；平台监控该网关，因此可以在本地注入上游错误并验证分类。网关出错时不向真实 Provider 发送故障请求。

背景流量默认每 5 秒请求一次 `/work`，只在本地执行计算，不调用 LLM。业务页面手动问答和人工发起诊断会调用真实 LLM。Endpoint 每小时自动进行一次 5 Token 短探测；测试 LLM 故障时可在平台点击“手动探测”，不必等待一小时。

真实 Key 保存在 `.local/` 下被忽略的独立文件中；平台诊断使用既有权限受限的 Linux secret 卷。SSH 私钥与主机身份保存在 `.local/testlab/ssh/`，不要提交这些文件。业务源码、镜像层和操作说明不含密钥。

## 控制场景

在 `D:\research\sentinelops` 执行。故障默认持续到人工恢复，准备完成时保留 `healthy` 和 `steady`。

```powershell
# 查看容器、业务和流量状态
& backend/.venv/Scripts/python.exe testlab/manage.py status

# 注入业务故障
& backend/.venv/Scripts/python.exe testlab/manage.py fault slow

# 恢复正常
& backend/.venv/Scripts/python.exe testlab/manage.py fault healthy

# 切换流量；暂停为 paused
& backend/.venv/Scripts/python.exe testlab/manage.py load cycle
& backend/.venv/Scripts/python.exe testlab/manage.py load steady
```

| 故障模式 | 实际行为 |
| --- | --- |
| `healthy` | 正常业务与问答 |
| `slow` | `/work` 增加 3 秒延迟 |
| `cpu` | 每个业务请求增加约 0.8 秒 CPU 计算，容器限制为 1 CPU |
| `memory` | 下一次业务请求后持有 64 MiB 内存；正常请求恢复时释放，容器上限 256 MiB |
| `stuck` | `/work`、`/ready` 返回 503；业务进程重启后清除该故障 |
| `business_error` | 进程 `/health` 正常，但 `/work`、`/ready` 返回 503；重启不清除 |
| `llm_401` / `llm_429` / `llm_503` | 网关和问答返回对应上游故障；不调用真实 LLM |
| `llm_timeout` | 网关故意等待 20 秒，超过平台 15 秒探测截止时间 |

流量模式：`steady`（0.2 次/秒）、`ramp`（10 分钟内递增）、`cycle`（10 分钟周期）、`burst`（每 2 分钟的前 15 秒高峰）、`paused`。生成器单并发，目标最高 5 次/秒；服务变慢时实际吞吐会降低。10 分钟周期用于快速观察，不代表真实日周期。

已启用业务不可用、业务延迟以及 LLM 不可用、限流和服务异常规则，持续条件为 30 秒。另有之前保留的本机规则。故障发生后需要等待采集、规则计算和告警通知，不是修改场景后立即出现告警。平台 restart_container 仍须由用户按正常审批流程批准，测试准备没有自动消费审批或执行运维动作。

## 源码与修复测试

`testlab/service` 是独立的小型 Git 仓库。`main` / `v1.0.0` 保留原始正常版本；`fault/source-503` / `v1.0.0-bug` 为故障版本。当前运行的指标版使用本地工作区扩展，见下方业务指标接入记录。

- 当前正常镜像：`sentinelops-testlab-business:metrics-20261018`，业务返回 200，并开放 `/metrics`；原 `sentinelops-testlab-business:v1` 保留用于回退。
- 故障镜像：`sentinelops-testlab-business:v1-bug`，源码错误地固定选择 `business_error`，业务一直返回 503；修改运行场景或重启都不能解决。该镜像已构建，未部署。
- 原 `v1` 与故障镜像的隔离验证见 `.local/testlab/source-versions.json`；当前指标版的验证见 `.local/testlab/business-metrics-20261018/`。
- 正常版本已关联不可变源码快照、实际镜像 ID、发布和容器部署，知识库已加入专用 SOP 并完成索引。本地源码快照保留“版本未经远程 Git 核验”的缺口，不声称生产源码验证通过。

部署源码故障版本时，应同时登记对应源码快照、发布及实际新容器部署；只替换镜像会使诊断证据与故障版本不一致。本次准备保持正常版本，源码故障测试可作为下一轮独立实测。

### 业务指标接入（2026-10-18）

- 当前业务镜像使用本地工作区的指标实现，已登记本地源码快照、实际镜像 ID 和新部署；源码文件哈希与镜像构建文件一致，不声明为已核验的 Git 发布。旧部署记录保留，并记录实际结束时间。
- 服务的真实流量目标为 `testlab-business:8080`，Prometheus 的 `service-traffic` 任务通过平台服务发现采集 `/metrics`。保留 `/ready` 就绪检测，新增 `GET /work` 业务请求检测；选择后者查看请求速率、错误率和 P95。带 `X-SentinelOps-Probe: 1` 的平台探测归入 `probe`，图表仅统计 `business` 流量。
- 运行中的业务容器保留原共享状态目录，密钥与环境变量保持一致。此次实际配置使用 `compose.testlab.yaml` 加 `.local/testlab/business-metrics-20261018/compose.override.yaml`，更新命令为：

  ```powershell
  docker compose -f compose.testlab.yaml -f .local/testlab/business-metrics-20261018/compose.override.yaml up -d --no-deps --no-build --wait business
  ```

- 新业务容器已关联原服务，现有就绪监测与内存预测已重绑，SSH 允许列表仍仅含当前业务实例。原始记录与配置备份、隔离镜像验证和实测结果位于 `.local/testlab/business-metrics-20261018/`。
- 回退时使用保留的 `sentinelops-testlab-business:v1` 镜像及相同状态、密钥挂载，只重建 `business`；随后同步容器资产，按实际新 ID 更新服务监测、预测和 SSH 允许列表，并登记回退部署。回退后旧镜像不提供业务指标接口，真实流量图会停止新增样本。
- 已验证 `/ready`、`/work`、`/metrics`、探测与业务请求计数隔离、延迟直方图、Prometheus 配置及实际采集、平台各指标当前值和历史样本、新实例 SSH 只读诊断。LLM 配置保留；本次验证未调用真实模型。

## 启停和检查

```powershell
# 停止 / 重新启动这三个测试容器，不停止运维平台
& backend/.venv/Scripts/python.exe testlab/manage.py stop
& backend/.venv/Scripts/python.exe testlab/manage.py start

# 重建正常业务镜像、更新 SSH 允许的容器 ID，并启动平台所需 worker
& backend/.venv/Scripts/python.exe testlab/manage.py fault healthy
& backend/.venv/Scripts/python.exe testlab/manage.py up
& backend/.venv/Scripts/python.exe testlab/manage.py register
& backend/.venv/Scripts/python.exe testlab/manage.py bind-release

# 重新构建源码故障镜像，不改变运行中的正常服务
& backend/.venv/Scripts/python.exe testlab/prepare_versions.py

# 检查全部资源，结束后恢复 healthy 与 steady；包含一次真实短问答
& backend/.venv/Scripts/python.exe testlab/verify.py
```

Compose 为 `compose.testlab.yaml`，共享平台已有的 `sentinelops_default` 网络。当前 source-worker 已将本项目只读挂载为 `/source-input`，读取 `testlab/service`；新机器需按平台源码接入说明设置该挂载。

监控历史从启动起真实积累，预测任务已启用。当前历史不足以评价日、周周期预测准确率。准备检查覆盖服务与资源连通、故障控制和 SSH 权限；完整事故诊断、人工审批、执行及复盘质量留给正式实测。

资源 ID 记录：`.local/testlab/resources.json`；实际验证结果：`.local/testlab/verification.json`；真实 LLM 首次连通记录：`.local/testlab/llm-check.json`。

前端 Nginx 使用 Docker DNS 动态解析后端地址，健康检查同时覆盖网页和 `/health/ready`。资源验收通过 8080 网页代理执行登录与平台查询，避免只检查 8000 直连接口而遗漏网页入口。2026-10-14 已在隔离网络中更换后端 IP，确认无须重载 Nginx 即可恢复 API 和健康检查；证据为 `.local/testlab/proxy-regression.json` 与 `.local/testlab/proxy-live-check.json`。配置依据为 [Nginx upstream resolve](https://nginx.org/en/docs/http/ngx_http_upstream_module.html#server)。
## 只读业务巡检验收

「实测问答服务」的真实工作响应快照及误拒、漏检回放已接入巡检，记录保留在平台；两条验收计划当前暂停。规则回放、去重及复核通过；授权真实模型复测发生 Provider 访问异常，评审未完成。接入方式、首轮待复核结果及复测证据见[实测问答服务巡检验收](../docs/backend/实测问答服务巡检验收-20261024.md)。历史问答语义质量尚未验证。

2026-10-25 追加[独立多源验收](../docs/backend/独立多源巡检实施与验收.md)：构造封面来自 API，内页来自真实 PostgreSQL 的独立只读账号。保留三条对象结论和已暂停计划，没有调用模型或使用真实证书。API 使用 `compose.inspections-multisource.yaml` 独立项目，数据库使用 `sentinelops-inspections-db` 的 `_test` 库，端口 25436 仅绑定本机。保留两个容器供页面复查，不与平台业务库混用。

已准备测试库、当前平台及「实测问答服务」时，可在仓库根目录复验（复用同名计划，保存新版本和巡检记录，结束时暂停）：

```powershell
backend/.venv/Scripts/python.exe testlab/accept_multisource_inspections.py
```

脚本只向受 `_test` 名称保护的库写构造内页，经平台执行 GET/SELECT 并保存巡检结果；本机测试配置、只读密码及证据位于 `.local/`，不提交。源数据、规则、脚本限制及启动方式见上述验收说明。

## 服务数据库隔离验收

MySQL 健康检查通过 TCP 连接确认就绪，避免初始化阶段的临时 Unix socket 被误判为可供平台连接的数据库。

数据库接入使用独立的 `sentinelops-database-acceptance` Compose 项目，配置见 `compose.databases.yaml`。在仓库根目录运行 `backend/.venv/Scripts/python.exe backend/scripts/accept_service_databases.py --start`，脚本准备随机凭据、测试 CA、独立平台测试库和两个只读业务库账号，再运行定向测试。端口 15483、13386 仅绑定回环；启动、限制和清理命令见[数据库接入说明](../docs/backend/服务级只读数据库接入.md)。

