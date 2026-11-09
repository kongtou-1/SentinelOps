"""Platform sender configuration, separate from service recipients."""

import sqlalchemy as sa
from alembic import op

revision = "0038_platform_mail"
down_revision = "0037_agent_model_channels"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("notification_policy", sa.Column("encrypted_smtp_config", sa.Text()))
    op.add_column(
        "notification_policy",
        sa.Column("smtp_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM notification_policy "
            "WHERE encrypted_smtp_config IS NOT NULL)"
        )
    ):
        raise RuntimeError(
            "Platform mail is configured; preserve the configuration before rollback"
        )
    op.drop_column("notification_policy", "smtp_version")
    op.drop_column("notification_policy", "encrypted_smtp_config")
