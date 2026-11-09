/**
 * 页面标题栏的功能介绍。列表页按完整路径匹配，带 ID 的详情页按前缀兜底。
 * 文案只说明这个页面能做什么，不重复标题，也不写操作提示。
 */

const exactIntros: Record<string, string> = {
  "/": "汇总应用运行、服务器资源、告警、事故、待审批与预测风险，一屏掌握平台当前状态",
  "/servers":
    "登记与管理服务器资产，可配置指标采集目标、SSH 连接与 HTTP 健康检查",
  "/containers":
    "同步 Docker 容器清单与运行状态，按业务服务归类并下钻指标与日志",
  "/applications": "维护业务服务，关联容器、源码版本、主动监测与数据库连接",
  "/llm-endpoints":
    "登记大模型服务地址，主动探测真实推理请求的可用性、延迟与错误分类",
  "/predictions":
    "预测服务器 CPU、内存、磁盘等指标的短期趋势，可配置预测任务与风险阈值",
  "/prediction-tasks":
    "按服务器配置 CPU、内存、磁盘等预测指标、风险阈值与自动调度，可启停任务",
  "/incidents": "告警聚合为事故，串联诊断、审批执行与复盘的处理中心",
  "/alerts": "查看告警事件的触发与结束，跟踪人工确认及资源恢复核验",
  "/alert-rules": "维护告警规则，保存后需显式应用并重载 Prometheus 才生效",
  "/approvals":
    "AI 诊断结论经人工采用后在此申请执行授权，审批绑定具体目标与参数",
  "/executions": "查看人工发起的受控执行，含前置检查、执行结果、恢复验证与审计",
  "/action-skills": "查看平台预定义的执行动作、参数与风险等级，不提供任意命令",
  "/evaluations": "收录诊断与处置案例，回放并评价 AI 结论与动作效果",
  "/inspections": "配置巡检任务与计划，按数据源自动核查服务、资源与配置状态",
  "/inspections/plans":
    "配置巡检任务与计划，按数据源自动核查服务、资源与配置状态",
  "/inspections/runs": "查看每次巡检的覆盖范围、证据与问题记录",
  "/inspections/sources": "登记巡检使用的数据库、接口等数据源与连接信息",
  "/askdata/chat":
    "基于本体与代码库进行知识问答，用自然语言提问并生成可复核的查询与分析",
  "/askdata/catalog": "维护数据目录版本，标注数据表、字段及其业务含义",
  "/askdata/models": "维护业务模型与指标口径，供问数 Agent 理解数据语义",
  "/askdata/analyses": "保存常用分析的问题、SQL 与结果，便于复用和复核",
  "/askdata/statistics": "查看问数调用量、成功率与模型用量，评估使用效果",
  "/askdata/settings": "配置问数 Agent 的数据库连接、模型与可访问范围",
  "/reports": "冻结事故证据，生成、编辑、审核并导出复盘报告",
  "/improvements": "跟踪复盘提出的整改事项及完成状态",
  "/knowledge": "维护 Markdown 运维知识、SOP 与故障案例，索引后参与检索",
  "/knowledge/search": "按问题检索运维知识、SOP 与故障案例",
  "/skills": "维护版本化 Skill，定义 Agent 适用场景、诊断步骤与输出要求",
  "/knowledge-validity": "检查知识文档与 Skill 的有效期，提醒更新过期内容",
  "/notifications": "集中处理待审批、告警与任务通知",
  "/access": "查看当前账号权限，发起权限申请或处理授权",
  "/roles": "配置各角色的默认权限矩阵",
  "/users": "管理平台账号与角色分配",
  "/ssh-credentials": "加密保存 SSH 凭据，供只读诊断与受控执行使用",
  "/audit-logs": "按事故、审批、执行、报告与操作人追溯只追加的审计记录",
  "/system": "查看后端、数据库、采集组件与各 worker 的运行状态",
  "/agent-configs": "配置平台 AI 与各 Agent 的模型、开关和调用参数",
  "/notification-settings": "配置通知渠道、维护窗口与待办生成规则",
  "/data-governance": "查看存储占用，管理数据留存期限与清理策略",
  "/recycle-bin": "查看并恢复已软删除的对象",
};

const prefixIntros: Record<string, string> = {
  "/servers/": "查看单台服务器的采集状态、容器、指标与 SSH 连通性",
  "/containers/": "查看容器运行详情、资源占用与关联的业务服务",
  "/llm-endpoints/": "查看模型服务的探测历史、故障分类与关联应用",
  "/applications/": "查看业务服务的容器、源码版本、监测与诊断证据",
  "/incidents/": "查看事故关联告警、诊断结论、审批执行与复盘材料",
  "/alerts/": "查看告警详情、确认记录与恢复核验状态",
  "/predictions/": "查看单次预测的曲线、各时间窗结论与残差异常",
  "/prediction-tasks/": "查看预测任务的资源配置、阈值与调度历史",
  "/approvals/": "核对审批绑定的目标、参数与有效期，再决定是否批准",
  "/executions/": "查看执行的前置检查、结果、恢复验证与审计记录",
  "/reports/": "查看复盘报告正文、证据引用、审核与投递状态",
  "/knowledge/": "查看知识文档正文、版本与索引状态",
  "/skills/": "查看 Skill 版本、适用场景、诊断步骤与输出要求",
  "/inspections/plans/":
    "配置巡检任务的目标数据源、检查项与调度，或查看任务证据",
  "/inspections/runs/": "查看本次巡检的覆盖范围、证据与问题明细",
  "/inspections/services/": "按业务服务查看巡检覆盖与问题明细",
  "/inspections/": "查看单次巡检任务或结果的证据、覆盖与问题明细",
};

const prefixes = Object.entries(prefixIntros).sort(
  (a, b) => b[0].length - a[0].length,
);

export function pageIntro(path: string): string {
  if (exactIntros[path]) return exactIntros[path];
  if (path.endsWith("/diagnosis"))
    return "AI 基于只读工具与知识检索给出诊断建议，结论需人工采用";
  return prefixes.find(([prefix]) => path.startsWith(prefix))?.[1] ?? "";
}
