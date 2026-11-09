import gzip
import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import (
    AnalysisConversation,
    AnalysisMessage,
    Application,
    ApplicationRelease,
    ArchiveRecord,
    Incident,
    ModelUsage,
    Role,
    SourceSnapshot,
    User,
)
from app.services.operations import retention, usage


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    archive = tmp_path / "archives"
    archive.mkdir()
    monkeypatch.setattr(get_settings(), "source_store_dir", source)
    monkeypatch.setattr(get_settings(), "archive_dir", archive)
    return source, archive


def snapshot_fixture(source):
    old = datetime.now(UTC) - timedelta(days=100)
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "admin"))
        application = Application(code="retention-source", name="Source")
        db.add(application)
        db.flush()
        data = ("safe source line\n" * 10000).encode()
        row = SourceSnapshot(
            application_id=application.id,
            requested_by=user.id,
            status="ready",
            request={},
            manifest=[
                {
                    "path": "main.py",
                    "bytes": len(data),
                    "stored_sha256": hashlib.sha256(data).hexdigest(),
                }
            ],
            total_bytes=len(data),
            created_at=old,
            updated_at=old,
        )
        db.add(row)
        db.commit()
        path = source / "snapshots" / str(row.id)
        path.mkdir(parents=True)
        (path / "main.py").write_bytes(data)
        return row.id, application.id, path


def plan(client, headers, kinds):
    response = client.post("/api/v1/system/cleanup-plans", headers=headers, json={"kinds": kinds})
    assert response.status_code == 201, response.text
    row = response.json()
    response = client.post(
        f"/api/v1/system/cleanup-plans/{row['id']}/execute",
        headers=headers,
        json={"expected_version": row["version"], "confirm": True},
    )
    assert response.status_code == 202, response.text
    return row


def test_archive_releases_files_only_after_verified_archive(client, admin_headers, isolated_store):
    source, archives = isolated_store
    identifier, application_id, path = snapshot_fixture(source)
    preview = plan(client, admin_headers, ["source_snapshot"])
    assert preview["candidates"][0]["protected_reasons"] == []
    assert retention.cleanup_once()
    assert not path.exists()
    with SessionLocal() as db:
        row = db.get(SourceSnapshot, identifier)
        assert row.deleted_at is not None and row.manifest == []
        archive = db.scalar(select(ArchiveRecord).where(ArchiveRecord.resource_id == identifier))
        archive_id = archive.id
        assert archive.status == "complete" and archive.released_file_bytes > archive.archive_bytes
    response = client.get(f"/api/v1/archives/{archive_id}/content", headers=admin_headers)
    assert response.status_code == 200, response.text
    content = json.loads(gzip.decompress(response.content))
    assert content["resource"]["id"] == str(identifier) and "main.py" in content["files"]
    # A late current reference cannot attach to a tombstoned resource, even via SQL.
    with SessionLocal() as db, pytest.raises(IntegrityError):
        db.add(
            ApplicationRelease(
                application_id=application_id, version="late", snapshot_id=identifier
            )
        )
        db.commit()
    (archives / (str(archive_id) + ".json.gz")).write_bytes(b"corrupt")
    assert (
        client.get(f"/api/v1/archives/{archive_id}/content", headers=admin_headers).status_code
        == 503
    )


def test_reference_added_after_preview_protects_source(client, admin_headers, isolated_store):
    source, _ = isolated_store
    identifier, application_id, path = snapshot_fixture(source)
    response = client.post(
        "/api/v1/system/cleanup-plans", headers=admin_headers, json={"kinds": ["source_snapshot"]}
    ).json()
    assert not response["candidates"][0]["protected_reasons"]
    with SessionLocal() as db:
        db.add(
            ApplicationRelease(application_id=application_id, version="v1", snapshot_id=identifier)
        )
        db.commit()
    client.post(
        f"/api/v1/system/cleanup-plans/{response['id']}/execute",
        headers=admin_headers,
        json={"expected_version": 1, "confirm": True},
    )
    assert retention.cleanup_once()
    assert path.exists()
    result = client.get(
        f"/api/v1/system/cleanup-plans/{response['id']}", headers=admin_headers
    ).json()
    assert (
        result["result"][0]["status"] == "protected"
        and "release_reference" in result["result"][0]["reasons"]
    )


def test_private_archive_keeps_owner_scope_and_revoked_admin_cannot_clean(
    client, admin_headers, make_user, login, isolated_store
):
    owner = make_user("archive-owner", "operator")
    make_user("archive-other", "operator")
    now = datetime.now(UTC)
    old = now - timedelta(days=100)
    with SessionLocal() as db:
        incident = Incident(
            title="Private", severity="warning", started_at=now, last_alert_started_at=now
        )
        db.add(incident)
        db.flush()
        conversation = AnalysisConversation(
            incident_id=incident.id, owner_id=owner, title="Private", created_at=old, updated_at=old
        )
        db.add(conversation)
        db.flush()
        db.add(
            AnalysisMessage(
                conversation_id=conversation.id,
                role="user",
                content="private original content",
                status="accepted",
                created_at=old,
                updated_at=old,
            )
        )
        db.commit()
        identifier = conversation.id
    plan(client, admin_headers, ["conversation"])
    assert retention.cleanup_once()
    with SessionLocal() as db:
        archive = db.scalar(select(ArchiveRecord).where(ArchiveRecord.resource_id == identifier))
        archive_id = archive.id
    owner_headers = {"Authorization": "Bearer " + login("archive-owner")["access_token"]}
    other_headers = {"Authorization": "Bearer " + login("archive-other")["access_token"]}
    content = client.get(f"/api/v1/archives/{archive_id}/content", headers=owner_headers)
    assert (
        content.status_code == 200
        and json.loads(gzip.decompress(content.content))["messages"][0]["content"]
        == "private original content"
    )
    assert (
        client.get(f"/api/v1/archives/{archive_id}/content", headers=other_headers).status_code
        == 403
    )
    assert client.get("/api/v1/archives", headers=other_headers).json() == []
    assert (
        client.post("/api/v1/system/cleanup-plans", headers=owner_headers, json={}).status_code
        == 403
    )
    row = plan(client, admin_headers, ["source_snapshot"])
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.username == "admin"))
        admin.role_id = db.scalar(select(Role.id).where(Role.code == "operator"))
        db.commit()
    assert retention.cleanup_once()
    with SessionLocal() as db:
        from app.models import RetentionPlan

        assert db.get(RetentionPlan, UUID(row["id"])).error_code == "admin_authorization_changed"


def test_cleanup_paths_stay_within_owned_store(isolated_store):
    source, _ = isolated_store
    with pytest.raises(ValueError):
        retention.boundary(source, source / "../outside")
    with pytest.raises(ValueError):
        retention.temporary_path("../outside")


def test_platform_metrics_use_explicit_results_and_unique_bounded_labels(
    client, admin_headers, tmp_path, monkeypatch
):
    from prometheus_client.parser import text_string_to_metric_families

    token = tmp_path / "monitoring.token"
    token.write_text("local-metrics-acceptance-2026-secret")
    monkeypatch.setattr(get_settings(), "monitoring_token_file", token)
    assert client.get("/metrics").status_code == 401
    response = client.get(
        "/metrics", headers={"Authorization": "Bearer local-metrics-acceptance-2026-secret"}
    )
    assert response.status_code == 200, response.text
    samples = [
        sample
        for family in text_string_to_metric_families(response.text)
        for sample in family.samples
    ]
    keys = [(sample.name, tuple(sorted(sample.labels.items()))) for sample in samples]
    assert len(keys) == len(set(keys))
    assert any(
        sample.labels.get("operation") == "diagnosis" and sample.value == 0 for sample in samples
    )


def test_usage_keeps_tokens_and_request_results_without_pricing(client, admin_headers):
    key = usage.start_gateway(("analysis", uuid4(), "diagnosis"), 1, "model-a", "model")
    usage.finish_gateway(
        key, {"prompt_tokens": 1000000, "completion_tokens": 0}, 1.5, 0.25, "success"
    )
    unknown = usage.start_gateway(("analysis", uuid4(), "diagnosis"), 1, "model-a", "model")
    usage.finish_gateway(unknown, {}, 1, None, "failed")
    rows = client.get("/api/v1/system/model-usage", headers=admin_headers).json()
    known = next(row for row in rows if row["input_tokens"] is not None)
    assert known["input_tokens"] == 1000000 and known["output_tokens"] == 0
    assert known["duration_seconds"] == 1.5 and known["outcome"] == "success"
    assert next(row for row in rows if row["input_tokens"] is None)["unknown_calls"] == 1
    assert all(
        not {"known_cost", "cost_status", "price_id", "currency"}.intersection(row) for row in rows
    )
    summary = client.get("/api/v1/system/model-usage/summary", headers=admin_headers).json()
    group = summary["groups"][0]
    assert group["calls"] == 2 and group["unknown_usage_records"] == 1
    assert group["success_calls"] == 1 and group["failed_calls"] == 1
    assert "known_cost" not in group and "unpriced_records" not in group


@pytest.mark.parametrize("path", ["model-prices", "usage-budgets", "usage-budgets/status"])
def test_removed_price_and_budget_endpoints(client, admin_headers, path):
    assert client.get("/api/v1/system/" + path, headers=admin_headers).status_code == 404
    assert client.post("/api/v1/system/" + path, headers=admin_headers, json={}).status_code == 404


def test_application_missing_usage_is_unknown_and_collection_is_idempotent(
    client, admin_headers, monkeypatch
):
    now = datetime.now(UTC)
    with SessionLocal() as db:
        app = Application(
            code="usage",
            name="Usage",
            traffic_target="http://business:8000",
            traffic_config_changed_at=now - timedelta(hours=1),
        )
        db.add(app)
        db.commit()
        identifier = app.id

    def prometheus(path, params, **kwargs):
        if "tokens_total" in params["query"]:
            return {"result": []}
        metric = {"model": "app-model", "operation": "ask"}
        if "usage_observations" in params["query"]:
            metric["availability"] = "unknown"
        return {"result": [{"metric": metric, "value": [now.timestamp(), "1"]}]}

    monkeypatch.setattr(usage, "prometheus", prometheus)
    with SessionLocal() as db:
        usage.collect_applications(db, now)
        usage.collect_applications(db, now)
        rows = db.scalars(select(ModelUsage).where(ModelUsage.application_id == identifier)).all()
        assert len(rows) == 1 and rows[0].input_tokens is None and rows[0].unknown_calls == 1
        assert rows[0].detail["estimated"] is True


def test_usage_capture_uses_actual_first_streamed_content(monkeypatch):
    from app.integrations import usage_capture

    clock = [10.0]
    monkeypatch.setattr(usage_capture.time, "monotonic", lambda: clock[0])
    capture = usage_capture.UsageCapture(None, 1, "m", "mock")
    clock[0] = 10.4
    capture.feed(b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n')
    assert capture.first_content is None
    clock[0] = 10.7
    capture.feed(b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n')
    capture.feed(b'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":2}}\n\n')
    assert capture.first_content == pytest.approx(0.7) and capture.usage["prompt_tokens"] == 3
    other = usage_capture.UsageCapture(None, 1, "m", "mock")
    other.raw_json(
        b'{"choices":[{"message":{"content":"done"}}],"usage":{"prompt_tokens":1,"completion_tokens":1}}'
    )
    assert other.first_content is None
