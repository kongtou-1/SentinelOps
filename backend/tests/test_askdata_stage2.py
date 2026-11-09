from uuid import uuid4

from sqlalchemy import select, update

from app.db.session import SessionLocal
from app.models import AskDataBuild, AskDataModelVersion, ServiceDatabase
from app.models.askdata import ONTOLOGY_TABLES
from app.services import askdata as settings
from app.services import askdata_build
from tests.test_askdata import create_connection


def prepare(identifier, *, query=True):
    with SessionLocal() as db:
        db.execute(
            update(ServiceDatabase)
            .where(ServiceDatabase.id == identifier)
            .values(
                query_enabled=query,
                permission_status="verified" if query else "unverified",
                schema_hash="a" * 64,
                schema_cache={"tables": {}},
                allowed_tables={},
            )
        )
        db.commit()


def create_version(client, headers, identifier):
    response = client.post(
        f"/api/v1/askdata/connections/{identifier}/versions", headers=headers, json={}
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_version_lifecycle_and_permissions(client, admin_headers, make_user, login, monkeypatch):
    monkeypatch.setattr(settings, "check_build_capabilities", lambda: None)
    identifier = create_connection(client, admin_headers)
    prepare(identifier)
    make_user("reader", "viewer")
    reader = {"Authorization": "Bearer " + login("reader")["access_token"]}
    path = f"/api/v1/askdata/connections/{identifier}/versions"
    assert client.get(path, headers=reader).status_code == 200
    assert client.get(path, headers=reader).json() == []
    assert client.post(path, headers=reader, json={}).status_code == 403
    version = create_version(client, admin_headers, identifier)
    assert version["status"] == "draft"
    assert version["build"]["status"] == "queued"
    assert version["active"] is False
    assert len(client.get(path, headers=admin_headers).json()) == 1
    with SessionLocal() as db:
        row = db.get(AskDataModelVersion, version["id"])
        build = db.scalar(select(AskDataBuild).where(AskDataBuild.model_version_id == row.id))
        row.status = "ready"
        build.status = "completed"
        db.commit()
    published = client.post(
        f"/api/v1/askdata/versions/{version['id']}/publish", headers=admin_headers
    )
    assert published.status_code == 200, published.text
    assert published.json()["active"] is True
    assert client.get(path, headers=reader).json()[0]["active"] is True
    detail_path = f"/api/v1/askdata/versions/{version['id']}"
    assert client.get(detail_path, headers=reader).json()["active"] is True
    retired = client.post(f"/api/v1/askdata/versions/{version['id']}/retire", headers=admin_headers)
    assert retired.status_code == 200
    assert retired.json()["active"] is False
    assert client.get(detail_path, headers=reader).json()["active"] is False


def test_version_rejects_structural_connection(client, admin_headers, monkeypatch):
    monkeypatch.setattr(settings, "check_build_capabilities", lambda: None)
    identifier = create_connection(client, admin_headers)
    prepare(identifier, query=False)
    response = client.post(
        f"/api/v1/askdata/connections/{identifier}/versions", headers=admin_headers, json={}
    )
    assert response.status_code == 409
    assert "只读" in response.text


def seed_catalog(version_id):
    tables = ONTOLOGY_TABLES
    database, schema = str(uuid4()), str(uuid4())
    orders, customers = str(uuid4()), str(uuid4())
    order_id, customer_id, customer_key = str(uuid4()), str(uuid4()), str(uuid4())
    term, attribute = str(uuid4()), str(uuid4())
    with SessionLocal() as db:
        db.execute(
            tables["catalog_database"]
            .insert()
            .values(id=database, name="business", model_version_id=version_id)
        )
        db.execute(
            tables["catalog_schema"]
            .insert()
            .values(id=schema, database_id=database, name="public", model_version_id=version_id)
        )
        for identifier, name, pk in ((orders, "orders", ["id"]), (customers, "customers", ["id"])):
            db.execute(
                tables["catalog_table"]
                .insert()
                .values(
                    id=identifier,
                    schema_id=schema,
                    name=name,
                    pk=pk,
                    table_type="base table",
                    model_version_id=version_id,
                )
            )
        db.execute(
            tables["catalog_column"]
            .insert()
            .values(
                id=order_id,
                table_id=orders,
                name="id",
                data_type="integer",
                is_nullable=False,
                ordinal_position=1,
                model_version_id=version_id,
            )
        )
        db.execute(
            tables["catalog_column"]
            .insert()
            .values(
                id=customer_id,
                table_id=orders,
                name="customer_id",
                data_type="integer",
                is_nullable=False,
                ordinal_position=2,
                sample_values="[10, 20]",
                model_version_id=version_id,
            )
        )
        db.execute(
            tables["catalog_column"]
            .insert()
            .values(
                id=customer_key,
                table_id=customers,
                name="id",
                data_type="integer",
                is_nullable=False,
                ordinal_position=1,
                is_unique=True,
                model_version_id=version_id,
            )
        )
        db.execute(
            tables["term"]
            .insert()
            .values(
                id=term,
                name="订单",
                description="客户订单",
                source="semantic",
                synonyms=["order"],
                model_version_id=version_id,
            )
        )
        db.execute(
            tables["table__term"]
            .insert()
            .values(table_id=orders, term_id=term, model_version_id=version_id)
        )
        db.execute(
            tables["column_attribute"]
            .insert()
            .values(
                id=attribute,
                name="customerId",
                description="客户编号",
                datatype="integer",
                source="semantic",
                source_column="customer_id",
                term_name="订单",
                table_id=orders,
                model_version_id=version_id,
            )
        )
        db.execute(
            tables["column_attribute__term"]
            .insert()
            .values(
                attribute_id=attribute,
                term_id=term,
                model_version_id=version_id,
            )
        )
        db.execute(
            tables["column__has_attribute"]
            .insert()
            .values(
                column_id=customer_id,
                attribute_id=attribute,
                model_version_id=version_id,
            )
        )
        db.execute(
            tables["column__semantic_fk"]
            .insert()
            .values(
                column_id=customer_id,
                attribute_id=attribute,
                model_version_id=version_id,
            )
        )
        db.execute(
            tables["column__foreign_key"]
            .insert()
            .values(
                source_column_id=customer_id,
                target_column_id=customer_key,
                model_version_id=version_id,
            )
        )
        db.commit()
    return {"orders": orders, "term": term}


def test_catalog_views(client, admin_headers, make_user, login, monkeypatch):
    monkeypatch.setattr(settings, "check_build_capabilities", lambda: None)
    identifier = create_connection(client, admin_headers)
    prepare(identifier)
    version = create_version(client, admin_headers, identifier)
    seeded = seed_catalog(version["id"])
    with SessionLocal() as db:
        row = db.get(AskDataModelVersion, version["id"])
        row.status = "ready"
        db.commit()
    make_user("reader", "viewer")
    reader = {"Authorization": "Bearer " + login("reader")["access_token"]}
    base = f"/api/v1/askdata/versions/{version['id']}"
    summary = client.get(f"{base}/catalog", headers=reader)
    assert summary.status_code == 200, summary.text
    assert summary.json()["tables"] == 2
    assert summary.json()["terms"] == 1
    assert summary.json()["relationships"] == 2
    listing = client.get(f"{base}/tables?search=ord", headers=reader)
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 1
    detail = client.get(f"{base}/tables/{seeded['orders']}", headers=reader)
    assert detail.status_code == 200, detail.text
    assert detail.json()["columns"][1]["sample_values"] == [10, 20]
    assert detail.json()["foreign_keys"][0]["kind"] == "declared"
    terms = client.get(f"{base}/terms", headers=reader)
    assert terms.status_code == 200, terms.text
    assert terms.json()["items"][0]["name"] == "订单"
    term = client.get(f"{base}/terms/{seeded['term']}", headers=reader)
    assert term.status_code == 200, term.text
    assert term.json()["attributes"][0]["table_name"] == "orders"
    relationships = client.get(f"{base}/relationships", headers=reader)
    assert relationships.status_code == 200, relationships.text
    assert relationships.json()["declared"] == 1
    assert relationships.json()["semantic"] == 1


def test_build_failure_is_recorded(client, admin_headers, monkeypatch):
    monkeypatch.setattr(settings, "check_build_capabilities", lambda: None)
    identifier = create_connection(client, admin_headers)
    prepare(identifier)
    version = create_version(client, admin_headers, identifier)
    monkeypatch.setattr(
        askdata_build,
        "_execute",
        lambda *args: (_ for _ in ()).throw(askdata_build.BuildFailure("catalog_empty")),
    )
    with SessionLocal() as db:
        build = db.scalar(
            select(AskDataBuild).where(AskDataBuild.model_version_id == version["id"])
        )
        build.status, build.started_at = "running", build.started_at
        db.commit()
        build_id = build.id
    askdata_build.run(build_id)
    with SessionLocal() as db:
        build = db.get(AskDataBuild, build_id)
        model = db.get(AskDataModelVersion, version["id"])
        assert build.status == "failed"
        assert build.error_code == "catalog_empty"
        assert model.status == "failed"


def test_vector_store_round_trip_and_filter(client, admin_headers, monkeypatch):
    from app.integrations.askdata_semantic import PlatformVectorStore

    monkeypatch.setattr(settings, "check_build_capabilities", lambda: None)
    identifier = create_connection(client, admin_headers)
    prepare(identifier)
    version = create_version(client, admin_headers, identifier)
    vector = [0.0] * 511 + [1.0]
    store = PlatformVectorStore(version["id"])
    count = store.run(
        [
            [
                {
                    "text": "ColumnAttribute: customerId",
                    "path": "semantic:attr:1",
                    "metadata": {
                        "label": "ColumnAttribute",
                        "id": "attribute-1",
                        "database_name": str(identifier),
                        "is_unique": True,
                        "database_name_copy": str(identifier),
                        "embedding": vector,
                    },
                }
            ]
        ]
    )
    assert count == 1
    hits = store.retrieval(
        [vector],
        top_k=3,
        where={"label": "ColumnAttribute", "database_name": str(identifier), "is_unique": True},
    )[0]
    assert hits[0]["metadata"]["id"] == "attribute-1"
    assert store.retrieval([vector], where={"is_unique": False})[0] == []
