"""Stage-4 source-rule build, review and query-context integration tests.

Requires a dedicated PostgreSQL database (``POSTGRES_DB`` ending in ``_test``);
the shared ``conftest`` session fixture provisions and migrates it. The build
step is invoked directly through ``service.extract_for_build`` with an injected
``source_reader`` so the tests stay independent of the snapshot file store.
"""

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from fastapi import HTTPException
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import AskDataBuild, AskDataCodeRule, AskDataModelVersion, ServiceDatabase
from app.repositories import askdata_code_rules as rule_repository
from app.services import askdata as settings
from app.services import askdata_code_rules as code_rules
from tests.test_askdata import create_connection
from tests.test_sources import create_app, run_sync
from tests.test_sources import source_paths as source_paths

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "askdata_stage4"


def _sample_files(app):
    files = {}
    for p in sorted((FIXTURE_ROOT / app).rglob("*")):
        if p.suffix in (".py", ".java", ".xml"):
            files[p.relative_to(FIXTURE_ROOT / app).as_posix()] = p.read_text(encoding="utf-8")
    return files


def _reader(app):
    files = _sample_files(app)
    return lambda snapshot, user, db: (files, {"reason": "injected"})


def _prepare_connection(db, identifier):
    db.execute(
        ServiceDatabase.__table__.update()
        .where(ServiceDatabase.id == identifier)
        .values(
            query_enabled=True,
            permission_status="verified",
            schema_hash="a" * 64,
            schema_cache={"tables": {}},
            allowed_tables={},
        )
    )
    db.commit()


def _make_ready_version(client, headers, identifier, **body):
    response = client.post(
        f"/api/v1/askdata/connections/{identifier}/versions", headers=headers, json=body
    )
    assert response.status_code == 202, response.text
    version_id = response.json()["id"]
    with SessionLocal() as db:
        version = db.get(AskDataModelVersion, version_id)
        version.status = "ready"
        build = db.scalar(select(AskDataBuild).where(AskDataBuild.model_version_id == version_id))
        if build is not None:
            build.status = "completed"
        db.commit()
    return version_id


def _admin_id(client, headers):
    me = client.get("/api/v1/me", headers=headers)
    assert me.status_code == 200, me.text
    return me.json()["id"]


def test_build_persists_rules_and_exposes_context(client, admin_headers, monkeypatch):
    monkeypatch.setattr(settings, "check_build_capabilities", lambda: None)
    identifier = create_connection(client, admin_headers)
    with SessionLocal() as db:
        _prepare_connection(db, identifier)
    version_id = _make_ready_version(client, admin_headers, identifier)
    user_id = _admin_id(client, admin_headers)

    counts = code_rules.extract_for_build(
        str(uuid4()),
        user_id,
        identifier,
        version_id,
        source_reader=_reader("app_a"),
    )
    assert counts["source_rules"] > 0
    assert counts["source_static_sql"] > 0
    assert counts["source_files"] == 3

    allowed = {
        "public.orders": {
            "columns": [
                {"name": c}
                for c in (
                    "id",
                    "status",
                    "amount",
                    "is_deleted",
                    "deleted_at",
                    "created_at",
                    "paid_at",
                    "user_id",
                )
            ]
        }
    }
    sql = code_rules.source_queries(identifier, version_id, allowed)
    assert sql, "authorized static SQL must be exposed to the upstream pipeline"
    assert all("secret" not in statement.lower() for statement in sql)

    payload = code_rules.context_payload(version_id, allowed)
    assert payload["glossary"], "code rules must surface as glossary entries"
    assert payload["evidence"], "code evidence block must be non-empty"

    # Summary endpoint reflects persisted counts.
    summary = client.get(
        f"/api/v1/askdata/versions/{version_id}/code-rules/summary", headers=admin_headers
    )
    assert summary.status_code == 200, summary.text
    body = summary.json()
    assert body["rule_count"] == counts["source_rules"]
    assert body["file_count"] == 3

    # Listing supports filtering by rule kind.
    listing = client.get(
        f"/api/v1/askdata/versions/{version_id}/code-rules?kind=amount_unit",
        headers=admin_headers,
    )
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] >= 1


def test_code_rules_require_manage_permission_for_review(
    client, admin_headers, make_user, login, monkeypatch
):
    monkeypatch.setattr(settings, "check_build_capabilities", lambda: None)
    identifier = create_connection(client, admin_headers)
    with SessionLocal() as db:
        _prepare_connection(db, identifier)
    version_id = _make_ready_version(client, admin_headers, identifier)
    user_id = _admin_id(client, admin_headers)
    code_rules.extract_for_build(
        str(uuid4()),
        user_id,
        identifier,
        version_id,
        source_reader=_reader("app_a"),
    )

    make_user("reader", "viewer")
    reader = {"Authorization": "Bearer " + login("reader")["access_token"]}

    listing = client.get(f"/api/v1/askdata/versions/{version_id}/code-rules", headers=reader)
    assert listing.status_code == 200  # viewers may read code rules

    rule_id = listing.json()["items"][0]["id"]
    revise = client.put(
        f"/api/v1/askdata/versions/{version_id}/code-rules/{rule_id}",
        headers=reader,
        json={"expression": "amount / 100", "certified": True},
    )
    assert revise.status_code == 403  # only managers may revise
    for action in ("resolve", "conflict"):
        denied = client.post(
            f"/api/v1/askdata/versions/{version_id}/code-rules/{rule_id}/{action}",
            headers=reader,
            json={"action": "keep_manual"} if action == "conflict" else None,
        )
        assert denied.status_code == 403


def test_two_samples_produce_different_business_context(client, admin_headers, monkeypatch):
    monkeypatch.setattr(settings, "check_build_capabilities", lambda: None)

    def version_for(app):
        identifier = create_connection(client, admin_headers)
        with SessionLocal() as db:
            _prepare_connection(db, identifier)
        vid = _make_ready_version(client, admin_headers, identifier)
        uid = _admin_id(client, admin_headers)
        code_rules.extract_for_build(str(uuid4()), uid, identifier, vid, source_reader=_reader(app))
        return vid

    allowed = {
        "public.orders": {
            "columns": [
                {"name": c}
                for c in (
                    "id",
                    "status",
                    "amount",
                    "is_deleted",
                    "deleted_at",
                    "created_at",
                    "paid_at",
                    "user_id",
                )
            ]
        }
    }
    a = code_rules.context_payload(version_for("app_a"), allowed)["glossary"]
    b = code_rules.context_payload(version_for("app_b"), allowed)["glossary"]
    a_text = " ".join(g["description"] for g in a)
    b_text = " ".join(g["description"] for g in b)
    assert "CENT" in a_text and "YUAN" not in a_text
    assert "YUAN" in b_text and "CENT" not in b_text
    assert "orders.is_deleted = FALSE" in a_text
    assert "orders.deleted_at IS NULL" in b_text


def test_real_snapshot_reader_is_pinned_and_rejects_tampering(
    client, admin_headers, monkeypatch, source_paths
):
    from app.core.config import get_settings

    monkeypatch.setattr(settings, "check_build_capabilities", lambda: None)
    monkeypatch.setattr(code_rules, "_index_rules", lambda *a: 0)
    project = source_paths / "orders"
    project.mkdir()
    source = project / "orders.py"
    source.write_text(
        'raise RuntimeError("source code must never execute")\n'
        'class Order:\n    __tablename__ = "orders"\n'
        'def paid(q):\n    return q.filter(Order.status == "PAID")\n',
        encoding="utf-8",
    )
    app = create_app(client, admin_headers, local_path="orders")
    snapshot = run_sync(client, admin_headers, app)
    identifier = create_connection(client, admin_headers, application_id=app["id"])
    with SessionLocal() as db:
        _prepare_connection(db, identifier)
    source.write_text('q.filter(Order.status == "REFUNDED")', encoding="utf-8")
    version = _make_ready_version(
        client, admin_headers, identifier, source_snapshot_id=snapshot["id"]
    )
    counts = code_rules.extract_for_build(
        uuid4(), _admin_id(client, admin_headers), identifier, version
    )
    assert counts["source_files"] == 1
    listing = client.get(
        f"/api/v1/askdata/versions/{version}/code-rules", headers=admin_headers
    ).json()
    assert any(row["expression"] == "orders.status = PAID" for row in listing["items"])

    stored = get_settings().source_store_dir / "snapshots" / snapshot["id"] / "orders.py"
    stored.write_text("tampered", encoding="utf-8")
    next_version = _make_ready_version(
        client, admin_headers, identifier, source_snapshot_id=snapshot["id"]
    )
    with pytest.raises(HTTPException, match="source_snapshot_integrity_error"):
        code_rules.extract_for_build(
            uuid4(), _admin_id(client, admin_headers), identifier, next_version
        )
    with SessionLocal() as db:
        assert not list(
            db.scalars(
                select(AskDataCodeRule).where(
                    AskDataCodeRule.model_version_id == UUID(next_version)
                )
            )
        )


def test_review_conflict_and_filtered_pagination(
    client, admin_headers, monkeypatch, migrated_database
):
    monkeypatch.setattr(settings, "check_build_capabilities", lambda: None)
    identifier = create_connection(client, admin_headers)
    with SessionLocal() as db:
        _prepare_connection(db, identifier)
    version = _make_ready_version(client, admin_headers, identifier)
    uid = _admin_id(client, admin_headers)
    code_rules.extract_for_build(uuid4(), uid, identifier, version, source_reader=_reader("app_a"))
    with pytest.raises(RuntimeError, match="preserve"):
        command.downgrade(migrated_database, "0047_inspection_agent_timestamps")
    root = f"/api/v1/askdata/versions/{version}/code-rules"
    page = client.get(root + "?kind=soft_delete&limit=1", headers=admin_headers).json()
    assert len(page["items"]) == 1
    with SessionLocal() as db:
        actual = list(
            db.scalars(
                select(AskDataCodeRule).where(
                    AskDataCodeRule.model_version_id == UUID(version),
                    AskDataCodeRule.rule_kind == "soft_delete",
                )
            )
        )
        assert page["total"] == len(actual)
        original = db.get(AskDataCodeRule, UUID(page["items"][0]["id"]))
        removed = AskDataCodeRule(
            connection_id=identifier,
            model_version_id=UUID(version),
            language="python",
            path="removed.py",
            start_line=1,
            end_line=1,
            rule_kind="soft_delete",
            object_key="orders.removed",
            expression="removed = 0",
            fingerprint="removed",
            superseded=True,
            review_required=True,
            conflict_state="pending",
        )
        db.add(removed)
        original.certified = True
        original.review_required = True
        original.conflict_state = "pending"
        original.detail = {**original.detail, "code_expression": "orders.is_deleted IS FALSE"}
        db.commit()
        removed_id = str(removed.id)
        original_id = str(original.id)
    summary = client.get(root + "/summary", headers=admin_headers).json()
    assert summary["pending_review"] == 2
    reviews = client.get(
        root + "?review_required=true&include_superseded=true", headers=admin_headers
    ).json()
    assert reviews["total"] == 2
    assert len(reviews["items"]) == 2
    assert (
        client.put(
            root + f"/{removed_id}", headers=admin_headers, json={"certified": True}
        ).status_code
        == 409
    )
    second = client.get(root + "?kind=soft_delete&limit=1&offset=1", headers=admin_headers).json()
    assert second["items"][0]["id"] != page["items"][0]["id"]
    with SessionLocal() as db:
        effective = rule_repository.context_rules(db, UUID(version), limit=800)
        assert original_id not in {str(row.id) for row in effective}
        assert removed_id not in {str(row.id) for row in effective}
    accepted = client.post(
        root + f"/{original_id}/conflict", headers=admin_headers, json={"action": "accept_code"}
    )
    assert accepted.status_code == 200, accepted.text
    assert not accepted.json()["certified"] and not accepted.json()["review_required"]
    certified = client.put(
        root + f"/{original_id}", headers=admin_headers, json={"certified": True}
    )
    assert certified.status_code == 200 and certified.json()["certified"]
    resolved = client.post(root + f"/{removed_id}/resolve", headers=admin_headers)
    assert resolved.status_code == 200
    summary = client.get(root + "/summary", headers=admin_headers).json()
    assert summary["pending_review"] == 0 and summary["conflicts"] == 0
    assert client.get(root + f"/{removed_id}", headers=admin_headers).status_code == 404
