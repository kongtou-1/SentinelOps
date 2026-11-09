"""Business permission catalogue and the immutable upgrade baseline."""

from dataclasses import dataclass

ROLE_PERMISSIONS = {
    "viewer": frozenset(
        {
            "inspections:read",
            "market:read",
            "market:submit",
            "profile:read",
            "resources:read",
            "sources:read",
            "repairs:read",
            "alerts:read",
            "llm:read",
            "predictions:read",
            "incidents:read",
            "knowledge:read",
            "analyses:read",
            "executions:read",
            "reports:read",
        }
    ),
    "operator": frozenset(
        {
            "inspections:read",
            "inspections:run",
            "inspections:review",
            "market:read",
            "market:submit",
            "profile:read",
            "system:read",
            "resources:read",
            "alerts:read",
            "alerts:ack",
            "containers:sync",
            "sources:read",
            "sources:sync",
            "repairs:read",
            "repairs:write",
            "containers:diagnose",
            "llm:read",
            "llm:probe",
            "predictions:read",
            "predictions:run",
            "incidents:read",
            "incidents:handle",
            "knowledge:read",
            "analyses:read",
            "analyses:run",
            "executions:read",
            "executions:run",
            "reports:read",
            "reports:run",
        }
    ),
    "admin": frozenset(
        {
            "inspections:read",
            "inspections:run",
            "inspections:review",
            "inspections:manage",
            "market:read",
            "market:submit",
            "market:review",
            "market:manage",
            "profile:read",
            "system:read",
            "users:manage",
            "roles:read",
            "resources:read",
            "resources:manage",
            "sources:read",
            "sources:sync",
            "sources:manage",
            "repairs:read",
            "repairs:write",
            "credentials:manage",
            "alerts:read",
            "alerts:ack",
            "containers:sync",
            "containers:diagnose",
            "rules:manage",
            "llm:read",
            "llm:probe",
            "llm:manage",
            "predictions:read",
            "predictions:run",
            "predictions:manage",
            "incidents:read",
            "incidents:handle",
            "knowledge:read",
            "knowledge:manage",
            "analyses:read",
            "analyses:run",
            "executions:read",
            "executions:run",
            "approvals:decide",
            "incidents:force_close",
            "analysis_conversations:read_all",
            "audit:read",
            "reports:read",
            "reports:run",
            "reports:manage",
        }
    ),
}

RESOURCE_READS = {
    "server": "servers:read",
    "container": "containers:read",
    "application": "applications:read",
    "llm_endpoint": "llm:read",
    "service_monitor": "service_monitors:read",
    "service_database": "service_databases:read",
    "dashboard": "dashboards:read",
}
READ_EXPANSION = frozenset({*RESOURCE_READS.values(), "metrics:read"})
ROLE_PERMISSIONS = {
    role: frozenset(permissions | (READ_EXPANSION if "resources:read" in permissions else set()))
    for role, permissions in ROLE_PERMISSIONS.items()
}
ROLE_PERMISSIONS["admin"] |= {"access:manage", "resources:edit", "logs:read"}
ROLE_PERMISSIONS["operator"] |= {"logs:read"}
ROLE_PERMISSIONS["viewer"] |= {"askdata:read"}
ROLE_PERMISSIONS["operator"] |= {"askdata:read", "askdata:run"}
ROLE_PERMISSIONS["admin"] |= {"askdata:read", "askdata:run", "askdata:manage"}

ADMIN_ONLY = frozenset(
    {
        "access:manage",
        "askdata:manage",
        "users:manage",
        "roles:read",
        "credentials:manage",
        "resources:manage",
        "sources:manage",
        "rules:manage",
        "llm:manage",
        "predictions:manage",
        "knowledge:manage",
        "approvals:decide",
        "incidents:force_close",
        "analysis_conversations:read_all",
        "audit:read",
        "reports:manage",
        "inspections:manage",
        "market:review",
        "market:manage",
    }
)
DASHBOARDS = {
    "overview": "总览",
    "incidents": "事件看板",
    "predictions": "预测中心",
    "inspections": "Agent巡检",
    "evaluations": "效果评价",
    "data-governance": "数据与成本",
}
MODULES = {
    "askdata": "智能问数",
    "resources": "服务器与容器",
    "applications": "应用与服务",
    "diagnosis": "AI诊断",
    "alerts": "告警与事故",
    "predictions": "预测",
    "inspections": "Agent巡检",
    "executions": "审批与执行",
    "reports": "复盘报告",
    "knowledge": "知识与Skill",
    "market": "资源市场",
    "llm": "LLM服务",
    "system": "系统管理",
    "dashboards": "业务看板",
}
VERBS = {
    "read": "查看",
    "run": "运行",
    "manage": "管理",
    "edit": "编辑",
    "sync": "同步",
    "diagnose": "诊断",
    "probe": "探测",
    "ack": "确认",
    "handle": "处理",
    "write": "编辑",
    "review": "复核",
    "submit": "提交",
    "decide": "审批",
    "force_close": "强制关闭",
    "read_all": "查看全部",
}


@dataclass(frozen=True)
class Permission:
    code: str
    name: str
    module: str
    requestable: bool


def module_for(code):
    prefix = code.split(":")[0]
    return {
        "servers": "resources",
        "containers": "resources",
        "metrics": "resources",
        "logs": "resources",
        "sources": "applications",
        "service_monitors": "applications",
        "service_databases": "applications",
        "analyses": "diagnosis",
        "repairs": "diagnosis",
        "analysis_conversations": "diagnosis",
        "incidents": "alerts",
        "approvals": "executions",
    }.get(prefix, prefix if prefix in MODULES else "system")


CATALOGUE = {
    code: Permission(
        code,
        f"{MODULES[module_for(code)]}·{VERBS.get(code.split(':')[1], '使用')}",
        module_for(code),
        code not in ADMIN_ONLY and code not in {"resources:read", "profile:read"},
    )
    for code in sorted(set().union(*ROLE_PERMISSIONS.values()))
}


def bundle(key, module, name, permissions):
    scopes = {
        "askdata": ["application", "service_database"],
        "resources": ["server", "container", "application"],
        "applications": ["application", "service_monitor", "service_database"],
        "diagnosis": ["application", "server", "container", "llm_endpoint", "service_monitor"],
        "alerts": ["application", "server", "container", "llm_endpoint", "service_monitor"],
        "predictions": ["application", "server", "container", "llm_endpoint"],
        "inspections": ["application"],
        "executions": ["application", "server", "container"],
        "reports": ["application", "server", "container", "llm_endpoint", "service_monitor"],
        "knowledge": [],
        "market": [],
        "llm": ["llm_endpoint"],
        "dashboards": ["dashboard"],
        "system": [],
    }
    primary = (
        set(permissions)
        if key.endswith(".view")
        else {p for p in permissions if not p.endswith(":read")}
    )
    if key == "resources.diagnose":
        primary.add("logs:read")
    return {
        "id": key,
        "module": module,
        "name": name,
        "permissions": sorted(set(permissions)),
        "primary_permissions": sorted(primary),
        "scopes": ["global", *scopes[module]],
    }


BUNDLES = [
    bundle("askdata.view", "askdata", "查看", {"askdata:read", "service_databases:read"}),
    bundle(
        "askdata.run",
        "askdata",
        "问数",
        {"askdata:read", "askdata:run", "service_databases:read"},
    ),
    bundle("resources.view", "resources", "查看", READ_EXPANSION - {"dashboards:read", "llm:read"}),
    bundle(
        "resources.diagnose",
        "resources",
        "诊断",
        {"servers:read", "containers:read", "metrics:read", "logs:read", "containers:diagnose"},
    ),
    bundle(
        "resources.operate",
        "resources",
        "日常操作",
        {"servers:read", "containers:read", "containers:sync", "resources:edit"},
    ),
    bundle(
        "applications.view",
        "applications",
        "查看",
        {
            "applications:read",
            "sources:read",
            "service_monitors:read",
            "service_databases:read",
            "containers:read",
            "metrics:read",
        },
    ),
    bundle(
        "applications.operate",
        "applications",
        "日常操作",
        {"applications:read", "sources:read", "sources:sync", "resources:edit"},
    ),
    bundle(
        "diagnosis.view",
        "diagnosis",
        "查看",
        {"incidents:read", "alerts:read", "analyses:read", "repairs:read"},
    ),
    bundle(
        "diagnosis.run",
        "diagnosis",
        "诊断",
        {
            "applications:read",
            "sources:read",
            "service_databases:read",
            "service_monitors:read",
            "containers:read",
            "servers:read",
            "llm:read",
            "metrics:read",
            "logs:read",
            "containers:diagnose",
            "incidents:read",
            "alerts:read",
            "analyses:read",
            "analyses:run",
            "repairs:read",
        },
    ),
    bundle(
        "diagnosis.operate",
        "diagnosis",
        "修复交接",
        {"repairs:read", "repairs:write", "incidents:read", "analyses:read"},
    ),
    bundle("alerts.view", "alerts", "查看", {"alerts:read", "incidents:read"}),
    bundle(
        "alerts.operate",
        "alerts",
        "日常操作",
        {"alerts:read", "incidents:read", "alerts:ack", "incidents:handle"},
    ),
    bundle("predictions.view", "predictions", "查看", {"predictions:read"}),
    bundle("predictions.run", "predictions", "运行", {"predictions:read", "predictions:run"}),
    bundle("inspections.view", "inspections", "查看", {"inspections:read"}),
    bundle(
        "inspections.operate",
        "inspections",
        "运行与复核",
        {"inspections:read", "inspections:run", "inspections:review"},
    ),
    bundle("executions.view", "executions", "查看", {"executions:read"}),
    bundle(
        "executions.run",
        "executions",
        "申请与发起",
        {"executions:read", "executions:run", "incidents:read", "analyses:read"},
    ),
    bundle("reports.view", "reports", "查看", {"reports:read"}),
    bundle("reports.run", "reports", "生成", {"reports:read", "reports:run", "incidents:read"}),
    bundle("knowledge.view", "knowledge", "查看", {"knowledge:read"}),
    bundle("market.view", "market", "查看", {"market:read"}),
    bundle("market.submit", "market", "提交", {"market:read", "market:submit"}),
    bundle("llm.view", "llm", "查看", {"llm:read", "metrics:read"}),
    bundle("llm.probe", "llm", "探测", {"llm:read", "llm:probe"}),
    bundle("dashboards.view", "dashboards", "查看", {"dashboards:read"}),
    bundle("system.view", "system", "查看状态", {"system:read"}),
]
BUNDLE_MAP = {item["id"]: item for item in BUNDLES}
