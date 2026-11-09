"""Database access; transaction ownership stays with the caller."""

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.models import Container, Server, SSHCredential


def credentials_query():
    return (
        select(SSHCredential).where(SSHCredential.deleted_at.is_(None)).order_by(SSHCredential.name)
    )


def delete_credential_query(credential_id, db):
    return db.scalar(
        select(Server.id)
        .where(Server.credential_id == credential_id, Server.deleted_at.is_(None))
        .limit(1)
    )


def delete_credential_query_2():
    return func.clock_timestamp()


def servers_query(q):
    return (
        select(Server)
        .where(
            Server.deleted_at.is_(None),
            (
                Server.name.icontains(q or "", autoescape=True)
                | Server.host.icontains(q or "", autoescape=True)
            ),
        )
        .order_by(Server.name)
    )


def delete_server_query():
    return func.clock_timestamp()


def delete_server_query_2(db, server_id):
    return db.scalars(
        select(Container).where(Container.server_id == server_id, Container.deleted_at.is_(None))
    )


def delete_server_query_3():
    return func.clock_timestamp()


def test_ssh_query():
    return func.clock_timestamp()


def servers_summary_query(db, server_ids):
    records = db.scalars(
        select(Server).where(Server.id.in_(server_ids), Server.deleted_at.is_(None))
    ).all()
    return records


def synchronize_inventory_query(db, server):
    db.refresh(server, with_for_update=True)


def synchronize_inventory_query_2(db, values):
    stmt = insert(Container).values(**values)
    db.execute(
        stmt.on_conflict_do_update(
            constraint="uq_containers_server_id",
            set_={
                "updated_at": func.now(),
                **{
                    key: value
                    for key, value in values.items()
                    if key not in ("server_id", "docker_id")
                },
            },
            where=Container.deleted_at.is_(None),
        )
    )


def synchronize_inventory_query_3(db, server_id):
    return db.scalars(
        select(Container).where(Container.server_id == server_id, Container.deleted_at.is_(None))
    )


def synchronize_inventory_query_4(db, server_id):
    return db.scalars(
        select(Container)
        .where(Container.server_id == server_id, Container.deleted_at.is_(None))
        .order_by(Container.name)
    )


def inventory_metrics_query(db):
    server_ids = db.scalars(
        select(Server.id).where(Server.deleted_at.is_(None), Server.monitoring_enabled.is_(True))
    ).all()
    return server_ids


def inventory_metrics_query_2(db, server_id):
    server = db.get(Server, server_id, populate_existing=True)
    return server


def inventory_metrics_query_3(db, server_id):
    return db.scalars(
        select(Container).where(Container.server_id == server_id, Container.deleted_at.is_(None))
    )


def containers_query(q):
    query = (
        select(Container)
        .join(Server)
        .where(
            Container.deleted_at.is_(None),
            (
                Container.name.icontains(q or "", autoescape=True)
                | Container.image.icontains(q or "", autoescape=True)
                | Container.service_name.icontains(q or "", autoescape=True)
                | Container.purpose.icontains(q or "", autoescape=True)
            ),
            Server.deleted_at.is_(None),
        )
    )
    return query


def containers_query_2(query, server_id):
    query = query.where(Container.server_id == server_id)
    return query


def containers_query_3(query, status):
    query = query.where(Container.status == status)
    return query


def containers_query_4(query):
    return query.order_by(Container.name)


def update_container_service_query(db, server):
    db.refresh(server, with_for_update=True)


def server_container_overview_query(db, server_id):
    inventory = db.scalars(
        select(Container)
        .where(Container.server_id == server_id, Container.deleted_at.is_(None))
        .order_by(Container.name)
    ).all()
    return inventory


def retire_container_query():
    return func.clock_timestamp()
