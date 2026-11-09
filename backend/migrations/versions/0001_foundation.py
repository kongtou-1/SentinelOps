"""Migration foundation, including timestamps on Alembic's own table."""

from alembic import op

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE FUNCTION set_updated_at() RETURNS trigger AS $$
        BEGIN
            NEW.updated_at = clock_timestamp();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_alembic_version_updated_at
        BEFORE UPDATE ON alembic_version
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
    """)


def downgrade():
    op.execute("DROP TRIGGER trg_alembic_version_updated_at ON alembic_version")
    op.execute("DROP FUNCTION set_updated_at()")
