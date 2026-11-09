"""Automatic read-only business inspections; frozen DDL and initial Skills."""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0032_business_inspections"
down_revision = "0031_data_cost_governance"
branch_labels = None
depends_on = None

STATEMENTS = [
    (
        "\n"
        "CREATE TABLE inspection_plans (\n"
        "\tid UUID NOT NULL, \n"
        "\tapplication_id UUID NOT NULL, \n"
        "\tconfigured_by UUID NOT NULL, \n"
        "\tname VARCHAR(100) NOT NULL, \n"
        "\tenvironment VARCHAR(50) NOT NULL, \n"
        "\tenabled BOOLEAN NOT NULL, \n"
        "\tversion INTEGER NOT NULL, \n"
        "\tinterval_seconds INTEGER NOT NULL, \n"
        "\tlookback_seconds INTEGER NOT NULL, \n"
        "\tmax_records INTEGER NOT NULL, \n"
        "\tagent_enabled BOOLEAN NOT NULL, \n"
        "\tmax_agent_items INTEGER NOT NULL, \n"
        "\tskill_id UUID, \n"
        "\tskill_version INTEGER, \n"
        "\tsource_kind VARCHAR(20) NOT NULL, \n"
        "\tsource_config JSON NOT NULL, \n"
        "\tencrypted_headers TEXT, \n"
        "\trules JSON NOT NULL, \n"
        "\tnext_run_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
        "\twatermark TIMESTAMP WITH TIME ZONE, \n"
        "\tcontinuation JSON NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_inspection_plans PRIMARY KEY (id), \n"
        "\tCONSTRAINT fk_inspection_plans_application_id_applications FORE"
        "IGN KEY(application_id) REFERENCES applications (id), \n"
        "\tCONSTRAINT fk_inspection_plans_configured_by_users FOREIGN KEY("
        "configured_by) REFERENCES users (id), \n"
        "\tCONSTRAINT fk_inspection_plans_skill_id_knowledge_skills FOREIG"
        "N KEY(skill_id) REFERENCES knowledge_skills (id)\n"
        ")\n"
        "\n"
    ),
    ("CREATE INDEX ix_inspection_plans_application_id ON inspection_plans (application_id)"),
    ("CREATE INDEX ix_inspection_plans_next_run_at ON inspection_plans (next_run_at)"),
    (
        "CREATE TRIGGER trg_inspection_plans_updated_at BEFORE UPDATE ON i"
        "nspection_plans FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    ),
    (
        "\n"
        "CREATE TABLE inspection_runs (\n"
        "\tid UUID NOT NULL, \n"
        "\tplan_id UUID NOT NULL, \n"
        "\tplan_version INTEGER NOT NULL, \n"
        "\ttrigger VARCHAR(20) NOT NULL, \n"
        "\trequested_by UUID NOT NULL, \n"
        "\tstatus VARCHAR(20) NOT NULL, \n"
        "\tsnapshot JSON NOT NULL, \n"
        "\twindow_start TIMESTAMP WITH TIME ZONE NOT NULL, \n"
        "\twindow_end TIMESTAMP WITH TIME ZONE NOT NULL, \n"
        "\tpage_cursor JSON NOT NULL, \n"
        "\tcollected INTEGER NOT NULL, \n"
        "\tagent_calls INTEGER NOT NULL, \n"
        "\tsummary JSON NOT NULL, \n"
        "\terror_code VARCHAR(80), \n"
        "\tstarted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tfinished_at TIMESTAMP WITH TIME ZONE, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_inspection_runs PRIMARY KEY (id), \n"
        "\tCONSTRAINT fk_inspection_runs_plan_id_inspection_plans FOREIGN "
        "KEY(plan_id) REFERENCES inspection_plans (id), \n"
        "\tCONSTRAINT fk_inspection_runs_requested_by_users FOREIGN KEY(re"
        "quested_by) REFERENCES users (id)\n"
        ")\n"
        "\n"
    ),
    ("CREATE INDEX ix_inspection_runs_plan_id ON inspection_runs (plan_id)"),
    ("CREATE INDEX ix_inspection_runs_status ON inspection_runs (status)"),
    (
        "CREATE UNIQUE INDEX uq_inspection_runs_active_plan ON inspection_"
        "runs (plan_id) WHERE status IN ('queued','collecting','evaluating"
        "')"
    ),
    (
        "CREATE TRIGGER trg_inspection_runs_updated_at BEFORE UPDATE ON in"
        "spection_runs FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    ),
    (
        "\n"
        "CREATE TABLE inspection_items (\n"
        "\tid UUID NOT NULL, \n"
        "\trun_id UUID NOT NULL, \n"
        "\tplan_id UUID NOT NULL, \n"
        "\tidentity_hash VARCHAR(64) NOT NULL, \n"
        "\trecord_id VARCHAR(200) NOT NULL, \n"
        "\trecord_version VARCHAR(200) NOT NULL, \n"
        "\tobserved_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
        "\tevidence JSON NOT NULL, \n"
        "\tchecks JSON NOT NULL, \n"
        "\tverdict VARCHAR(30) NOT NULL, \n"
        "\tagent_status VARCHAR(30) NOT NULL, \n"
        "\tagent_result JSON NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_inspection_items PRIMARY KEY (id), \n"
        "\tCONSTRAINT fk_inspection_items_run_id_inspection_runs FOREIGN K"
        "EY(run_id) REFERENCES inspection_runs (id), \n"
        "\tCONSTRAINT fk_inspection_items_plan_id_inspection_plans FOREIGN"
        " KEY(plan_id) REFERENCES inspection_plans (id)\n"
        ")\n"
        "\n"
    ),
    ("CREATE INDEX ix_inspection_items_plan_id ON inspection_items (plan_id)"),
    ("CREATE INDEX ix_inspection_items_run_id ON inspection_items (run_id)"),
    ("CREATE UNIQUE INDEX uq_inspection_items_identity ON inspection_items (identity_hash)"),
    (
        "CREATE TRIGGER trg_inspection_items_updated_at BEFORE UPDATE ON i"
        "nspection_items FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    ),
    (
        "\n"
        "CREATE TABLE inspection_reviews (\n"
        "\tid UUID NOT NULL, \n"
        "\titem_id UUID NOT NULL, \n"
        "\treviewer_id UUID NOT NULL, \n"
        "\tverdict VARCHAR(30) NOT NULL, \n"
        "\tcomment VARCHAR(4000) NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_inspection_reviews PRIMARY KEY (id), \n"
        "\tCONSTRAINT fk_inspection_reviews_item_id_inspection_items FOREI"
        "GN KEY(item_id) REFERENCES inspection_items (id), \n"
        "\tCONSTRAINT fk_inspection_reviews_reviewer_id_users FOREIGN KEY("
        "reviewer_id) REFERENCES users (id)\n"
        ")\n"
        "\n"
    ),
    ("CREATE INDEX ix_inspection_reviews_item_id ON inspection_reviews (item_id)"),
    (
        "CREATE TRIGGER trg_inspection_reviews_updated_at BEFORE UPDATE ON"
        " inspection_reviews FOR EACH ROW EXECUTE FUNCTION set_updated_at("
        ")"
    ),
]

SKILLS = [
    {
        "code": "business_inspection",
        "title": "业务只读巡检",
        "content": (
            "---\n"
            "name: business-inspection\n"
            "description: 对已授权业务记录执行只读巡检，核对固定规则、原始"
            "事实与线上 Agent 结论，输出可追溯差异和证据缺口。\n"
            "---\n"
            "\n"
            "先读取本轮业务记录和程序检查结果。只检查计划指定的对象和依据"
            "，不能扩大范围。\n"
            "\n"
            "1. 核对记录版本、原始事实、证据来源和时间。原 Agent 结论属于"
            "待检查输出，不是真值。\n"
            "2. 对照固定规则和程序检查结果，识别缺项、数据矛盾、规则误用与"
            "结论冲突。\n"
            "3. 已有程序检查不能被文字解释覆盖；不得自行创建阈值、执行公式"
            "或补造未提供的事实。\n"
            "4. 缺少关键原文、适用依据或可靠数值时，输出 unassessable 或 n"
            "eeds_review，并列出 data_gaps。\n"
            "5. supported 仅表示当前提供的依据支持原结论，不代表业务全面正"
            "确。contradicted 必须说明具体矛盾。\n"
            "6. evidence_ids 只能引用本条工具返回的记录 ID；不要引用 Skill"
            " 当作业务观测。\n"
            "\n"
            "输出遵循本轮 InspectionAgentResult 结构。只读检查，不改判、不"
            "修复、不发送通知、不执行脚本。\n"
        ),
    },
    {
        "code": "metrology_certificate_review",
        "title": "计量证书只读复核",
        "content": (
            "---\n"
            "name: metrology-certificate-review\n"
            "description: 复核计量检测、校准证书的仪器、项目、点位、适用依"
            "据、客户要求与线上核验结论，仅使用本轮提供的证据与固定规则。"
            "\n"
            "---\n"
            "\n"
            "1. 读取本条证据和程序结果，核对证书版本、仪器型号/编号、项目"
            "、量程、档位和点位。\n"
            "2. 核对内页原文片段、表格定位及关键数值是否齐全。只有原 Agent"
            " 识别表格、没有原文时，明确保留识别错误风险。\n"
            "3. 检查规程/规范、客户要求、判定规则的来源与版本。不得只凭规"
            "程名称猜测限值，不默认全部取最严，也不自行选择不确定度保护带"
            "。\n"
            "4. 对照注册检查器的数值、单位和覆盖检查结果，说明原线上结论的"
            "差异；不在语言模型中替代确定性复算。\n"
            "5. 校准数据、证书完整性及满足客户用途分别核对。缺少适用条件、"
            "原文、规则或可靠数值，输出无法评估或待复核。\n"
            "6. 明确区分疑似误拒、疑似漏检与证据不足。没有重新测量，不宣称"
            "已知道仪器真值。\n"
            "\n"
            "只引用本条记录 ID。检查器已发现的矛盾不能被改为通过。输出遵循"
            "本轮 InspectionAgentResult；不得修改线上判定或执行任何业务动"
            "作。\n"
        ),
    },
]


def upgrade():
    for statement in STATEMENTS:
        op.execute(statement)
    table = sa.table(
        "knowledge_skills",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("title", sa.String()),
        sa.column("content", sa.Text()),
        sa.column("tags", sa.JSON()),
        sa.column("version", sa.Integer()),
        sa.column("enabled", sa.Boolean()),
    )
    connection = op.get_bind()
    for skill in SKILLS:
        if not connection.scalar(sa.select(table.c.id).where(table.c.code == skill["code"])):
            connection.execute(
                table.insert().values(
                    id=uuid4(), **skill, tags=["inspection"], version=1, enabled=True
                )
            )


def downgrade():
    for name in ("inspection_reviews", "inspection_items", "inspection_runs", "inspection_plans"):
        op.drop_table(name)
