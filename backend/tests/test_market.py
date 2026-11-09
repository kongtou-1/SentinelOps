from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import delete, inspect, select, text

from app.db.session import SessionLocal, engine
from app.models import MarketAuditLog, MarketCategory, MarketEntry, MarketRevision

BASE = "/api/v1/market"


@pytest.fixture(autouse=True)
def reset_extra_categories(clean_users):
    # The shared fixture clears dependent rows; keep migration seeds, remove test categories.
    names = [
        "办公效率",
        "写作与翻译",
        "科研与学习",
        "数据与分析",
        "设计与创作",
        "开发与运维",
        "其他",
    ]
    with SessionLocal() as db:
        db.execute(delete(MarketCategory).where(MarketCategory.name.not_in(names)))
        db.commit()


@pytest.fixture
def people(make_user, login):
    headers = {}
    for role in ("viewer", "operator", "admin"):
        make_user(role, role)
        headers[role] = {"Authorization": "Bearer " + login(role)["access_token"]}
    return headers


def payload(client, headers, kind="skill", title="办公助手"):
    categories = client.get(BASE + "/categories", headers=headers).json()
    category = next(c for c in categories if c["name"] == "办公效率")
    return {
        "kind": kind,
        "category_id": category["id"],
        "title": title,
        "summary": "整理会议记录与文档",
        "tags": ["办公", "会议"],
        "description": "## 功能\n整理文档",
        "usage": "打开来源页面，按说明使用。",
        "target_url": "https://example.org/office",
        "author": "测试作者",
        "reason": "适合日常协作",
    }


def create(client, headers, **kwargs):
    response = client.post(
        BASE + "/entries", headers=headers, json=payload(client, headers, **kwargs)
    )
    assert response.status_code == 201, response.text
    return response.json()


def act(client, headers, revision, action, **extra):
    return client.post(
        BASE + f"/revisions/{revision['id']}/{action}",
        headers=headers,
        json={"expected_version": revision["edit_version"], **extra},
    )


def publish(client, owner, admin, entry):
    response = act(client, owner, entry["revision"], "submit")
    assert response.status_code == 200, response.text
    response = act(client, admin, response.json(), "review", decision="approve")
    assert response.status_code == 200, response.text
    return response.json()


def save(client, headers, entry, body):
    return client.put(
        BASE + f"/entries/{entry['id']}/draft",
        headers=headers,
        json={
            **{k: v for k, v in body.items() if k != "kind"},
            "base_revision_id": entry["revision"]["id"],
            "expected_version": entry["revision"]["edit_version"],
        },
    )


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
def test_roles_can_submit_but_only_reviewed_content_is_visible(client, people, role):
    headers = people[role]
    entry = create(client, headers)
    assert client.get(BASE + "/entries", headers=headers).json() == []
    assert client.get(BASE + f"/entries/{entry['id']}", headers=headers).status_code == 404
    counts = client.get(BASE + "/categories?kind=skill", headers=headers).json()
    assert sum(c["count"] for c in counts) == 0
    mine = client.get(BASE + "/entries?scope=mine", headers=headers)
    assert mine.headers["X-Total-Count"] == "1"
    approved = publish(client, headers, people["admin"], entry)
    result = client.get(BASE + "/entries?kind=skill&q=会议", headers=people["viewer"])
    assert result.status_code == 200 and result.headers["X-Total-Count"] == "1"
    assert result.json()[0]["revision"]["id"] == approved["id"]
    assert client.get(BASE + "/entries?kind=tool", headers=headers).json() == []
    assert (
        sum(c["count"] for c in client.get(BASE + "/categories?kind=skill", headers=headers).json())
        == 1
    )


def test_owner_and_admin_authorization_cannot_be_bypassed(client, people):
    entry = create(client, people["viewer"])
    path = BASE + f"/entries/{entry['id']}/workbench"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=people["operator"]).status_code == 403
    assert client.get(path, headers=people["admin"]).status_code == 200
    for role in ("viewer", "operator"):
        assert client.get(BASE + "/entries?scope=manage", headers=people[role]).status_code == 403
        assert (
            client.post(
                BASE + "/categories", headers=people[role], json={"name": "越权分类"}
            ).status_code
            == 403
        )
        assert (
            act(client, people[role], entry["revision"], "review", decision="approve").status_code
            == 403
        )
        assert client.get(BASE + "/category-audit", headers=people[role]).status_code == 403
    body = payload(client, people["viewer"])
    assert save(client, people["admin"], entry, body).status_code == 403
    assert save(client, people["operator"], entry, body).status_code == 403
    assert (
        client.post(
            BASE + "/entries", headers=people["viewer"], json={**body, "listed": True}
        ).status_code
        == 422
    )


def test_pending_is_locked_with_withdraw_and_stale_review_protection(client, people):
    owner, admin = people["viewer"], people["admin"]
    entry = create(client, owner)
    submitted = act(client, owner, entry["revision"], "submit").json()
    current = {**entry, "revision": submitted}
    assert save(client, owner, current, payload(client, owner)).status_code == 409
    withdrawn = act(client, owner, submitted, "withdraw")
    assert withdrawn.status_code == 200
    assert act(client, admin, submitted, "review", decision="approve").status_code == 409
    assert act(client, owner, entry["revision"], "submit").status_code == 409
    submitted = act(client, owner, withdrawn.json(), "submit").json()
    assert act(client, admin, submitted, "review", decision="approve").status_code == 200
    assert act(client, admin, submitted, "review", decision="approve").status_code == 409


def test_published_update_keeps_old_version_until_approved(client, people):
    owner, admin = people["operator"], people["admin"]
    entry = create(client, owner)
    approved = publish(client, owner, admin, entry)
    body = payload(client, owner, title="新版本办公助手")
    response = save(client, owner, {**entry, "revision": approved}, body)
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["revision"]["number"] == 2
    public = client.get(BASE + f"/entries/{entry['id']}", headers=owner).json()
    assert public["revision"]["title"] == "办公助手"
    assert client.get(BASE + "/entries?q=新版本", headers=owner).json() == []
    assert client.get(BASE + "/entries?q=%", headers=owner).json() == []
    pending = act(client, owner, updated["revision"], "submit").json()
    assert (
        client.get(BASE + "/entries", headers=owner).json()[0]["revision"]["id"] == approved["id"]
    )
    assert act(client, admin, pending, "review", decision="approve").status_code == 200
    public = client.get(BASE + f"/entries/{entry['id']}", headers=owner).json()
    assert public["revision"]["title"] == "新版本办公助手"
    workbench = client.get(BASE + f"/entries/{entry['id']}/workbench", headers=owner).json()
    assert len(workbench["revisions"]) == 2
    assert workbench["revisions"][1]["title"] == "办公助手"


def test_rejected_version_remains_immutable_when_resubmitting(client, people):
    owner, admin = people["viewer"], people["admin"]
    entry = create(client, owner, kind="tool")
    pending = act(client, owner, entry["revision"], "submit").json()
    assert act(client, admin, pending, "review", decision="reject").status_code == 422
    rejected = act(client, admin, pending, "review", decision="reject", note="补充说明")
    assert rejected.status_code == 200
    result = save(
        client,
        owner,
        {**entry, "revision": rejected.json()},
        payload(client, owner, kind="tool", title="修改后的工具"),
    )
    assert result.status_code == 200
    assert result.json()["revision"]["id"] != rejected.json()["id"]
    workbench = client.get(BASE + f"/entries/{entry['id']}/workbench", headers=owner).json()
    assert workbench["revisions"][1]["review_note"] == "补充说明"
    assert workbench["revisions"][1]["title"] == "办公助手"


def test_unpublish_hides_everywhere_invalidates_pending_and_requires_review(client, people):
    owner, admin = people["viewer"], people["admin"]
    entry = create(client, owner)
    approved = publish(client, owner, admin, entry)
    draft = save(client, owner, {**entry, "revision": approved}, payload(client, owner)).json()
    pending = act(client, owner, draft["revision"], "submit").json()
    path = BASE + f"/entries/{entry['id']}/unpublish"
    assert client.post(path, headers=owner, json={"note": "下架"}).status_code == 403
    assert client.post(path, headers=admin, json={"note": "来源需要复查"}).status_code == 200
    assert client.get(BASE + "/entries", headers=owner).json() == []
    assert client.get(BASE + f"/entries/{entry['id']}", headers=admin).status_code == 404
    assert sum(c["count"] for c in client.get(BASE + "/categories", headers=owner).json()) == 0
    assert act(client, admin, pending, "review", decision="approve").status_code == 409
    workbench = client.get(BASE + f"/entries/{entry['id']}/workbench", headers=owner).json()
    assert workbench["revision"]["status"] == "draft"
    publish(client, owner, admin, workbench)
    assert client.get(BASE + f"/entries/{entry['id']}", headers=owner).status_code == 200


def test_category_management_unique_stale_disable_and_delete(client, people):
    admin, owner = people["admin"], people["viewer"]
    response = client.post(BASE + "/categories", headers=admin, json={"name": "专项分类"})
    assert response.status_code == 201
    identifier = response.json()["id"]
    assert (
        client.post(BASE + "/categories", headers=admin, json={"name": "专项分类"}).status_code
        == 409
    )
    body = payload(client, owner)
    body["category_id"] = identifier
    entry = client.post(BASE + "/entries", headers=owner, json=body).json()
    pending = act(client, owner, entry["revision"], "submit").json()
    category = next(
        c for c in client.get(BASE + "/categories", headers=admin).json() if c["id"] == identifier
    )
    change = {
        "name": "专项分类更新",
        "description": "新说明",
        "sort_order": 5,
        "enabled": False,
        "expected_updated_at": category["updated_at"],
    }
    path = BASE + "/categories/" + identifier
    assert client.put(path, headers=admin, json=change).status_code == 200
    assert client.put(path, headers=admin, json=change).status_code == 409
    assert client.post(BASE + "/entries", headers=owner, json=body).status_code == 409
    assert act(client, admin, pending, "review", decision="approve").status_code == 409
    assert (
        act(client, admin, pending, "review", decision="reject", note="分类已停用").status_code
        == 200
    )
    assert client.delete(path, headers=admin).status_code == 409
    spare = client.post(BASE + "/categories", headers=admin, json={"name": "空分类"}).json()
    assert client.delete(BASE + "/categories/" + spare["id"], headers=admin).status_code == 204
    assert spare["id"] not in {
        c["id"] for c in client.get(BASE + "/categories", headers=owner).json()
    }
    assert len(client.get(BASE + "/category-audit", headers=admin).json()) >= 4


@pytest.mark.parametrize(
    "link", ["javascript:alert(1)", "file:///C:/secret", "https://a:b@example.org"]
)
def test_external_links_reject_script_file_and_credentials(client, people, link):
    body = {**payload(client, people["viewer"]), "target_url": link}
    assert client.post(BASE + "/entries", headers=people["viewer"], json=body).status_code == 422


def test_submit_requires_complete_content_and_category_counts_are_type_specific(client, people):
    owner, admin = people["viewer"], people["admin"]
    body = payload(client, owner, kind="tool")
    body["usage"] = ""
    entry = client.post(BASE + "/entries", headers=owner, json=body).json()
    assert act(client, owner, entry["revision"], "submit").status_code == 422
    body["usage"], body["reason"] = "按说明操作", ""
    draft = save(client, owner, entry, body).json()
    assert act(client, owner, draft["revision"], "submit").status_code == 422
    body["reason"] = "适合办公"
    draft = save(client, owner, draft, body).json()
    publish(client, owner, admin, draft)
    assert (
        sum(c["count"] for c in client.get(BASE + "/categories?kind=skill", headers=owner).json())
        == 0
    )
    assert (
        sum(c["count"] for c in client.get(BASE + "/categories?kind=tool", headers=owner).json())
        == 1
    )


def test_market_migration_metadata_timestamps_and_audit(client, people, migrated_database):
    inspector = inspect(engine)
    for table in (
        "market_categories",
        "market_entries",
        "market_entry_revisions",
        "market_audit_logs",
    ):
        columns = {c["name"]: c for c in inspector.get_columns(table)}
        for field in ("created_at", "updated_at", "deleted_at"):
            assert columns[field]["type"].timezone
        assert not columns["created_at"]["nullable"] and columns["created_at"]["default"]
        assert not columns["updated_at"]["nullable"] and columns["updated_at"]["default"]
        assert columns["deleted_at"]["nullable"]
    entry = create(client, people["viewer"])
    approved = publish(client, people["viewer"], people["admin"], entry)
    with SessionLocal() as db:
        row = db.get(MarketEntry, UUID(entry["id"]))
        assert row.listed and row.published_revision_id == UUID(approved["id"])
        assert db.get(MarketRevision, UUID(approved["id"])).reviewer_id
        log = db.scalars(select(MarketAuditLog).where(MarketAuditLog.entry_id == row.id)).all()
        assert {r.action for r in log} == {"draft_created", "submitted", "approved"}
        category = db.get(MarketCategory, UUID(approved["category_id"]))
        before = category.updated_at
        db.execute(
            text("UPDATE market_categories SET description = description WHERE id = :id"),
            {"id": category.id},
        )
        db.commit()
        db.refresh(category)
        assert before < category.updated_at <= datetime.now(UTC)
    command.check(migrated_database)


def test_concurrent_review_publishes_once(client, people):
    entry = create(client, people["viewer"])
    pending = act(client, people["viewer"], entry["revision"], "submit").json()
    barrier = Barrier(2)

    def review_once():
        barrier.wait(timeout=5)
        return act(client, people["admin"], pending, "review", decision="approve").status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: review_once(), range(2)))
    assert sorted(results) == [200, 409]
    with SessionLocal() as db:
        events = db.scalars(
            select(MarketAuditLog).where(
                MarketAuditLog.entry_id == UUID(entry["id"]), MarketAuditLog.action == "approved"
            )
        ).all()
        assert len(events) == 1


def test_disabling_category_keeps_existing_publication(client, people):
    owner, admin = people["viewer"], people["admin"]
    identifier = client.post(
        BASE + "/categories", headers=admin, json={"name": "保留内容分类"}
    ).json()["id"]
    body = {**payload(client, owner), "category_id": identifier}
    entry = client.post(BASE + "/entries", headers=owner, json=body).json()
    publish(client, owner, admin, entry)
    category = next(
        c for c in client.get(BASE + "/categories", headers=admin).json() if c["id"] == identifier
    )
    response = client.put(
        BASE + "/categories/" + identifier,
        headers=admin,
        json={
            "name": category["name"],
            "enabled": False,
            "expected_updated_at": category["updated_at"],
        },
    )
    assert response.status_code == 200
    assert client.get(BASE + f"/entries/{entry['id']}", headers=owner).status_code == 200
    assert len(client.get(BASE + "/entries?category_id=" + identifier, headers=owner).json()) == 1
