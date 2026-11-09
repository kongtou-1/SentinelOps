import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_ai import ai_config as ai_config
from test_ai import diagnosis, running
from test_ai import incident as incident
from test_source_diagnostics import sources as sources
from test_sources import source_paths as source_paths

from app.db.session import SessionLocal
from app.models import (
    AgentAnalysis,
    AnalysisConversation,
    AnalysisMessage,
    ApplicationDeployment,
    ApplicationRelease,
    AuditLog,
    Container,
    Incident,
    RepairHandoff,
)
from app.services.diagnosis.analyses import complete
from app.services.diagnosis.tools import DiagnosticTools
from app.services.reports.logic import snapshot
from app.utils.canonical_json import digest


@pytest.fixture
def formal(client, admin_headers, incident, ai_config, sources):
    identifier = running(client, admin_headers, incident)
    tools = DiagnosticTools(identifier)
    evidence = tools.call(
        "read_source_file",
        {"snapshot_id": sources["snapshots"][1]["id"], "path": "backend/app/main.py"},
    )
    with SessionLocal() as db:
        complete(db, identifier, diagnosis(evidence["id"]), [evidence], [], [])
    return str(identifier)


def generate(client, auth, incident, formal, **extra):
    response = client.post(
        f"/api/v1/incidents/{incident['incident_id']}/repair-handoffs",
        headers=auth,
        json={
            "analysis_id": formal,
            "acceptance_criteria": ["超时测试通过", "部署后错误率为 0"],
            "reproduction": "请求超时；password=hidden-acceptance-value",
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def handoff(client, admin_headers, incident, formal):
    return generate(client, admin_headers, incident, formal)


@pytest.fixture
def repair_release(incident, sources):
    with SessionLocal() as db:
        old = db.get(ApplicationDeployment, sources["deployments"][1])
        old.ended_at = datetime.now(UTC)
        release = ApplicationRelease(
            application_id=old.application_id,
            version="v3",
            snapshot_id=UUID(sources["snapshots"][0]["id"]),
            expected_commit="b" * 40,
        )
        db.add(release)
        db.flush()
        deployment = ApplicationDeployment(
            application_id=old.application_id,
            release_id=release.id,
            server_id=old.server_id,
            container_id=old.container_id,
            environment=old.environment,
            started_at=old.ended_at,
            binding={"status": "declared"},
        )
        db.add(deployment)
        db.commit()
        return {"release_id": str(release.id), "deployment_id": str(deployment.id)}


def add_result(client, auth, handoff, **extra):
    return client.post(
        f"/api/v1/repair-handoffs/{handoff['id']}/results",
        headers=auth,
        json={"change_summary": "修复超时参数", "test_result": "pytest timeout: 3 passed", **extra},
    )


def verification_body(result, **extra):
    return {
        "result_id": result["id"],
        "outcome": "fix_validated",
        "observed_at": datetime.now(UTC).isoformat(),
        "evidence": "服务健康检查 200",
        "acceptance_results": "超时测试通过；观察窗口内错误为 0",
        **extra,
    }


def test_freeze_export_and_public_private_boundary(
    client, admin_headers, incident, formal, handoff, sources
):
    assert handoff["packet_hash"] == digest(handoff["packet"])
    assert handoff["application_id"] == sources["app"]["id"]
    assert handoff["packet"]["source_context"]["bindings"][0]["release_version"] == "v2"
    assert "hidden-acceptance-value" not in json.dumps(handoff)
    with SessionLocal() as db:
        analysis = db.get(AgentAnalysis, UUID(formal))
        analysis.result = {"summary": "subsequently changed"}
        owner = analysis.requested_by
        conversation = AnalysisConversation(
            incident_id=analysis.incident_id, owner_id=owner, title="PRIVATE CHAT CONTENT"
        )
        db.add(conversation)
        db.flush()
        db.add(
            AnalysisMessage(
                conversation_id=conversation.id,
                role="user",
                content="PRIVATE CHAT CONTENT",
                status="received",
            )
        )
        db.commit()
    (sources["project"] / "backend/app/main.py").write_text("changed source")
    path = f"/api/v1/repair-handoffs/{handoff['id']}"
    assert client.get(path, headers=admin_headers).json()["packet"] == handoff["packet"]
    result = client.get(path + "/export?format=json", headers=admin_headers)
    assert result.json()["packet"] == handoff["packet"]
    assert "PRIVATE CHAT CONTENT" not in result.text and '"config"' not in result.text
    assert result.headers["cache-control"] == "no-store"
    md = client.get(path + "/export?format=markdown", headers=admin_headers)
    assert "backend/app/main.py" in md.text and "snippet_hash" in md.text
    assert "timeout = 20" in md.text and handoff["packet_hash"] in md.text
    assert client.get(path + "/export?format=html", headers=admin_headers).status_code == 422


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
def test_rbac(client, admin_headers, make_user, login, incident, formal, handoff, role):
    make_user("repairs-" + role, role)
    auth = {"Authorization": "Bearer " + login("repairs-" + role)["access_token"]}
    path = f"/api/v1/repair-handoffs/{handoff['id']}"
    for suffix in ("", "/export?format=json", "/export?format=markdown"):
        assert client.get(path + suffix, headers=auth).status_code == 200
    listed = client.get(
        f"/api/v1/incidents/{incident['incident_id']}/repair-handoffs?limit=1", headers=auth
    )
    assert listed.headers["x-total-count"] == "1"
    response = add_result(client, auth, handoff)
    assert response.status_code == (403 if role == "viewer" else 201)
    response = client.post(
        f"/api/v1/incidents/{incident['incident_id']}/repair-handoffs",
        headers=auth,
        json={"analysis_id": formal, "acceptance_criteria": ["test"]},
    )
    assert response.status_code == (403 if role == "viewer" else 201)
    if role == "viewer":
        assert (
            client.post(
                path + "/verifications", headers=auth, json=verification_body({"id": str(uuid4())})
            ).status_code
            == 403
        )
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("change", ["failed", "wrong_incident", "bad_reference", "unavailable"])
def test_reject_nonformal_or_invalid_evidence(client, admin_headers, incident, formal, change):
    with SessionLocal() as db:
        row = db.get(AgentAnalysis, UUID(formal))
        if change == "failed":
            row.status = "failed"
        elif change == "wrong_incident":
            other = Incident(
                title="other",
                severity="warning",
                started_at=datetime.now(UTC),
                last_alert_started_at=datetime.now(UTC),
            )
            db.add(other)
            db.flush()
            row.incident_id = other.id
        elif change == "bad_reference":
            row.evidence = []
        else:
            row.evidence = [{**row.evidence[0], "error_code": "source_unavailable"}]
        db.commit()
    response = client.post(
        f"/api/v1/incidents/{incident['incident_id']}/repair-handoffs",
        headers=admin_headers,
        json={"analysis_id": formal, "acceptance_criteria": ["test"]},
    )
    assert response.status_code == 409


def test_no_source_still_allows_handoff(client, admin_headers, incident, formal):
    with SessionLocal() as db:
        row = db.get(AgentAnalysis, UUID(formal))
        row.source_context = {}
        row.evidence = [
            {"id": "runtime", "source": "get_container_status", "data": {"status": "stopped"}}
        ]
        row.result = diagnosis("runtime")
        db.commit()
    row = generate(client, admin_headers, incident, formal, reproduction="")
    assert row["application_id"] is None
    assert len(row["packet"]["data_gaps"]) == 3
    assert add_result(client, admin_headers, row, repair_commit="a" * 40).status_code == 201


def test_result_verification_append_and_report_freeze(
    client, admin_headers, incident, handoff, repair_release
):
    response = add_result(client, admin_headers, handoff, repair_commit="b" * 40, **repair_release)
    assert response.status_code == 201, response.text
    row = response.json()
    result = row["results"][0]
    assert result["content_hash"] == digest(
        {k: v for k, v in result.items() if k != "content_hash"}
    )
    assert result["release"]["version"] == "v3"
    path = f"/api/v1/repair-handoffs/{handoff['id']}/verifications"
    response = client.post(path, headers=admin_headers, json=verification_body(result))
    assert response.status_code == 201, response.text
    verified = response.json()
    assert verified["packet_hash"] == handoff["packet_hash"]
    assert verified["updated_at"] > handoff["updated_at"]
    assert verified["verifications"][0]["basis"] == "human_reported"
    assert verified["verifications"][0]["alert_recovery"][0]["recovery_status"] == "pending"
    with SessionLocal() as db:
        report = snapshot(db, db.get(Incident, UUID(incident["incident_id"])))
        frozen = next(r for r in report["records"] if r["kind"] == "repair_handoff")
        assert frozen["data"]["packet"] == handoff["packet"]
    client.post(path, headers=admin_headers, json=verification_body(result, outcome="failed"))
    assert len(frozen["data"]["verifications"]) == 1
    with SessionLocal() as db:
        assert len(db.get(RepairHandoff, UUID(handoff["id"])).verifications) == 2
        assert db.get(Incident, UUID(incident["incident_id"])).status == "open"
        assert len(db.scalars(select(AuditLog).where(AuditLog.action.like("repair_%"))).all()) == 4


@pytest.mark.parametrize(
    "invalid", ["commit", "release", "old_release", "target", "environment", "time"]
)
def test_repair_association_validation(
    client, admin_headers, handoff, repair_release, sources, invalid
):
    args = {**repair_release, "repair_commit": "b" * 40}
    with SessionLocal() as db:
        deployment = db.get(ApplicationDeployment, UUID(repair_release["deployment_id"]))
        if invalid == "commit":
            args["repair_commit"] = "c" * 40
        elif invalid == "release":
            args["release_id"] = str(sources["releases"][0])
        elif invalid == "old_release":
            args = {"release_id": str(sources["releases"][1])}
        elif invalid == "target":
            other = Container(
                server_id=deployment.server_id,
                docker_id="d" * 64,
                name="unrelated",
                image="other",
                status="running",
                labels={},
                last_seen_at=datetime.now(UTC),
            )
            db.add(other)
            db.flush()
            deployment.container_id = other.id
        elif invalid == "environment":
            deployment.environment = "other"
        elif invalid == "time":
            deployment.started_at = datetime.now(UTC) - timedelta(days=1)
        db.commit()
    assert add_result(client, admin_headers, handoff, **args).status_code == 422


@pytest.mark.parametrize("invalid", ["unknown_result", "future", "old", "no_deployment", "ended"])
def test_verification_validation(client, admin_headers, handoff, repair_release, invalid):
    response = add_result(
        client, admin_headers, handoff, **({} if invalid == "no_deployment" else repair_release)
    )
    assert response.status_code == 201
    result = response.json()["results"][0]
    body = verification_body(result)
    if invalid == "unknown_result":
        body["result_id"] = str(uuid4())
    elif invalid == "future":
        body["observed_at"] = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    elif invalid == "old":
        body["observed_at"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    elif invalid == "ended":
        with SessionLocal() as db:
            db.get(
                ApplicationDeployment, UUID(repair_release["deployment_id"])
            ).ended_at = datetime.now(UTC)
            db.commit()
        body["observed_at"] = datetime.now(UTC).isoformat()
    response = client.post(
        f"/api/v1/repair-handoffs/{handoff['id']}/verifications", headers=admin_headers, json=body
    )
    assert response.status_code == 422, response.text


def test_concurrent_appends_preserve_both(client, admin_headers, handoff):
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: add_result(client, admin_headers, handoff), range(2)))
    assert [r.status_code for r in responses] == [201, 201]
    assert (
        len(
            client.get(f"/api/v1/repair-handoffs/{handoff['id']}", headers=admin_headers).json()[
                "results"
            ]
        )
        == 2
    )


def test_closed_incident_can_track_repair_merged_cannot(client, admin_headers, handoff, incident):
    with SessionLocal() as db:
        row = db.get(Incident, UUID(incident["incident_id"]))
        row.status = "closed"
        db.commit()
    assert add_result(client, admin_headers, handoff).status_code == 201
    with SessionLocal() as db:
        db.get(Incident, UUID(incident["incident_id"])).status = "merged"
        db.commit()
    assert add_result(client, admin_headers, handoff).status_code == 409


def test_budget_and_soft_delete(client, admin_headers, handoff, monkeypatch):
    import app.services.repairs.handoffs as repairs

    monkeypatch.setattr(repairs, "MAX_BYTES", 1)
    assert add_result(client, admin_headers, handoff).status_code == 422
    with SessionLocal() as db:
        row = db.get(RepairHandoff, UUID(handoff["id"]))
        assert row.results == []
        row.deleted_at = datetime.now(UTC)
        db.commit()
    assert (
        client.get(f"/api/v1/repair-handoffs/{handoff['id']}", headers=admin_headers).status_code
        == 404
    )
