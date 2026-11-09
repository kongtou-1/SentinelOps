import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from test_ai import ai_config as ai_config
from test_ai import diagnosis, enqueue, running
from test_ai import incident as incident
from test_ai import local_provider as local_provider
from test_conversations import create, send
from test_sources import create_app, run_sync
from test_sources import source_paths as source_paths

from app.workers.ai import analyze_once
from app.services.diagnosis.analyses import complete, fingerprint, validate_result
from app.workers.conversations import conversation_once
from app.services.diagnosis.conversations import valid_evidence
from app.db.session import SessionLocal
from app.services.diagnosis.tools import DiagnosticTools
from app.models import (
    AgentAnalysis,
    AlertEvent,
    AnalysisRun,
    Application,
    ApplicationDeployment,
    ApplicationRelease,
    Container,
    Incident,
    LLMEndpoint,
    SourceSnapshot,
)
from app.services.sources.diagnostics import freeze_source_context


@pytest.fixture
def sources(client, admin_headers, incident, source_paths):
    project = source_paths / "service"
    (project / "backend/app").mkdir(parents=True)
    (project / "docs").mkdir()
    app = create_app(client, admin_headers, local_path="service")
    versions = []
    for version in (1, 2):
        (project / "backend/app/main.py").write_text(f"timeout = {version * 10}\nprint(timeout)\n")
        (project / "docs/runbook.md").write_text(f"Version {version}: check timeout\n")
        versions.append(run_sync(client, admin_headers, app))
    with SessionLocal() as db:
        event = db.scalar(select(AlertEvent))
        now = event.starts_at
        deployments = []
        releases = []
        for i, snap in enumerate(versions):
            release = ApplicationRelease(
                application_id=UUID(app["id"]),
                version=f"v{i + 1}",
                snapshot_id=UUID(snap["id"]),
                change_summary=f"timeout v{i + 1}",
            )
            db.add(release)
            db.flush()
            deployment = ApplicationDeployment(
                application_id=UUID(app["id"]),
                release_id=release.id,
                server_id=UUID(incident["server_id"]),
                container_id=UUID(incident["container_id"]),
                environment="test",
                started_at=now - timedelta(hours=2 - i),
                ended_at=now - timedelta(hours=1) if i == 0 else None,
                binding={
                    "service_code": app["code"],
                    "entrypoint": "app.main:app",
                    "status": "declared",
                    "path_mappings": app["path_mappings"],
                },
            )
            db.add(deployment)
            db.flush()
            deployments.append(deployment.id)
            releases.append(release.id)
        db.commit()
    return {
        "app": app,
        "snapshots": versions,
        "deployments": deployments,
        "releases": releases,
        "project": project,
    }


def test_frozen_historical_deployment_and_path_mapping(
    client, admin_headers, incident, ai_config, sources
):
    identifier = running(client, admin_headers, incident)
    snap = sources["snapshots"][1]
    with SessionLocal() as db:
        row = db.get(AgentAnalysis, identifier)
        frozen = row.source_context
        assert frozen["bindings"][0]["release_version"] == "v2"
        assert {s["role"] for s in frozen["snapshots"]} == {"release", "previous_release"}
        app = db.get(Application, UUID(sources["app"]["id"]))
        app.path_mappings = [{"runtime_prefix": "/app/app", "repository_prefix": "new"}]
        app.deleted_at = datetime.now(UTC)
        d = db.get(ApplicationDeployment, sources["deployments"][1])
        d.ended_at = datetime.now(UTC)
        db.commit()
        assert db.get(AgentAnalysis, identifier).source_context == frozen
    (sources["project"] / "backend/app/main.py").write_text("different current code\n")
    tools = DiagnosticTools(identifier)
    result = tools.call(
        "read_source_file", {"snapshot_id": snap["id"], "runtime_path": "/app/app/main.py"}
    )
    assert result["data"]["content"] == "timeout = 20\nprint(timeout)"
    assert result["data"]["path"] == "backend/app/main.py"
    assert (
        result["data"]["snippet_hash"]
        == hashlib.sha256(result["data"]["content"].encode()).hexdigest()
    )
    assert result["data"]["source_refs"][0]["content_hash"] == snap["content_hash"]
    search = tools.call(
        "search_source", {"snapshot_id": snap["id"], "query": "Version", "path": "docs"}
    )
    assert search["data"]["matches"][0]["path"] == "docs/runbook.md"
    assert tools.call("list_source_tree", {"snapshot_id": snap["id"]})["data"]["total"] == 2
    diff = tools.call(
        "compare_source_snapshots",
        {
            "snapshot_id": sources["snapshots"][0]["id"],
            "other_id": snap["id"],
            "path": "backend/app/main.py",
        },
    )
    assert "+timeout = 20" in diff["data"]["diff"]


def test_svn_revision_is_frozen_in_diagnosis_scope(
    client, admin_headers, incident, ai_config, sources
):
    snap = sources["snapshots"][1]
    with SessionLocal() as db:
        snapshot = db.get(SourceSnapshot, UUID(snap["id"]))
        snapshot.source, snapshot.svn_revision = "svn", "123"
        snapshot.data_gaps = []
        release = db.get(ApplicationRelease, sources["releases"][1])
        release.expected_revision = "123"
        db.commit()
    identifier = running(client, admin_headers, incident)
    with SessionLocal() as db:
        frozen = db.get(AgentAnalysis, identifier).source_context
        assert frozen["bindings"][0]["expected_revision"] == "123"
        assert any(s.get("svn_revision") == "123" for s in frozen["snapshots"])
    result = DiagnosticTools(identifier).call(
        "read_source_file", {"snapshot_id": snap["id"], "runtime_path": "/app/app/main.py"}
    )
    assert result["data"]["svn_revision"] == "123"
    assert result["data"]["source_refs"][0]["svn_revision"] == "123"


def test_scope_does_not_expand_to_sibling_container(
    client, admin_headers, incident, ai_config, sources
):
    with SessionLocal() as db:
        sibling = Container(
            server_id=UUID(incident["server_id"]),
            name="sibling",
            image="same",
            docker_id="/docker/" + "c" * 64,
            status="running",
            labels={},
            last_seen_at=datetime.now(UTC),
        )
        db.add(sibling)
        db.flush()
        app = Application(code="sibling", name="sibling")
        db.add(app)
        db.flush()
        snap = SourceSnapshot(
            application_id=app.id,
            requested_by=db.scalar(select(AgentAnalysis.requested_by)),
            request={},
            status="ready",
            content_hash="c" * 64,
        )
        # Use the existing source's requester, independently of any analysis.
        snap.requested_by = db.get(SourceSnapshot, UUID(sources["snapshots"][0]["id"])).requested_by
        db.add(snap)
        db.flush()
        rel = ApplicationRelease(application_id=app.id, version="v1", snapshot_id=snap.id)
        db.add(rel)
        db.flush()
        db.add(
            ApplicationDeployment(
                application_id=app.id,
                release_id=rel.id,
                container_id=sibling.id,
                server_id=sibling.server_id,
                environment="test",
                started_at=datetime.now(UTC) - timedelta(days=1),
                binding={},
            )
        )
        db.commit()
        outside = str(snap.id)
    tools = DiagnosticTools(running(client, admin_headers, incident))
    assert outside not in str(tools.call("get_alert_context", {})["data"]["source_context"])
    with pytest.raises(ValueError, match="outside frozen scope"):
        tools.call("read_source_file", {"snapshot_id": outside, "path": "README.md"})
    assert tools.blocked == "source_not_allowed"


def test_host_alert_candidates_grant_no_source_scope(
    client, admin_headers, incident, ai_config, sources
):
    with SessionLocal() as db:
        db.scalar(select(AlertEvent)).labels = {}
        db.commit()
        context = freeze_source_context(db, UUID(incident["incident_id"]))
        assert context["candidates"] and not context["snapshots"] and not context["bindings"]
    tools = DiagnosticTools(running(client, admin_headers, incident))
    with pytest.raises(ValueError):
        tools.call("list_source_tree", {"snapshot_id": sources["snapshots"][0]["id"]})


@pytest.mark.parametrize("labels", [{"id": "/docker/" + "a" * 64}, {"docker_id": "a" * 64}])
def test_native_monitoring_identity_resolves_exact_container(
    client, admin_headers, incident, ai_config, sources, labels
):
    with SessionLocal() as db:
        db.scalar(select(AlertEvent)).labels = labels
        db.commit()
        context = freeze_source_context(db, UUID(incident["incident_id"]))
        assert context["bindings"][0]["target_id"] == incident["container_id"]
        assert context["bindings"][0]["release_version"] == "v2"


def test_old_alert_does_not_follow_new_deployment(
    client, admin_headers, incident, ai_config, sources
):
    with SessionLocal() as db:
        alert = db.scalar(select(AlertEvent))
        alert.starts_at -= timedelta(minutes=90)
        db.commit()
        context = freeze_source_context(db, UUID(incident["incident_id"]))
        assert context["bindings"][0]["release_version"] == "v1"
        assert context["snapshots"][0]["snapshot_id"] == sources["snapshots"][0]["id"]
        assert sources["snapshots"][1]["id"] not in str(context)


def test_llm_endpoint_deployment_has_its_own_source_scope(
    client, admin_headers, incident, ai_config, sources
):
    with SessionLocal() as db:
        endpoint = LLMEndpoint(
            name="source-endpoint",
            base_url="http://local",
            model="test",
            encrypted_api_key="fixture",
        )
        db.add(endpoint)
        db.flush()
        deployment = db.get(ApplicationDeployment, sources["deployments"][1])
        deployment.container_id, deployment.llm_endpoint_id = None, endpoint.id
        deployment.server_id = None
        alert = db.scalar(select(AlertEvent))
        alert.server_id, alert.llm_endpoint_id, alert.labels = None, endpoint.id, {}
        db.commit()
        context = freeze_source_context(db, UUID(incident["incident_id"]))
        assert context["bindings"][0]["target_id"] == str(endpoint.id)
        assert context["snapshots"][0]["snapshot_id"] == sources["snapshots"][1]["id"]


def test_frozen_prediction_target_does_not_require_mutable_task(
    client, admin_headers, incident, ai_config, sources
):
    with SessionLocal() as db:
        alert = db.scalar(select(AlertEvent))
        alert.labels = {}
        alert.rule_snapshot = {"prediction_config": {"container_id": incident["container_id"]}}
        db.commit()
        assert (
            freeze_source_context(db, UUID(incident["incident_id"]))["bindings"][0]["target_id"]
            == incident["container_id"]
        )


def test_oversized_fragment_is_a_gap_and_legacy_evidence_is_persisted(
    client, admin_headers, incident, ai_config, sources
):
    (sources["project"] / "backend/app/main.py").write_text(("x" * 1000 + "\n") * 200)
    large = run_sync(client, admin_headers, sources["app"])
    with SessionLocal() as db:
        db.get(ApplicationRelease, sources["releases"][1]).snapshot_id = UUID(large["id"])
        db.commit()
    identifier = running(client, admin_headers, incident)
    tools = DiagnosticTools(identifier)
    evidence = tools.call(
        "read_source_file", {"snapshot_id": large["id"], "path": "backend/app/main.py"}
    )
    assert evidence["error_code"] == "evidence_too_large" and not tools.blocked
    context = tools.call("get_alert_context", {})
    with SessionLocal() as db:
        assert db.get(AgentAnalysis, identifier).evidence[0] == evidence
        complete(db, identifier, diagnosis(context["id"]), tools.evidence, [], tools.calls)
        assert "evidence_too_large" in db.get(AgentAnalysis, identifier).result["data_gaps"]


@pytest.mark.parametrize("chat", [False, True])
def test_real_harness_without_any_source_continues(
    client, admin_headers, incident, ai_config, local_provider, chat
):
    local_provider.scenario = "source_diagnosis"
    if chat:
        message = send(client, admin_headers, create(client, admin_headers, incident))
        assert conversation_once()
        response = client.post(
            f"/api/v1/analysis-runs/{message['run_id']}/adopt", headers=admin_headers
        )
        assert response.status_code == 200, response.text
        row = response.json()
    else:
        identifier = enqueue(client, admin_headers, incident).json()["id"]
        assert analyze_once()
        row = client.get(f"/api/v1/analyses/{identifier}", headers=admin_headers).json()
    assert row["status"] == "completed", row
    assert "source_unavailable_continue_runtime" in row["result"]["data_gaps"]


@pytest.mark.parametrize("failure", ["missing_file", "missing_store", "corrupt", "unmapped"])
def test_missing_source_is_nonblocking_and_gaps_are_enforced(
    client, admin_headers, incident, ai_config, sources, failure
):
    identifier = running(client, admin_headers, incident)
    snapshot = sources["snapshots"][1]
    args = {"snapshot_id": snapshot["id"], "path": "backend/app/main.py"}
    if failure == "missing_file":
        args["path"] = "missing.py"
    elif failure == "unmapped":
        args["runtime_path"] = "/somewhere/main.py"
    else:
        from app.core.config import get_settings

        target = get_settings().source_store_dir / "snapshots" / snapshot["id"] / args["path"]
        if failure == "missing_store":
            target.unlink()
        else:
            target.write_text("corrupt")
    tools = DiagnosticTools(identifier)
    unavailable = tools.call("read_source_file", args)
    assert unavailable["data"]["availability"] == "unavailable" and not tools.blocked
    context = tools.call("get_alert_context", {})
    with SessionLocal() as db:
        complete(db, identifier, diagnosis(context["id"]), tools.evidence, [], tools.calls)
        row = db.get(AgentAnalysis, identifier)
        assert row.status == "completed"
        assert unavailable["data"]["reason"] in row.result["data_gaps"]
        with pytest.raises(ValueError):
            validate_result(db, row, diagnosis(unavailable["id"]), tools.evidence, [])


@pytest.mark.parametrize(
    "args",
    [
        {"path": "../escape"},
        {"runtime_path": "/app/../escape"},
        {"start_line": 1, "end_line": 201},
        {"start_line": 5, "end_line": 4},
    ],
)
def test_source_schema_boundaries(client, admin_headers, incident, ai_config, sources, args):
    tools = DiagnosticTools(running(client, admin_headers, incident))
    with pytest.raises(ValidationError):
        tools.call("read_source_file", {"snapshot_id": sources["snapshots"][1]["id"], **args})


def test_new_ready_source_changes_fingerprint_but_not_queued_context(
    client, admin_headers, incident, ai_config, sources
):
    with SessionLocal() as db:
        db.get(ApplicationRelease, sources["releases"][1]).snapshot_id = None
        for snap in db.scalars(select(SourceSnapshot)):
            snap.status = "queued"
        db.commit()
    row = enqueue(client, admin_headers, incident).json()
    assert not row["source_context"]["snapshots"]
    with SessionLocal() as db:
        db.get(SourceSnapshot, UUID(sources["snapshots"][1]["id"])).status = "ready"
        db.commit()
        current = db.get(Incident, UUID(incident["incident_id"]))
        assert fingerprint(db, current) != db.get(AgentAnalysis, UUID(row["id"])).input_hash
        assert db.get(AgentAnalysis, UUID(row["id"])).source_context == row["source_context"]
        new = freeze_source_context(db, current.id)
        assert new["snapshots"][0]["role"] == "reference"
        assert "reference_source_version_unverified" in new["data_gaps"]


def test_multi_round_identity_filter_and_adopt_frozen_evidence(
    client, admin_headers, incident, ai_config, sources
):
    conversation = create(client, admin_headers, incident)
    message = send(client, admin_headers, conversation)
    run_id = UUID(message["run_id"])
    with SessionLocal() as db:
        run = db.get(AnalysisRun, run_id)
        run.status = "running"
        db.commit()
    tools = DiagnosticTools(run_id, row_model=AnalysisRun)
    evidence = tools.call(
        "read_source_file",
        {"snapshot_id": sources["snapshots"][1]["id"], "path": "backend/app/main.py"},
    )
    with SessionLocal() as db:
        run = db.get(AnalysisRun, run_id)
        run.evidence, run.candidate, run.status = (
            tools.evidence,
            diagnosis(evidence["id"]),
            "completed",
        )
        db.commit()
    adopted = client.post(f"/api/v1/analysis-runs/{run_id}/adopt", headers=admin_headers)
    assert adopted.status_code == 200, adopted.text
    assert (
        adopted.json()["source_context"]
        == client.get(f"/api/v1/analysis-runs/{run_id}", headers=admin_headers).json()[
            "source_context"
        ]
    )
    assert adopted.json()["evidence"][0] == evidence
    second = send(client, admin_headers, conversation)
    with SessionLocal() as db:
        new_run = db.get(AnalysisRun, UUID(second["run_id"]))
        assert valid_evidence(db, new_run, [evidence]) == [evidence]
        new_run.source_context = {**new_run.source_context, "data_gaps": ["changed"]}
        assert valid_evidence(db, new_run, [evidence]) == []


@pytest.mark.parametrize("chat", [False, True])
def test_real_harness_reads_source_and_adopts(
    client, admin_headers, incident, ai_config, sources, local_provider, chat
):
    local_provider.scenario = "source_diagnosis"
    if chat:
        conversation = create(client, admin_headers, incident)
        msg = send(client, admin_headers, conversation)
        assert conversation_once()
        row = client.get(f"/api/v1/analysis-runs/{msg['run_id']}", headers=admin_headers).json()
        assert row["status"] == "completed", row
        row = client.post(
            f"/api/v1/analysis-runs/{msg['run_id']}/adopt", headers=admin_headers
        ).json()
    else:
        identifier = enqueue(client, admin_headers, incident).json()["id"]
        assert analyze_once()
        row = client.get(f"/api/v1/analyses/{identifier}", headers=admin_headers).json()
    assert row["status"] == "completed", row
    source = [e for e in row["evidence"] if e["source"] == "read_source_file"]
    assert source and "timeout = 20" in source[0]["data"]["content"]
    assert source[0]["id"] in row["result"]["evidence_ids"]
    assert "local_source_version_unverified" in row["result"]["data_gaps"]
