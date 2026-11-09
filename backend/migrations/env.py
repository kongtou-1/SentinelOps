from alembic import context
from alembic.ddl.postgresql import PostgresqlImpl
from sqlalchemy import Column, DateTime, create_engine, func

from app.core.config import get_settings
from app.models import Base


def managed_name(name, type_, parent_names):
    return type_ != "schema" or name in (None, "public", "askdata")


class TimestampedPostgresqlImpl(PostgresqlImpl):
    __dialect__ = "postgresql"

    def version_table_impl(self, **kwargs):
        table = super().version_table_impl(**kwargs)
        table.append_column(
            Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now())
        )
        table.append_column(
            Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now())
        )
        table.append_column(Column("deleted_at", DateTime(timezone=True), nullable=True))
        return table


def run_migrations_offline():
    context.configure(
        url=get_settings().database_url,
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_schemas=True,
        include_name=managed_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = create_engine(
        get_settings().database_url,
        connect_args={"connect_timeout": 5, "options": "-c timezone=UTC"},
        hide_parameters=True,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            compare_type=True,
            include_schemas=True,
            include_name=managed_name,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
