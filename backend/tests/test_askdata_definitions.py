"""Stage 2: bounded sampling, reviewed definitions and version comparison."""

from uuid import uuid4

from sqlalchemy import select, update

from app.db.session import SessionLocal
from app.integrations.askdata_sampling import sample_statement
from app.integrations.database_sql import checked_query
from app.models import AskDataDefinition, AskDataModelVersion
from app.models.askdata import ONTOLOGY_TABLES
from app.services import askdata as settings
from app.services import askdata_definitions as definitions
from tests.test_askdata import create_connection
from tests.test_askdata_stage2 import create_version, prepare, seed_catalog

TABLES = {
    "public.orders": {
        "columns": [{"name": "id"}, {"name": "customer_id"}, {"name": "amount"}]
    }
}


def test_sample_rewrite_uses_granted_columns_and_bounded_rows():
    sql = sample_statement(
        'SELECT * FROM "public"."orders" LIMIT 1000', "postgres", TABLES
    )
    assert '"id"' in sql and '"customer_id"' in sql and '"amount"' in sql
    assert "LIMIT 50" in sql and "*" not in sql
    statement, _, _ = checked_query(
        sql,
        "postgresql",
        {"public.orders": ["id", "customer_id", "amount"]},
        {"public.orders": {"columns": TABLES["public.orders"]["columns"]}},
    )
    assert "LIMIT 50" in statement
    assert sample_statement("SELECT COUNT(*) FROM orders", "postgres", TABLES) is None
    assert sample_statement("SELECT id FROM orders", "postgres", TABLES) is None
    assert sample_statement('SELECT * FROM "public"."secret" LIMIT 1000', "postgres", TABLES) == ""


def test_structure_only_connector_never_reads_rows():
    import pandas as pd

    from app.integrations.askdata_database import PlatformDatabase

    calls = []

    def executor(sql):
        calls.append(sql)
        return {"rows": [], "columns": [], "truncated": False, "observed_at": None}

    connector = PlatformDatabase(
        uuid4(),
        lambda: {"engine": "postgresql", "tables": TABLES, "query_enabled": False},
        executor,
    )
    frame = connector.execute('SELECT * FROM "public"."orders" LIMIT 1000')
    assert frame.empty and calls == []
    assert isinstance(frame, pd.DataFrame)


def _version(client, headers, monkeypatch, identifier=None):
    monkeypatch.setattr(settings, "check_build_capabilities", lambda: None)
    connection_id = identifier or create_connection(client, headers)
    prepare(connection_id)
    return connection_id, create_version(client, headers, connection_id)


def test_edit_certify_and_metric_listing(client, admin_headers, make_user, login, monkeypatch):
    connection_id, version = _version(client, admin_headers, monkeypatch)
    seeded = seed_catalog(version["id"])
    make_user("reader", "viewer")
    reader = {"Authorization": "Bearer " + login("reader")["access_token"]}
    base = f"/api/v1/askdata/versions/{version['id']}"

    assert client.get(f"{base}/definitions", headers=reader).status_code == 200
    assert client.get(f"{base}/metrics", headers=reader).status_code == 200
    assert (
        client.put(
            f"{base}/definitions",
            headers=reader,
            json={"object_kind": "term", "object_key": "订单", "description": "已下单记录"},
        ).status_code
        == 403
    )

    revised = client.put(
        f"{base}/definitions",
        headers=admin_headers,
        json={
            "object_kind": "term",
            "object_key": "订单",
            "description": "已下单记录",
            "synonyms": ["order", "交易"],
            "certified": True,
        },
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["certified"] is True
    assert revised.json()["payload"]["description"] == "已下单记录"
    listing = client.get(f"{base}/definitions", headers=reader).json()
    assert listing["total"] == 1 and listing["pending_review"] == 0

    with SessionLocal() as db:
        term = ONTOLOGY_TABLES["term"]
        row = db.execute(
            select(term.c.description, term.c.synonyms, term.c.description_certified)
            .where(term.c.model_version_id == version["id"], term.c.id == seeded["term"])
            .execution_options(access_internal=True)
        ).first()
        assert row[0] == "已下单记录" and "交易" in row[1] and row[2] is True

    metric_id = str(uuid4())
    with SessionLocal() as db:
        db.execute(
            ONTOLOGY_TABLES["sql_attribute"]
            .insert()
            .values(
                id=metric_id,
                name="订单金额",
                description="金额合计",
                expression="SUM(amount)",
                source="sql",
                certified=False,
                model_version_id=version["id"],
            )
        )
        db.commit()
    metrics = client.get(f"{base}/metrics?search=金额", headers=reader).json()
    assert metrics["total"] == 1 and metrics["items"][0]["expression"] == "SUM(amount)"


def test_carry_over_keeps_certified_definitions_and_flags_source_change(
    client, admin_headers, monkeypatch
):
    connection_id, first = _version(client, admin_headers, monkeypatch)
    seed_catalog(first["id"])
    base = f"/api/v1/askdata/versions/{first['id']}"
    assert (
        client.put(
            f"{base}/definitions",
            headers=admin_headers,
            json={
                "object_kind": "term",
                "object_key": "订单",
                "description": "人工口径",
                "certified": True,
            },
        ).status_code
        == 200
    )
    with SessionLocal() as db:
        db.execute(
            update(AskDataDefinition)
            .where(AskDataDefinition.model_version_id == first["id"])
            .values(source_fingerprint="previous-structure")
        )
        db.execute(
            update(AskDataModelVersion)
            .where(AskDataModelVersion.id == first["id"])
            .values(status="ready")
        )
        db.commit()

    second_id = uuid4()
    with SessionLocal() as db:
        db.add(
            AskDataModelVersion(
                id=second_id,
                connection_id=connection_id,
                created_by=db.scalar(select(AskDataModelVersion.created_by).limit(1)),
                config_version=1,
                schema_hash="a" * 64,
                status="draft",
                context={},
            )
        )
        db.commit()
    seeded = seed_catalog(second_id)
    result = definitions.carry_over(connection_id, second_id)
    assert result["carried"] == 0 and result["review"] == 1, result
    with SessionLocal() as db:
        term = ONTOLOGY_TABLES["term"]
        description = db.scalar(
            select(term.c.description)
            .where(term.c.model_version_id == second_id, term.c.id == seeded["term"])
            .execution_options(access_internal=True)
        )
        assert description == "人工口径"
        row = db.scalar(
            select(AskDataDefinition).where(
                AskDataDefinition.model_version_id == second_id,
                AskDataDefinition.deleted_at.is_(None),
            )
        )
        assert row.certified is True and row.review_required is True
        assert "待复核" in row.review_note
        definition_id = row.id
        db.execute(
            update(AskDataModelVersion)
            .where(AskDataModelVersion.id == second_id)
            .values(status="ready")
        )
        db.commit()

    # Rebuilding an unreviewed version must preserve its pending state and
    # original approval fingerprint, including in the upstream certification.
    third_id = uuid4()
    with SessionLocal() as db:
        second = db.get(AskDataModelVersion, second_id)
        db.get(AskDataModelVersion, first["id"]).status = "retired"
        db.add(
            AskDataModelVersion(
                id=third_id,
                connection_id=connection_id,
                created_by=second.created_by,
                config_version=second.config_version,
                schema_hash=second.schema_hash,
                status="draft",
                context={},
            )
        )
        db.commit()
    third_seeded = seed_catalog(third_id)
    assert definitions.carry_over(connection_id, third_id)["review"] == 1
    with SessionLocal() as db:
        pending = db.scalar(
            select(AskDataDefinition).where(AskDataDefinition.model_version_id == third_id)
        )
        assert pending.review_required and pending.certified
        assert pending.source_fingerprint == "previous-structure"
        assert "待复核" in pending.review_note
        term = ONTOLOGY_TABLES["term"]
        assert db.scalar(
            select(term.c.description_certified).where(
                term.c.model_version_id == third_id, term.c.id == third_seeded["term"]
            )
        ) is False

    response = client.post(
        f"/api/v1/askdata/versions/{second_id}/definitions/{definition_id}/resolve",
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["review_required"] is False
    assert response.json()["source_fingerprint"] != "previous-structure"
    with SessionLocal() as db:
        term = ONTOLOGY_TABLES["term"]
        assert db.scalar(
            select(term.c.description_certified).where(
                term.c.model_version_id == second_id, term.c.id == seeded["term"]
            )
        ) is True


def test_version_diff_reports_changed_and_added_objects(client, admin_headers, monkeypatch):
    connection_id, first = _version(client, admin_headers, monkeypatch)
    seed_catalog(first["id"])
    second_id = uuid4()
    with SessionLocal() as db:
        creator = db.scalar(select(AskDataModelVersion.created_by).limit(1))
        db.add(
            AskDataModelVersion(
                id=second_id,
                connection_id=connection_id,
                created_by=creator,
                config_version=1,
                schema_hash="a" * 64,
                status="draft",
                context={},
            )
        )
        db.commit()
    seed_catalog(second_id)
    with SessionLocal() as db:
        term = ONTOLOGY_TABLES["term"]
        db.execute(
            update(term)
            .where(term.c.model_version_id == second_id)
            .values(description="新口径")
        )
        db.execute(
            term.insert().values(
                id=str(uuid4()),
                name="客户",
                description="客户主数据",
                source="semantic",
                synonyms=[],
                model_version_id=second_id,
            )
        )
        db.commit()
    response = client.get(
        f"/api/v1/askdata/versions/{second_id}/diff?compare={first['id']}",
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"]["added"] == 1
    assert body["summary"]["changed"] >= 1
    assert any(item["object_key"] == "客户" for item in body["added"])
    missing = client.get(
        f"/api/v1/askdata/versions/{second_id}/diff?compare={uuid4()}",
        headers=admin_headers,
    )
    assert missing.status_code == 404
