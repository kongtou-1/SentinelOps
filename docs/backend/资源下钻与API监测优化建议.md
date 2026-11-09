# 资源下钻与 API 监测优化建议

2026-10-16。依据实施前源码及 Beszel 官方文档、主分支代码整理，保留当时的缺口分析与优先级。关键 API、多协议探测、真实请求指标、资源下钻及诊断证据已按本文完成第一轮实现，具体范围和验证见[服务可观测性实施与验收](服务可观测性实施与验收.md)。可配置自动诊断见[自动 AI 诊断](自动AI诊断.md)。

## 判断

当前平台已有主机与容器指标、容器健康/日志、业务服务分组、LLM 真实推理探测、告警事故关联、只读诊断和人工审批。资源下钻的主要缺口是缺少业务入口证据及跨层关联。容器全部正常不能证明业务正常；主机 CPU 很高也不能直接证明某个 API 受影响。

现有服务健康由容器运行/HEALTHCHECK 状态汇总。普通 HTTP 探测只有 Server.health_url 一个地址，使用 Blackbox http_2xx 模块；没有独立的服务关键 API 清单。LLM Probe 已发起真实 `/chat/completions` 请求并检查返回内容，但它反映探测请求，不能替代真实业务请求的成功率、吞吐量和尾延迟。

代码依据：

- `backend/app/models/resources.py`：服务器 health_url、容器业务归属和元数据。
- `frontend/src/features/applications/model/businessServices.ts`：服务健康由关联容器观测计算。
- `backend/app/services/resources/overview.py`：主机及容器实时资源、状态、网络观测。
- `backend/app/services/monitoring/config.py`：服务发现把 blackbox 目标绑定到服务器。
- `monitoring/blackbox.yml`：HTTP 2xx、5 秒超时、禁止自动跳转。
- `backend/app/services/llm/probes.py`：真实推理、固定 OK 响应校验、错误分类和探测历史指标。

## Beszel 值得借鉴的部分

| 设计 | 已核对的上游行为 | 在我们项目中的应用 |
| --- | --- | --- |
| 按实体下钻 | 逐容器历史，磁盘/挂载点、GPU 等分别呈现 | 磁盘容量异常直接定位分区与挂载点，网络异常直接定位网卡，不止展示主机总量 |
| 探测来源明确 | ICMP/TCP/HTTP/DNS 探测由选定主机的采集端执行 | 明确平台视角和服务所在主机视角，区分入口故障、内部依赖故障与采集失联 |
| 探测调度有界 | 首次错峰，慢探测不堆积，协议设置超时 | 关键 API 按频率调度、限制并发、错峰；上轮未结束不堆积相同任务 |
| 配置与历史身份绑定 | 目标/协议/端口改变会形成新监测身份 | API 目标、方法、断言或凭据变更后区分历史版本，不把旧成功样本当新配置正常证据 |
| 长时间范围聚合 | records 层汇总短周期数据，界面按范围显示 | 沿用 Prometheus，按时间范围选择查询步长；后续确有压力时增加固定 recording rules |
| 监控自身也被监控 | Hub 可向外部健康服务发送周期心跳 | 先在系统页汇总 worker 心跳、采集失败和队列等待；有明确目标和授权后再接外部监控 |

上游资料：[项目说明](https://github.com/henrygd/beszel)、[网络监测](https://beszel.dev/guide/network-monitors)、[磁盘监测](https://beszel.dev/guide/additional-disks)、[GPU 监测](https://beszel.dev/guide/gpu)、[平台心跳](https://beszel.dev/guide/heartbeat)。

核对代码：[探测调度](https://github.com/henrygd/beszel/blob/main/agent/network_monitor_schedule.go)、[协议探测](https://github.com/henrygd/beszel/blob/main/agent/network_monitor_probe.go)、[监测配置与结果](https://github.com/henrygd/beszel/blob/main/internal/entities/monitor/monitor.go)、[历史聚合](https://github.com/henrygd/beszel/blob/main/internal/records/records.go)、[探测图表](https://github.com/henrygd/beszel/blob/main/internal/site/src/components/routes/system/charts/monitors-charts.tsx)。

Beszel 的 HTTP 代码是 GET 可达性检查，HTTP 状态低于 400 就能通过。它适合作为网络监测参考，不能直接满足带鉴权、请求体及业务结果断言的关键 API 监测。也不需要为借鉴它迁移到 Go/PocketBase 或再部署一套资源监控平台。

## 第一优先级：服务的关键 API

每个业务服务登记少量真正影响使用的接口，例如问答、检索、登录验证和任务状态。第一批从一个服务的 2–3 个安全接口做完整闭环。

每个监测目标具备：服务 ID、名称、重要级别、URL、HTTP 方法、受控请求体、加密鉴权引用、期望状态码、简单 JSON 字段断言、超时、频率、启停状态和配置版本。写入型业务不默认加入探测；使用专用测试身份与不会产生业务副作用的请求。模型接口沿用小 token 请求，控制费用。

先复用现有 Blackbox 承担普通可达性；鉴权、POST 和 JSON 业务断言按需求由受控应用探测器补齐，不接受任意脚本。探测目标绑定已有资产与服务，沿用加密凭据和角色权限。

API 行显示：当前状态、探测成功率、探测耗时、最近成功/失败时间、失败类型。点击后看到历史曲线、脱敏失败证据、所属服务、关联告警/事故。关键接口失败单独影响「业务可用性」；容器状态仍保留为「运行健康」，不将两者混成一个不透明分数。

真实流量指标单独接入：按服务、HTTP 方法、路由模板、状态类别统计请求数、错误率及耗时分布，计算 RPS 和 P95。禁止把完整 URL、用户 ID 或请求 ID 当 Prometheus 标签。探测成功率和真实请求成功率分别标注；少量定时探测的耗时不能称为真实业务 P95。

验收：容器 running/healthy，但问答 API 返回 500 或错误 JSON 时，业务状态异常，产生 API 告警并关联事故；停止探测器显示采集未知，不判定恢复；新配置下取得新鲜成功证据后才核验恢复。

## 第二优先级：沿证据关联资源

服务页组织「关键 API、运行实例、依赖、告警与事故」。沿用已有服务、容器、LLM 绑定和 depends_on，不重新设计一套资产模型。提供两个方向：API 异常下钻到依赖与运行实例；主机/容器异常向上查看关联服务及关键 API 的实测状态。

服务器详情按磁盘、挂载点和网卡列出异常对象及相关曲线。容器详情区分宿主机 CPU 占比与容器配额使用率，增加当前用量/限制、CPU 节流、OOM/重启、网络及挂载信息。只展示有实际来源的字段，缺失状态独立呈现。

首批关联使用显式登记及已有 Docker/Compose 元数据。依赖关系表示可能传播路径，只有实际 API/依赖证据才能说明故障已传播；同机部署不直接算作业务故障。

验收：从一个失败 API 在连续页面中定位其服务、目标实例和已登记依赖，保留相同时间范围；从异常磁盘定位受该挂载影响的实例；不能关联的对象明确显示未关联。

## 第三优先级：把诊断结果接回事故

本次已经补上全局默认与规则覆盖的自动诊断。下一步让诊断上下文包含关键 API 状态、近期失败证据、对应实例和依赖；按同一时间范围比较 API 延迟、容器 CPU/内存、磁盘等待及 LLM 探测。图表同步和时间线能表达先后关系，但不能单凭相关性断言根因。

之后再根据真实问题增加维护窗口、变更时间线、服务目标与告警降噪。GPU 面板优先服务于实际自托管推理资产；没有 GPU 资产时不为了功能清单建设。完整链路追踪、出站采集和 SMART 可在已有证据无法定位问题时单独评估。

建议先完成一个可演示场景：问答 API 变慢 → 区分 LLM 依赖变慢和本地容器资源争用 → 自动给出带证据的诊断 → 人工决定处置 → API 成功且延迟恢复后核验。该场景比继续增加互不关联的监控面板更能体现平台价值。
