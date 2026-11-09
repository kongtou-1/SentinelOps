"""阶段 7 部署后端到端冒烟：连接、构建、代码依据、问数、权限、导出。

依赖：平台库已迁移到 head；postgres 中存在 askdata_smoke 业务库。
用法：python backend/scripts/smoke_askdata_stage7_e2e.py [--cleanup]
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
import uuid

import httpx
from askdata_smoke_support import admin_credentials

BASE = "http://127.0.0.1:8000"
PG = {
    "host": "postgres",
    "port": 5432,
    "database": "askdata_smoke",
    "username": "askdata_smoke_ro",
    "password": os.environ.get("ASKDATA_SMOKE_DB_PASSWORD", ""),
}
ALLOWED_TABLES = {
    "public.customer": ["id", "name", "city", "created_at"],
    "public.sales_order": [
        "id",
        "customer_id",
        "status",
        "amount_cent",
        "paid_at",
        "deleted",
        "created_at",
    ],
}

results: list[tuple[str, str, str]] = []


def record(name: str, ok: bool, detail: str) -> None:
    results.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} :: {detail}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--credentials-file")
    args = parser.parse_args()
    try:
        credentials = admin_credentials(args.credentials_file)
    except ValueError as exc:
        parser.error(str(exc))
    if not PG["password"]:
        parser.error("ASKDATA_SMOKE_DB_PASSWORD is required")

    with httpx.Client(base_url=args.base, trust_env=False, timeout=120.0) as client:
        token = _login(client, credentials)
        auth = {"Authorization": f"Bearer {token}"}

        # 1. 权限：未认证必须被拒
        anonymous = client.get("/api/v1/askdata/settings/connections")
        record("权限-未认证拦截", anonymous.status_code == 401, f"HTTP {anonymous.status_code}")

        # 2. 连接：注册业务库并解析为问数连接
        connection, created_connection = _register_connection(client, auth)
        connection_id = connection["id"]
        record(
            "连接-注册",
            bool(connection_id),
            f"connection={connection_id} engine={connection.get('engine')}",
        )

        resolved = client.get("/api/v1/askdata/settings/connections", headers=auth).json()
        record(
            "连接-问数侧可见",
            any(str(item["id"]) == str(connection_id) for item in resolved),
            f"{len(resolved)} 个连接",
        )

        # 该接口探测的是重排服务；本部署未配置重排，409 是预期结果。
        rerank = client.post(
            f"/api/v1/askdata/settings/{connection_id}/execution/test",
            headers=auth,
        )
        record(
            "重排-未配置时拒绝",
            rerank.status_code == 409,
            f"HTTP {rerank.status_code} {rerank.text[:120]}",
        )

        # 3. 构建：提交版本并等待 worker 完成
        version = _build(client, auth, connection_id)
        version_id = version["id"]
        status = version.get("status")
        record(
            "构建-完成", status in {"ready", "published"}, f"status={status} version={version_id}"
        )

        tables = client.get(f"/api/v1/askdata/versions/{version_id}/tables", headers=auth).json()
        rows = tables.get("items", []) if isinstance(tables, dict) else tables
        names = sorted(
            {
                item if isinstance(item, str) else item.get("name")
                for item in rows
                if (item if isinstance(item, str) else item.get("name"))
            }
        )
        record("构建-目录", "customer" in names and "sales_order" in names, f"tables={names}")

        rules = client.get(f"/api/v1/askdata/versions/{version_id}/code-rules", headers=auth).json()
        rule_items = rules.get("items") if isinstance(rules, dict) else rules
        record("代码依据-无源码时为空", not rule_items, f"code_rules={str(rules)[:120]}")

        published = client.post(
            f"/api/v1/askdata/versions/{version_id}/publish", headers=auth, json={}
        )
        record(
            "版本-发布",
            published.status_code < 300,
            f"HTTP {published.status_code} {published.text[:120]}",
        )

        # 4. 问数：提交问题并等待 run 终态
        _raise_budget(client, auth, connection_id, 900)
        run: dict = {}
        try:
            run = _ask(client, auth, connection_id, "最近 30 天已支付订单金额合计是多少？")
            record(
                "问数-任务受理",
                bool(run.get("id")),
                f"run={run.get('id')} status={run.get('status')}",
            )
            run = _wait_run(client, auth, run["id"])
            result = run.get("result") or {}
            answered = run.get("status") in {"completed", "succeeded"} and bool(result.get("sql"))
            record(
                "问数-终态",
                answered,
                f"status={run.get('status')} sql={(result.get('sql') or '')[:120]} "
                f"rows={result.get('row_count')} answer={(result.get('answer') or '')[:120]}",
            )
        except Exception as error:  # noqa: BLE001 - 冒烟需继续跑后续项
            record("问数-任务受理", False, f"{type(error).__name__}: {str(error)[:200]}")

        # 5. 导出：运行结果 CSV 与配置导出
        if run.get("id"):
            exported = client.get(f"/api/v1/askdata/runs/{run['id']}/export", headers=auth)
            record(
                "导出-运行结果",
                exported.status_code < 300,
                f"HTTP {exported.status_code} bytes={len(exported.content)}",
            )
        else:
            record("导出-运行结果", False, "无可用运行记录")
        config = client.get(f"/api/v1/askdata/settings/{connection_id}/export", headers=auth)
        record(
            "导出-配置",
            config.status_code < 300,
            f"HTTP {config.status_code} bytes={len(config.content)}",
        )

        # 6. 权限：只读账号运行问数应被拒
        viewer_status = _viewer_cannot_run(client, auth, connection_id)
        record("权限-只读不可运行", viewer_status == 403, f"HTTP {viewer_status}")

        if args.cleanup and created_connection:
            _cleanup(client, auth, connection_id)

    failed = [row for row in results if row[1] == "FAIL"]
    print()
    print(json.dumps({"total": len(results), "failed": len(failed)}, ensure_ascii=False))
    for name, _, detail in failed:
        print(f"  - {name}: {detail}")
    return 1 if failed else 0


def _login(client: httpx.Client, credentials: dict) -> str:
    response = client.post("/api/v1/auth/login", json=credentials)
    response.raise_for_status()
    return response.json()["access_token"]


def _register_connection(client: httpx.Client, auth: dict) -> tuple[dict, bool]:
    payload = {
        "name": "冒烟业务库",
        "engine": "postgresql",
        "host": PG["host"],
        "port": PG["port"],
        "database": PG["database"],
        "username": PG["username"],
        "password": PG["password"],
        "tls_mode": "disable",
        "environment": "staging",
        "purpose": "阶段 7 部署冒烟",
        "enabled": True,
        "query_enabled": False,
    }
    existing = [
        item
        for item in client.get("/api/v1/service-databases", headers=auth).json()
        if item.get("name") == payload["name"]
    ]
    if existing:
        row = existing[0]
        if any(
            row.get(key) != payload[key]
            for key in ("engine", "host", "port", "database", "username")
        ):
            raise RuntimeError("An existing connection with this name targets a different database")
    else:
        response = client.post("/api/v1/service-databases", headers=auth, json=payload)
        if response.status_code != 201:
            raise RuntimeError(f"注册业务库失败 HTTP {response.status_code}: {response.text[:300]}")
        row = response.json()

    connection_id = row["id"]
    refresh = client.post(f"/api/v1/service-databases/{connection_id}/schema/refresh", headers=auth)
    record(
        "连接-结构刷新",
        refresh.status_code < 300,
        f"HTTP {refresh.status_code} {refresh.text[:160]}",
    )

    authorized = client.put(
        f"/api/v1/service-databases/{connection_id}",
        headers=auth,
        json={
            **{k: v for k, v in payload.items() if k != "password"},
            "allowed_tables": ALLOWED_TABLES,
            "query_enabled": True,
        },
    )
    if authorized.status_code >= 300:
        raise RuntimeError(f"授权表列失败 HTTP {authorized.status_code}: {authorized.text[:300]}")
    return authorized.json(), not bool(existing)


def _build(client: httpx.Client, auth: dict, connection_id: str) -> dict:
    response = client.post(
        f"/api/v1/askdata/connections/{connection_id}/versions",
        headers=auth,
        json={},
    )
    response.raise_for_status()
    version = response.json()
    deadline = time.time() + 600
    while time.time() < deadline:
        current = client.get(f"/api/v1/askdata/versions/{version['id']}", headers=auth).json()
        if current.get("status") in {"ready", "published", "failed"}:
            return current
        time.sleep(2)
    return version


def _raise_budget(client: httpx.Client, auth: dict, connection_id: str, seconds: int) -> None:
    """思考模式下每步 10-25 秒，180 秒默认预算跑不完一次完整问数。"""
    current = client.get(f"/api/v1/askdata/settings/{connection_id}/execution", headers=auth).json()
    if current.get("budget_seconds", 0) >= seconds:
        record("执行-预算", True, f"budget_seconds={current.get('budget_seconds')}")
        return
    payload = {
        "expected_version": current["version"],
        "budget_seconds": seconds,
        "reasoning_channel_id": current.get("reasoning_channel_id"),
        "light_channel_id": current.get("light_channel_id"),
        "vector_model": current.get("vector_model", ""),
        "vector_dimension": current.get("vector_dimension", 512),
        "reranker_enabled": current.get("reranker_enabled", False),
        "reranker_endpoint": current.get("reranker_endpoint", ""),
        "reranker_model": current.get("reranker_model", ""),
    }
    response = client.put(
        f"/api/v1/askdata/settings/{connection_id}/execution",
        headers=auth,
        json=payload,
    )
    record(
        "执行-预算",
        response.status_code < 300,
        f"HTTP {response.status_code} budget_seconds={seconds}",
    )


def _ask(client: httpx.Client, auth: dict, connection_id: str, question: str) -> dict:
    conversation = client.post(
        "/api/v1/askdata/conversations",
        headers=auth,
        json={"connection_id": connection_id, "title": "冒烟"},
    )
    conversation.raise_for_status()
    conversation_id = conversation.json()["id"]
    response = client.post(
        f"/api/v1/askdata/conversations/{conversation_id}/runs",
        headers=auth,
        json={"question": question, "idempotency_key": str(uuid.uuid4())},
    )
    if response.status_code >= 300:
        raise RuntimeError(f"提交问数失败 HTTP {response.status_code}: {response.text[:300]}")
    return response.json()


def _wait_run(client: httpx.Client, auth: dict, run_id: str) -> dict:
    deadline = time.time() + 1000
    while time.time() < deadline:
        current = client.get(f"/api/v1/askdata/runs/{run_id}", headers=auth).json()
        if current.get("status") in {"succeeded", "completed", "failed", "cancelled"}:
            return current
        time.sleep(2)
    return current


def _viewer_cannot_run(client: httpx.Client, auth: dict, connection_id: str) -> int:
    user = {
        "username": "askdata_smoke_" + uuid.uuid4().hex[:12],
        "password": secrets.token_urlsafe(24),
        "role": "viewer",
    }
    created = client.post("/api/v1/users", headers=auth, json=user)
    if created.status_code != 201:
        return created.status_code
    try:
        token = client.post(
            "/api/v1/auth/login", json={"username": user["username"], "password": user["password"]}
        )
        if token.status_code != 200:
            return token.status_code
        headers = {"Authorization": f"Bearer {token.json()['access_token']}"}
        return client.post(
            "/api/v1/askdata/conversations",
            headers=headers,
            json={"connection_id": connection_id},
        ).status_code
    finally:
        client.delete(f"/api/v1/users/{created.json()['id']}", headers=auth).raise_for_status()


def _cleanup(client: httpx.Client, auth: dict, connection_id: str) -> None:
    for method, path in [
        ("DELETE", f"/api/v1/service-databases/{connection_id}"),
    ]:
        client.request(method, path, headers=auth)


if __name__ == "__main__":
    sys.exit(main())
