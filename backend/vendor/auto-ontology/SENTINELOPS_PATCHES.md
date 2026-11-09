# SentinelOps 源码适配记录

上游：NVIDIA/auto-ontology，提交 `c4495670947eb7b4616f0dd9900e5ced2a48ca1e`。
完整原始文件摘要与源码归档 SHA-256 见 `SENTINELOPS_UPSTREAM.json`。
原始依赖清单保存在 `pyproject.upstream.toml`。原始测试、许可证、第三方声明均保留。

## 阶段 0 补丁

- 新增 `auto_ontology.host.HostBindings`：任务上下文通过 ContextVar 传入，模型配置复制为只读映射，工厂及配置不进入 repr。
- 模型客户端在创建问数状态时取得，不再于导入 main 时捕获模型客户端。
- DAL 的 store 与事务入口委托平台工厂，禁用上游自行创建数据库 engine 的入口。
- 连接列表委托平台工厂，禁用原始连接字符串工厂；数据库驱动模块不再在包导入时全部加载。
- 向量存储创建及 embedding 调用委托平台工厂，避免构造上游向量存储时隐式建表。
- 设置读取委托平台，不读取上游 Prisma configurations 表。
- 并发编译任务使用传递上下文的 ThreadPoolExecutor，每次提交复制上下文，不共享 Session。
- 取消状态在绑定任务中使用平台回调；保留原始未绑定纯逻辑取消测试行为。
- 本地 workspace 包只声明核心运行依赖，移除未启用的十种驱动、Vault、NVIDIA 专用客户端及独立服务依赖；版本由平台 uv.lock 管理。

## 当前边界

阶段 1 已在平台服务与仓储中接入真实存储／事务工厂及授权连接器：`app.services.askdata.bind_storage`、`database_connector`。平台复制启用的 31 张目录／语义表至 askdata schema，纳入 Base/Alembic；存储适配统一处理版本隔离和软删除，没有重写上游 DAL。

阶段 2 未修改上游源码，补齐平台侧适配：向量仓储改为 `askdata.embeddings`（平台迁移建表与 HNSW 索引，按模型版本隔离）；构建任务由独立 worker 领取，持连接 advisory lock，支持软取消与中断恢复；上游全表采样在 `app.integrations.askdata_sampling` 重写为授权列与 50 行上限，再交给平台 SQL 校验器；人工修订与认证状态在构建提交后沿用，来源指纹变化时标记待复核。

阶段 3 同样未修改上游源码：直接调用 `retrieval.text_to_sql.main.stream_agent_response`，把 step／sql／result／error 事件映射为平台持久化的中文步骤事件；`HostBindings` 的 store／vector／embedding／connector 工厂沿用阶段 1—2 实现，`model_config` 保持空映射，模型客户端由平台 `model_factory` 独占，不逐请求改写环境变量。平台新增独立只读 SQL 校验器 `app/integrations/askdata_sql.py`（CTE、子查询、集合、条件表达式与窗口函数），问数执行统一走 `database_connector(gate="askdata")`，诊断与巡检仍用原校验器。问数 worker、SSE 与取消／预算控制已补齐；查询历史文本检索 `get_queries()`、rerank 与 Kumo 预测仍未启用。

阶段 4 未修改上游源码：新增 `app/integrations/askdata_code/`（Python AST 与 Java 静态解析的纯函数抽取器）与 `app/services/askdata_code_rules.py`。平台 `PlatformDatabase.get_queries` 子类通过 `query_provider` 读取代码 SQL（上游 `SQLDatabase.get_queries` 签名不变；结构-only 连接返回空 DataFrame），消费经过表／列白名单过滤的静态代码 SQL 片段，送入上游 `suggest_sql_attributes` 指标表达式生成；未授权 SQL 片段不进入指标与提示词。`askdata_build` 新增 `extracting_source_rules` 步骤，在构建期读取既有哈希校验的源码快照文本、抽取规则、落库并向量化；问数 `askdata_query` 的 `_payload` 注入代码业务规则，`_evidence` 扩展 `AskDataEvidence` 增加 `kind="code_rule"` 出处字段。源码全程只读，绝不导入或执行目标仓库程序。

不能把核心模块可导入、纯逻辑测试通过解释为已经具有可用的问数服务。
阶段 3 已用运行期任务绑定替代全局配置，rerank、导入期模型配置与查询历史文本检索按首版停用处理。
未挂载上游独立路由、启动其服务或应用其迁移。问数对话、目录、业务模型与语义构建页面由 SentinelOps 提供，业务部署尚未升级。真实模型通道与业务库的端到端联调尚未执行。

后续适配必须更新补丁清单，执行核心导入、任务隔离与授权边界测试。

2026-11-07 阶段 4 收尾：平台代码 SQL 授权复用问数完整 SQL gate，保留 schema 与列作用域；上下文和代码向量剔除未授权、动态、冲突与待复核规则，只有认证规则传入上游 `acronyms`（上游同时用于 domain_rules / glossary），草稿参考单独标识。源码快照完整性失败中止构建。未改动上游 Python 源码。

## 阶段 5 补丁

- `auto_ontology/utils/rerank.py`：端点、模型、密钥改为由 `_resolved()` 在调用时按任务解析（`get_rerank_kwargs()` 与后续请求都走它），不再在导入期解析后写进模块级常量。原因是平台把重排配置随任务写入 `HostBindings.model_config`（`RERANK_ENDPOINT` / `RERANK_MODEL` / `RERANK_API_KEY`），导入期解析会把第一个任务的配置永久冻结在进程里，导致后续连接的重排请求打到错误的端点或复用他人密钥。`resolve()` 本身已在阶段 3 改为优先读 `HostBindings.model_config`，此处只调整解析时机；未做请求级环境变量改写。
- 除上述一处外，阶段 5 未修改上游源码。推理／轻量通道、向量模型与维度、重排开关与密钥均在平台侧 `app.services.askdata_settings` 装配：`model_config()` 生成注入映射，`execution_context()` 给出 `vector_signature`，模型版本建立时落库签名，问数校验时比对签名要求重建，避免新旧向量混用。重排默认关闭，只有开启且通过连接测试（`reranker_verified_at` 非空）才注入 `RERANK_*` 配置。
- 上游 `retrieval` 侧接口签名与行为不变，`suggest_sql_attributes`、`stream_agent_response`、`SQLDatabase.get_queries` 仍按原契约调用；导入导出为平台新增能力（`app.services.askdata_interchange`），导出文档不含连接凭据、查询原始结果与分析记录。

## 阶段 7 补丁

- `auto_ontology/utils/llm_invoke.py`：新增 `_is_deepseek()`，`_structured_output_kwargs()` 对 DeepSeek 返回 `{"method": "function_calling", "tool_choice": None}`。原因是平台默认通道 `deepseek-flash` 不支持 `response_format.type=json_schema`（实测 400 `This response_format type is unavailable now`），构建在语义编译首步即失败；该端点支持工具调用，且思考模式下不接受任何强制 `tool_choice`（400 `Thinking mode does not support this tool_choice`），因此只给工具定义、不指定选择。判定同时匹配模型名与 `openai_api_base`，平台自建 OpenAI 兼容网关仍走默认 json_schema。实测日期 2026-11-07，`json_object` 亦可用但需调用方自行保证 schema，故未采用。
- 除上述一处外，阶段 7 未修改上游源码。构建写入的可见性问题在平台侧修复（见下条），不属于上游缺陷。
- 平台 `app/repositories/ontology.py`：`OntologyStore` 支持 `commit` 回调与多行 INSERT（`insert(t).values([...])`，逐行注入 `model_version_id`），不再以 `ontology_single_insert_required` 拒绝批量子句；平台 `app/integrations/askdata_semantic.py` 的 `BuildStorage` 在未开启显式事务时逐语句提交，符合上游 `store_factory()`「返回活动事务或 autocommit 句柄」的契约。原因是上游目录写入把表放在调用线程、列放在线程池，未提交的表行对写列的连接不可见，复合外键 `fk_catalog_column_table_id_catalog_table` 直接失败；语义编译仍包在 `transaction()` 内，保持全有或全无。
- `auto_ontology/retrieval/text_to_sql/agents/candidates_retrieval.py`、`candidates_preparation.py`：线程池由标准库 `ThreadPoolExecutor` 换成 `auto_ontology.host.ContextThreadPoolExecutor`（仅改导入）。原因是检索侧在池内并发做向量检索与候选准备，而平台把版本、存储与模型工厂放进 ContextVar 任务绑定；标准库线程池不复制上下文，池内 `require()` 直接抛 `Auto-Ontology requires an authorized platform task`，异常被上层吞掉后候选表为空，模型只能凭空编表名（实测出现 `public.orders`），问数在「校验筛选值→修正 SQL」之间反复重试直到预算耗尽。构建侧 `catalog/services/schema.py` 原本就用上下文线程池，故构建未暴露该问题。
