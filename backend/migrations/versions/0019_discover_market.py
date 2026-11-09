"""Independent discovery catalogue with reviewed revisions and category management."""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0019_discover_market"
down_revision = "0018_business_services"
branch_labels = None
depends_on = None


def timestamps():
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    ]


def upgrade():
    categories = op.create_table(
        "market_categories",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        *timestamps(),
    )
    op.create_index(
        "uq_market_category_name",
        "market_categories",
        ["name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "market_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("listed", sa.Boolean(), nullable=False),
        sa.Column("published_revision_id", sa.Uuid()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("kind IN ('skill','tool')", name="kind"),
        *timestamps(),
    )
    op.create_index("ix_market_entries_owner_id", "market_entries", ["owner_id"])
    op.create_table(
        "market_entry_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("entry_id", sa.Uuid(), sa.ForeignKey("market_entries.id"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("edit_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("category_id", sa.Uuid(), sa.ForeignKey("market_categories.id"), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("summary", sa.String(300), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewer_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("review_note", sa.String(2000), nullable=False),
        sa.UniqueConstraint("entry_id", "number"),
        sa.CheckConstraint("status IN ('draft','pending','approved','rejected')", name="status"),
        *timestamps(),
    )
    op.create_index("ix_market_entry_revisions_entry_id", "market_entry_revisions", ["entry_id"])
    op.create_index(
        "ix_market_entry_revisions_category_id", "market_entry_revisions", ["category_id"]
    )
    op.create_index(
        "uq_market_revision_working",
        "market_entry_revisions",
        ["entry_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('draft','pending') AND deleted_at IS NULL"),
    )
    op.create_foreign_key(
        "fk_market_entries_published_revision_id_market_entry_revisions",
        "market_entries",
        "market_entry_revisions",
        ["published_revision_id"],
        ["id"],
    )
    op.create_table(
        "market_audit_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("entry_id", sa.Uuid(), sa.ForeignKey("market_entries.id")),
        sa.Column("revision_id", sa.Uuid(), sa.ForeignKey("market_entry_revisions.id")),
        sa.Column("category_id", sa.Uuid(), sa.ForeignKey("market_categories.id")),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        *timestamps(),
    )
    op.create_index("ix_market_audit_logs_entry_id", "market_audit_logs", ["entry_id"])
    for table in (
        "market_categories",
        "market_entries",
        "market_entry_revisions",
        "market_audit_logs",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )
    op.bulk_insert(
        categories,
        [
            {
                "id": uuid4(),
                "name": name,
                "description": description,
                "sort_order": index * 10,
                "enabled": True,
            }
            for index, (name, description) in enumerate(
                [
                    ("办公效率", "文档、表格、演示、任务管理与日常协作"),
                    ("写作与翻译", "写作、校对、翻译与内容整理"),
                    ("科研与学习", "文献阅读、知识整理与学习辅助"),
                    ("数据与分析", "数据处理、统计分析与可视化"),
                    ("设计与创作", "图像、音视频、设计与创意制作"),
                    ("开发与运维", "编程、测试、部署与运行维护"),
                    ("其他", "其他实用场景"),
                ],
                1,
            )
        ],
    )


def downgrade():
    op.drop_table("market_audit_logs")
    op.drop_constraint(
        "fk_market_entries_published_revision_id_market_entry_revisions",
        "market_entries",
        type_="foreignkey",
    )
    op.drop_table("market_entry_revisions")
    op.drop_table("market_entries")
    op.drop_table("market_categories")
