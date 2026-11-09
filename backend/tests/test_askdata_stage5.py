"""Stage 5: saved analyses, statistics, governance settings and interchange.

Requires a dedicated PostgreSQL test database (``POSTGRES_DB`` ending in
``_test``); ``conftest`` migrates it and clears ask-data tables per test.
"""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select, update

from app.db.session import SessionLocal
from app.integrations.askdata_sampling import sample_statement
from app.models import (
    AskDataConfiguration,
    User,
    AskDataDefinition,
    AskDataEvent,
    AskDataModelVersion,
    AskDataRun,
    AskDataSensitiveField,
    ServiceDatabase,
)
from app.schemas.askdata_runs import AskDataColumnMeta
from app.services import askdata as settings
from app.services import askdata_analyses, askdata_settings
from tests.test_askdata import create_connection

TABLES = {
    "public.orders": {
        "columns": [{"name": "id"}, {"name": "phone"}, {"name": "amount"}],
        "primary_key": ["id"],
        "foreign_keys": [],
    }
}


def _prepare(client, headers, monkeypatch, *, schema_cache=None):
    monkeypatch.setattr(askdata_analyses.settings, "check_build_capabilities", lambda: None)
    identifier = create_connection(client, headers)
    with SessionLocal() as db:
        db.execute(
            update(ServiceDatabase)
            .where(ServiceDatabase.id == identifier)
            .values(
                query_enabled=True,
                permission_status="verified",
                schema_hash="c" * 64,
                schema_cache=schema_cache or {"tables": {}},
                allowed_tables={},
            )
        )
        db.commit()
        connection = db.get(ServiceDatabase, identifier)
        admin = db.scalar(select(User).where(User.username == "admin"))
        version = AskDataModelVersion(
            connection_id=identifier,
            created_by=admin.id,
            config_version=connection.config_version,
            schema_hash=connection.schema_hash,
            status="ready",
            context={},
        )
        db.add(version)
        db.flush()
        db.add(
            AskDataConfiguration(
                connection_id=identifier,
                enabled=True,
                version=1,
                active_model_version_id=version.id,
            )
        )
        db.commit()
        return identifier, version.id


def _headers(login, username):
    return {"Authorization": "Bearer " + login(username)["access_token"]}


def test_execution_settings_require_version_and_valid_channel(
    client, admin_headers, make_user, login, monkeypatch
):
    identifier, _ = _prepare(client, admin_headers, monkeypatch)
    base = f"/api/v1/askdata/settings/{identifier}/execution"
    current = client.get(base, headers=admin_headers)
    assert current.status_code == 200, current.text
    body = current.json()
    assert body["budget_seconds"] == 180
    assert body["vector_dimension"] == 512

    make_user("operator", "operator")
    operator = _headers(login, "operator")
    assert client.get(base, headers=operator).status_code == 200
    denied = client.put(
        base,
        headers=operator,
        json={"expected_version": body["version"], "budget_seconds": 120},
    )
    assert denied.status_code == 403

    stale = client.put(
        base,
        headers=admin_headers,
        json={"expected_version": body["version"] + 5, "budget_seconds": 120},
    )
    assert stale.status_code == 409

    assert (
        client.put(
            base,
            headers=admin_headers,
            json={
                "expected_version": body["version"],
                "budget_seconds": 240,
                "vector_model": "BAAI/bge-small-zh-v1.5",
                "vector_dimension": 512,
            },
        ).status_code
        == 200
    )
    updated = client.get(base, headers=admin_headers).json()
    assert updated["budget_seconds"] == 240
    assert updated["version"] == body["version"] + 1

    invalid = client.put(
        base,
        headers=admin_headers,
        json={
            "expected_version": updated["version"],
            "reasoning_channel_id": str(uuid4()),
        },
    )
    assert invalid.status_code == 422


def test_governance_settings_crud_and_permissions(
    client, admin_headers, make_user, login, monkeypatch
):
    identifier, _ = _prepare(client, admin_headers, monkeypatch)
    make_user("operator", "operator")
    make_user("reader", "viewer")
    operator = _headers(login, "operator")
    reader = _headers(login, "reader")
    base = f"/api/v1/askdata/settings/{identifier}"

    zone = {
        "name": "交易域",
        "description": "订单与支付",
        "targets": [{"kind": "table", "key": "public.orders"}],
    }
    created = client.post(f"{base}/zones", headers=admin_headers, json=zone)
    assert created.status_code == 201, created.text
    zone_id = created.json()["id"]
    assert client.post(f"{base}/zones", headers=operator, json=zone).status_code == 403
    assert client.get(f"{base}/zones", headers=reader).json()["total"] == 1
    duplicate = client.post(
        f"{base}/zones",
        headers=admin_headers,
        json={"name": "交易域", "description": "", "targets": []},
    )
    assert duplicate.status_code == 409
    assert (
        client.put(
            f"{base}/zones/{zone_id}",
            headers=admin_headers,
            json={"name": "交易域", "description": "已修订", "targets": [], "enabled": False},
        ).json()["description"]
        == "已修订"
    )

    tag = client.post(
        f"{base}/tags",
        headers=admin_headers,
        json={
            "name": "核心",
            "color": "#c00",
            "targets": [{"object_kind": "table", "object_key": "public.orders"}],
        },
    )
    assert tag.status_code == 201, tag.text
    assert tag.json()["targets"][0]["object_key"] == "public.orders"

    rule = client.post(
        f"{base}/rules",
        headers=admin_headers,
        json={
            "name": "订单口径",
            "kind": "status",
            "target": "public.orders",
            "expression": "status = 'paid'",
            "priority": 10,
        },
    )
    assert rule.status_code == 201, rule.text

    sensitive = client.post(
        f"{base}/sensitive-fields",
        headers=admin_headers,
        json={
            "object_key": "public.orders.phone",
            "category": "mobile",
            "policy": "mask",
            "mask_strategy": "keep_prefix",
            "mask_argument": 3,
            "sampling": "exclude",
        },
    )
    assert sensitive.status_code == 201, sensitive.text
    assert (
        client.post(
            f"{base}/sensitive-fields",
            headers=admin_headers,
            json={"object_key": "phone"},
        ).status_code
        == 422
    )

    supplement = client.post(
        f"{base}/supplements",
        headers=admin_headers,
        json={
            "term": "GMV",
            "kind": "abbreviation",
            "definition": "已支付订单金额合计",
            "aliases": ["成交额"],
        },
    )
    assert supplement.status_code == 201, supplement.text

    assert client.delete(f"{base}/rules/{rule.json()['id']}", headers=admin_headers).status_code == 204
    assert client.get(f"{base}/rules", headers=admin_headers).json()["total"] == 0
    assert (
        client.delete(f"{base}/zones/{zone_id}", headers=reader).status_code == 403
    )


def test_sensitive_policy_excludes_sampling_and_masks_results(
    client, admin_headers, monkeypatch
):
    identifier, _ = _prepare(client, admin_headers, monkeypatch, schema_cache={"tables": TABLES})
    with SessionLocal() as db:
        db.add(
            AskDataSensitiveField(
                connection_id=identifier,
                object_key="public.orders.phone",
                category="mobile",
                policy="mask",
                mask_strategy="keep_prefix",
                mask_argument=3,
                sampling="exclude",
            )
        )
        db.commit()
        exclusions = askdata_settings.sampling_exclusions(db, identifier, TABLES)
        assert exclusions == {"public.orders": ["phone"]}
        statement = sample_statement(
            'SELECT * FROM "public"."orders" LIMIT 1000',
            "postgres",
            TABLES,
            excluded=exclusions,
        )
        assert "phone" not in statement and "amount" in statement

        masked = askdata_settings.mask_result(
            db,
            identifier,
            [AskDataColumnMeta(name="phone", kind="text")],
            [["13800001111"]],
        )
        assert masked == [["138***"]]
        samples = json.loads(json.dumps(TABLES))
        samples["public.orders"]["columns"][1]["sample_values"] = ["13800001111"]
        askdata_settings.mask_samples(db, identifier, samples)
        assert samples["public.orders"]["columns"][1]["sample_values"] == ["138***"]


def test_analysis_replay_uses_fake_executor_and_masking(
    client, admin_headers, make_user, login, monkeypatch
):
    identifier, version_id = _prepare(client, admin_headers, monkeypatch, schema_cache={"tables": TABLES})
    with SessionLocal() as db:
        db.add(
            AskDataSensitiveField(
                connection_id=identifier,
                object_key="public.orders.phone",
                category="mobile",
                policy="mask",
                mask_strategy="full",
                sampling="exclude",
            )
        )
        db.commit()

    class Connector:
        def execute_raw(self, sql):
            return {
                "columns": ["phone", "amount"],
                "rows": [["13800001111", 12]],
                "column_types": [
                    {"name": "phone", "kind": "text"},
                    {"name": "amount", "kind": "decimal"},
                ],
                "truncated": False,
                "observed_at": None,
                "executed_sql": sql,
            }

    monkeypatch.setattr(
        askdata_analyses.settings, "database_connector", lambda *args, **kwargs: Connector()
    )
    created = client.post(
        "/api/v1/askdata/analyses",
        headers=admin_headers,
        json={
            "connection_id": str(identifier),
            "name": "手机号订单",
            "question": "订单里的手机号与金额",
            "sql_text": "SELECT phone, amount FROM public.orders",
            "explanation": "按订单导出手机号与金额",
            "evidence": [],
        },
    )
    assert created.status_code == 201, created.text
    identifier_analysis = created.json()["id"]

    make_user("other", "admin")
    other = _headers(login, "other")
    assert (
        client.get(f"/api/v1/askdata/analyses/{identifier_analysis}", headers=other).status_code
        == 404
    )
    executed = client.post(
        f"/api/v1/askdata/analyses/{identifier_analysis}/execute", headers=admin_headers
    )
    assert executed.status_code == 200, executed.text
    body = executed.json()
    assert body["result"]["rows"] == [["***", 12]]
    assert body["model_version_id"] == str(version_id)
    saved = client.get(
        f"/api/v1/askdata/analyses/{identifier_analysis}", headers=admin_headers
    ).json()
    assert saved["last_status"] == "completed"
    assert saved["last_row_count"] == 1

    listed = client.get(
        f"/api/v1/askdata/analyses?connection_id={identifier}", headers=admin_headers
    ).json()
    assert listed["total"] == 1


def test_statistics_aggregate_runs_models_and_certification(
    client, admin_headers, monkeypatch
):
    identifier, version_id = _prepare(client, admin_headers, monkeypatch)
    now = datetime.now(UTC)
    with SessionLocal() as db:
        from app.models import AskDataConversation, ModelUsage

        admin = db.scalar(select(User).where(User.username == "admin"))
        conversation = AskDataConversation(connection_id=identifier, owner_id=admin.id, title="统计")
        db.add(conversation)
        db.flush()
        completed = AskDataRun(
            connection_id=identifier,
            conversation_id=conversation.id,
            model_version_id=version_id,
            idempotency_key=uuid4(),
            status="completed",
            question="问题一",
            result={"answer": "答案"},
        )
        failed = AskDataRun(
            connection_id=identifier,
            conversation_id=conversation.id,
            model_version_id=version_id,
            idempotency_key=uuid4(),
            status="failed",
            question="问题二",
            result={},
            error_code="sql_execution_failed",
        )
        db.add_all([completed, failed])
        db.flush()
        db.add_all(
            [
                AskDataEvent(run_id=completed.id, seq=1, kind="step", data={}),
                AskDataEvent(run_id=failed.id, seq=1, kind="step", data={}),
            ]
        )
        db.add(
            ModelUsage(
                dedupe_key=f"askdata:{uuid4().hex}",
                source="askdata",
                provider_kind="agent_channel",
                resource_kind="service_database",
                resource_id=identifier,
                purpose="askdata",
                operation="reasoning",
                model="deepseek",
                occurred_at=now,
                calls=1,
                input_tokens=120,
                output_tokens=30,
                unknown_calls=0,
                outcome="success",
                detail={},
            )
        )
        db.add_all(
            [
                AskDataDefinition(
                    connection_id=identifier,
                    model_version_id=version_id,
                    object_kind="term",
                    object_key="GMV",
                    payload={},
                    certified=True,
                ),
                AskDataDefinition(
                    connection_id=identifier,
                    model_version_id=version_id,
                    object_kind="term",
                    object_key="客单价",
                    payload={},
                ),
            ]
        )
        db.commit()

    response = client.get(
        f"/api/v1/askdata/statistics?connection_id={identifier}&days=7",
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["totals"]["runs"] == 2
    assert body["totals"]["completed"] == 1
    assert body["totals"]["failed"] == 1
    assert body["totals"]["success_rate"] == 0.5
    assert body["failures"][0]["code"] == "sql_execution_failed"
    assert body["models"]["calls"] == 1
    assert body["models"]["input_tokens"] == 120
    assert body["certification"]["certified"] == 1
    assert body["certification"]["coverage"] == 0.5


def test_export_has_no_secrets_or_results_and_import_handles_conflicts(
    client, admin_headers, make_user, login, monkeypatch
):
    identifier, _ = _prepare(client, admin_headers, monkeypatch)
    base = f"/api/v1/askdata/settings/{identifier}"
    assert (
        client.post(
            f"{base}/zones",
            headers=admin_headers,
            json={"name": "交易域", "description": "旧说明", "targets": []},
        ).status_code
        == 201
    )
    exported = client.get(f"{base}/export", headers=admin_headers)
    assert exported.status_code == 200
    document = json.loads(exported.content)
    assert document["format"] == "sentinelops.askdata.model"
    assert "analyses" not in document["assets"]
    text = json.dumps(document)
    assert "password" not in text.lower()
    assert "result" not in document["assets"]

    make_user("operator", "operator")
    operator = _headers(login, "operator")
    assert client.get(f"{base}/export", headers=operator).status_code == 200
    assert (
        client.post(
            f"{base}/imports/preview", headers=operator, json={"document": document}
        ).status_code
        == 403
    )

    document["assets"]["zones"] = [
        {"name": "交易域", "description": "新说明", "targets": [{"kind": "table", "key": "public.orders"}], "enabled": True},
        {"name": "商品域", "description": "", "targets": [], "enabled": True},
    ]
    preview = client.post(
        f"{base}/imports/preview",
        headers=admin_headers,
        json={"filename": "model.json", "document": document},
    )
    assert preview.status_code == 200, preview.text
    report = preview.json()
    assert report["counts"]["conflict"] == 1
    assert report["counts"]["create"] == 1

    applied = client.post(
        f"{base}/imports/{report['import_id']}/apply",
        headers=admin_headers,
        json={"conflict_policy": "overwrite"},
    )
    assert applied.status_code == 200, applied.text
    zones = client.get(f"{base}/zones", headers=admin_headers).json()["items"]
    assert {zone["name"] for zone in zones} == {"交易域", "商品域"}
    assert next(zone for zone in zones if zone["name"] == "交易域")["description"] == "新说明"

    again = client.get(
        f"{base}/imports/{report['import_id']}/apply", headers=admin_headers
    )
    assert again.status_code in (405, 409)
