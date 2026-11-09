---
name: qa-work-response-review
description: 只读核对实测问答服务工作接口的响应契约。
---

读取本条记录、程序检查结果和固定依据，只核对本轮工作接口响应。

1. 此次对象是 `/work` 的响应快照，不是历史问答。`original_verdict` 来自 HTTP 状态基线，不代表已有业务 Agent 的语义判断。
2. 以计划中的四条固定规则为依据：进程健康、业务就绪和工作请求的 HTTP 状态均为 200，且 `work.ok` 为 true。逐条对照实际值与程序结果；任何程序矛盾都不能改为通过。
3. `facts.health`、`facts.ready`、`facts.work` 是实际响应完整解析后的 JSON；来源及采样时间在 evidence 中。不得把这些已经提供的 JSON 判为缺失，也不要求本范围未使用的网络报文或源码全文。
4. HTTP 200 而 `work.ok=false` 时，HTTP 状态基线可能漏检；规则全部通过而基线为 fail 时，可能误拒。四项均通过且基线为 pass、没有范围内矛盾时，当前依据支持该基线。
5. 缺失实际使用的状态码、`work.ok` 或规则依据时才列出对应 data_gaps。`status.requests`、`llm_calls`、是否配置 LLM、问答内容正确性和实时持续可用性均不在此次判断范围，不作为本条证据缺口。
6. 输出明确限制在采样时刻与这些响应断言；不得宣称问答答案正确或服务全面健康。`sample_kind=synthetic_acceptance_replay` 的记录须明确标为构造回放，不能描述为线上真实故障。

evidence_ids 只引用本条工具记录 ID。只读检查，不改判、不调用业务问答、不修复。
