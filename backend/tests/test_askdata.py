from uuid import UUID, uuid4

import pytest
from alembic import command
from auto_ontology.dal import schema as source
from fastapi import HTTPException
from sqlalchemy import delete, func, inspect, literal_column, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from app.db.session import SessionLocal, engine
from app.models import (
    AccessPolicy,
    AskDataConversation,
    AskDataModelVersion,
    AskDataRun,
    Base,
    ServiceDatabase,
    User,
)
from app.models.askdata import ONTOLOGY_TABLES
from app.repositories import askdata as repository
from app.services import askdata as service
from app.services.access_control import Authorization


def create_connection(client, headers, name="业务库", application_id=None):
    response = client.post(
        "/api/v1/service-databases",
        headers=headers,
        json={
            "name": name,
            "engine": "postgresql",
            "host": "localhost",
            "port": 5432,
            "database": "same_name",
            "username": "reader",
            "password": "Acceptance-only-secret",
            "environment": "test",
            "tls_mode": "disable",
            "application_id": application_id,
        },
    )
    assert response.status_code == 201, response.text
    return UUID(response.json()["id"])


def version(db, connection_id, user_id):
    connection = db.get(ServiceDatabase, connection_id)
    result = AskDataModelVersion(
        connection_id=connection_id,
        created_by=user_id,
        config_version=connection.config_version,
        schema_hash=connection.schema_hash,
        status="draft",
        context={"connection_id": str(connection_id)},
    )
    db.add(result)
    db.commit()
    return result


def test_tables_and_timestamps(migrated_database):
    inspector = inspect(engine)
    actual = set(inspector.get_table_names(schema="askdata"))
    assert actual == {t.name for t in Base.metadata.tables.values() if t.schema == "askdata"}
    assert len(ONTOLOGY_TABLES) == 31
    assert not {"user_group", "pql_analysis"} & actual
    for name in actual:
        columns = {c["name"]: c for c in inspector.get_columns(name, schema="askdata")}
        for field in ("created_at", "updated_at", "deleted_at"):
            assert columns[field]["type"].timezone
            assert columns[field]["nullable"] == (field == "deleted_at")
        if name in ONTOLOGY_TABLES:
            assert (
                "model_version_id"
                in inspector.get_pk_constraint(name, schema="askdata")["constrained_columns"]
            )


def test_connection_settings_and_roles(client, admin_headers, make_user, login):
    identifier = create_connection(client, admin_headers)
    make_user("reader", "viewer")
    make_user("operator", "operator")
    headers = {"Authorization": "Bearer " + login("reader")["access_token"]}
    listing = client.get("/api/v1/askdata/settings/connections", headers=headers)
    assert listing.status_code == 200
    assert listing.json()[0]["id"] == str(identifier)
    assert "Acceptance-only-secret" not in listing.text
    path = f"/api/v1/askdata/settings/connections/{identifier}"
    assert client.get(path, headers=headers).json()["version"] == 0
    assert client.put(path, headers=headers, json={"enabled": False}).status_code == 403
    response = client.put(
        path, headers=admin_headers, json={"enabled": False, "expected_version": 0}
    )
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 1
    assert client.get(path, headers=headers).json()["enabled"] is False
    assert (
        client.put(
            path, headers=admin_headers, json={"enabled": True, "expected_version": 0}
        ).status_code
        == 409
    )
    with SessionLocal() as db:
        reader = db.scalar(select(User).where(User.username == "reader"))
        operator = db.scalar(select(User).where(User.username == "operator"))
        assert not Authorization(db, reader).has("askdata:run")
        assert Authorization(db, operator).has("askdata:run")
        assert not Authorization(db, operator).has("askdata:manage")


def test_restricted_connections_and_parent_lifecycle(client, admin_headers, make_user, login):
    identifier = create_connection(client, admin_headers)
    make_user("reader", "viewer")
    headers = {"Authorization": "Bearer " + login("reader")["access_token"]}
    result = client.put(
        f"/api/v1/access-policies/service_database/{identifier}",
        headers=admin_headers,
        json={"restricted": True, "expected_version": 0},
    )
    assert result.status_code == 200, result.text
    assert client.get("/api/v1/askdata/settings/connections", headers=headers).json() == []
    assert client.get(
        f"/api/v1/askdata/settings/connections/{identifier}", headers=headers
    ).status_code in (403, 404)
    assert (
        client.delete(f"/api/v1/service-databases/{identifier}", headers=admin_headers).status_code
        == 204
    )
    assert (
        client.get(
            f"/api/v1/askdata/settings/connections/{identifier}", headers=admin_headers
        ).status_code
        == 404
    )


def test_upstream_store_isolation_upsert_soft_delete_and_rollback(client, admin_headers):
    first = create_connection(client, admin_headers, "first")
    second = create_connection(client, admin_headers, "second")
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.username == "admin"))
        a, b = version(db, first, admin.id), version(db, second, admin.id)
        identity = str(uuid4())
        for connection_id, model in ((first, a), (second, b)):
            with service.storage(db, admin, connection_id, model.id) as access:
                store = access.store_factory()
                store.query_write(
                    insert(source.catalog_database).values(id=identity, name="same_name")
                )
                store.query_write(
                    insert(source.term).values(
                        id=identity, name="订单", source="semantic", description=str(connection_id)
                    )
                )
            db.commit()
        with service.storage(db, admin, first, a.id) as access:
            store = access.store_factory()
            result = store.query_read(select(source.term.c.name, source.term.c.description))
            assert result == [{"name": "订单", "description": str(first)}]
            store.query_write(
                insert(source.term)
                .values(id=str(uuid4()), name="订单", source="semantic", description="更新")
                .on_conflict_do_update(
                    index_elements=[source.term.c.name, source.term.c.source],
                    set_={"description": "更新"},
                )
            )
            assert store.query_read(select(source.term.c.description)) == [{"description": "更新"}]
            db.rollback()
            assert store.query_read(select(source.term.c.description)) == [
                {"description": str(first)}
            ]
            # A subquery inside UPDATE must not read another version's definition.
            isolated = (
                select(source.term.c.description)
                .where(source.term.c.description == str(second))
                .scalar_subquery()
            )
            store.query_write(update(source.term).values(description=isolated))
            assert store.query_read(select(source.term.c.description)) == [{"description": None}]
            db.rollback()
            with pytest.raises(ValueError):
                store.query_read(select(source.term), {"model_version_id_1": b.id})
            store.query_write(delete(source.term).where(source.term.c.id == identity))
            db.commit()
            assert store.query_read(select(source.term)) == []
        with service.storage(db, admin, second, b.id) as access:
            assert access.store_factory().query_read(select(source.term.c.description)) == [
                {"description": str(second)}
            ]
        assert db.scalar(select(func.count()).select_from(ONTOLOGY_TABLES["term"])) == 2


def test_ontology_join_preserves_left_join_and_rejects_cross_scope_foreign_keys(
    client, admin_headers
):
    identifier = create_connection(client, admin_headers)
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.username == "admin"))
        first, second = version(db, identifier, admin.id), version(db, identifier, admin.id)
        database_id = str(uuid4())
        with service.storage(db, admin, identifier, first.id) as access:
            access.store_factory().query_write(
                insert(source.catalog_database).values(id=database_id, name="business")
            )
            db.commit()
        with service.storage(db, admin, identifier, second.id) as access:
            with pytest.raises(IntegrityError), db.begin_nested():
                access.store_factory().query_write(
                    insert(source.catalog_schema).values(name="public", database_id=database_id)
                )
        with service.storage(db, admin, identifier, first.id) as access:
            statement = select(
                source.catalog_database.c.name, source.catalog_schema.c.name.label("schema")
            ).select_from(
                source.catalog_database.outerjoin(
                    source.catalog_schema,
                    source.catalog_schema.c.database_id == source.catalog_database.c.id,
                )
            )
            assert access.store_factory().query_read(statement) == [
                {"name": "business", "schema": None}
            ]


def test_storage_rechecks_revocation_configuration_and_disable(client, admin_headers, make_user):
    identifier = create_connection(client, admin_headers)
    user_id = make_user("operator", "operator")
    with SessionLocal() as db:
        user = db.get(User, user_id)
        model = version(db, identifier, user_id)
        with service.storage(db, user, identifier, model.id) as access:
            store = access.store_factory()
            assert store.query_read(select(source.term)) == []
            with pytest.raises(HTTPException) as denied:
                store.query_write(insert(source.term).values(name="forbidden", source="semantic"))
            assert denied.value.status_code == 403
            with SessionLocal() as change:
                change.add(
                    AccessPolicy(
                        resource_kind="service_database",
                        resource_id=str(identifier),
                        user_id=user_id,
                        restricted=True,
                        updated_by=user_id,
                    )
                )
                change.commit()
            with pytest.raises(HTTPException):
                store.query_read(select(source.term))
        db.rollback()
        admin = db.scalar(select(User).where(User.username == "admin"))
        with service.storage(db, admin, identifier, model.id) as access:
            with SessionLocal() as change:
                change.get(ServiceDatabase, identifier).config_version += 1
                change.commit()
            with pytest.raises(HTTPException) as stale:
                access.store_factory().query_read(select(source.term))
            assert stale.value.status_code == 409


@pytest.mark.parametrize(
    "statement",
    [
        text("SELECT * FROM public.users"),
        select(User.__table__),
        select(literal_column("(SELECT password_hash FROM users LIMIT 1)")),
        insert(source.term).values(name="bad", source="semantic", model_version_id=uuid4()),
    ],
)
def test_storage_rejects_unmanaged_statements(client, admin_headers, statement):
    identifier = create_connection(client, admin_headers)
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.username == "admin"))
        model = version(db, identifier, admin.id)
        with service.storage(db, admin, identifier, model.id) as access, pytest.raises(ValueError):
            access.store_factory().query(statement)


def test_private_records_and_database_timestamp_trigger(client, admin_headers, make_user):
    identifier = create_connection(client, admin_headers)
    owner = make_user("operator", "operator")
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.username == "admin"))
        model = version(db, identifier, owner)
        conversation = AskDataConversation(connection_id=identifier, owner_id=owner)
        db.add(conversation)
        db.flush()
        run = AskDataRun(
            connection_id=identifier,
            conversation_id=conversation.id,
            model_version_id=model.id,
            idempotency_key=uuid4(),
            question="有多少订单？",
        )
        db.add(run)
        db.commit()
        assert repository.conversation(db, conversation.id, admin.id) is None
        assert repository.run(db, run.id, admin.id) is None
        assert repository.run(db, run.id, owner).id == run.id
        previous = run.updated_at
        db.execute(
            text("UPDATE askdata.runs SET question='本月订单？' WHERE id=:id"), {"id": run.id}
        )
        db.commit()
        db.refresh(run)
        assert run.updated_at > previous


def test_downgrade_refuses_to_delete_records(client, admin_headers, migrated_database):
    identifier = create_connection(client, admin_headers)
    result = client.put(
        f"/api/v1/askdata/settings/connections/{identifier}",
        headers=admin_headers,
        json={"enabled": False, "expected_version": 0},
    )
    assert result.status_code == 200
    with pytest.raises(RuntimeError, match="preserve"):
        command.downgrade(migrated_database, "0043_llm_agent_channel")
    assert "configurations" in inspect(engine).get_table_names(schema="askdata")


def test_empty_schema_upgrade_downgrade(migrated_database):
    command.downgrade(migrated_database, "0043_llm_agent_channel")
    assert "askdata" not in inspect(engine).get_schema_names()
    command.upgrade(migrated_database, "head")
    tables = inspect(engine).get_table_names(schema="askdata")
    assert len(tables) == 48
    assert {"embeddings", "definitions", "code_rules", "analyses", "zones", "imports"} <= set(
        tables
    )


def test_upstream_dal_uses_platform_transaction_and_scoped_aliases(client, admin_headers):
    from dataclasses import replace

    from auto_ontology.dal.session import store, write_transaction
    from auto_ontology.dal.terms import get_slim_term_by_id
    from test_ontology_host import bindings

    identifier = create_connection(client, admin_headers)
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.username == "admin"))
        model = version(db, identifier, admin.id)
        bound = replace(
            bindings(), connection_id=identifier, model_version_id=model.id, user_id=admin.id
        )
        identity = str(uuid4())
        with service.bind_storage(db, admin, bound, "askdata:manage"):
            with write_transaction():
                store().query_write(
                    insert(source.term).values(id=identity, name="订单", source="semantic")
                )
            assert get_slim_term_by_id(identity) == {"id": identity, "name": "订单"}
            alias = source.term.alias("selected_term")
            assert store().query_read(select(alias.c.name)) == [{"name": "订单"}]
            assert store().query_read(select(func.count()).select_from(source.term)) == [
                {"count_1": 1}
            ]
            db.rollback()
            assert get_slim_term_by_id(identity) is None


def test_storage_disable_and_thread_sharing_fail_closed(client, admin_headers):
    from concurrent.futures import ThreadPoolExecutor

    from app.models import AskDataConfiguration

    identifier = create_connection(client, admin_headers)
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.username == "admin"))
        model = version(db, identifier, admin.id)
        with service.storage(db, admin, identifier, model.id) as access:
            with ThreadPoolExecutor(max_workers=1) as pool:
                with pytest.raises(ValueError, match="thread"):
                    pool.submit(access.store_factory().query_read, select(source.term)).result()
            with SessionLocal() as change:
                change.add(AskDataConfiguration(connection_id=identifier, enabled=False))
                change.commit()
            with pytest.raises(HTTPException) as disabled:
                access.store_factory().query_read(select(source.term))
            assert disabled.value.status_code == 409
