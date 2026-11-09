"""阶段 7 部署后冒烟：登录、问数接口、旧功能回归。

用法：python backend/scripts/smoke_askdata_stage7.py [--base http://127.0.0.1:8000]
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx
from askdata_smoke_support import admin_credentials

LEGACY = [
    ("GET", "/api/v1/applications"),
    ("GET", "/api/v1/business-services"),
    ("GET", "/api/v1/service-databases"),
    ("GET", "/api/v1/reports"),
    ("GET", "/api/v1/notifications"),
    ("GET", "/api/v1/incidents?limit=5"),
    ("GET", "/api/v1/alerts?limit=5"),
    ("GET", "/api/v1/audit-logs?limit=5"),
    ("GET", "/api/v1/approvals"),
    ("GET", "/api/v1/access-grants"),
    ("GET", "/api/v1/agent-configs"),
    ("GET", "/api/v1/llm-bindings"),
]

ASKDATA = [
    ("GET", "/api/v1/askdata/settings/connections"),
    ("GET", "/api/v1/askdata/statistics"),
    ("GET", "/api/v1/askdata/conversations"),
    ("GET", "/api/v1/askdata/analyses"),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--credentials-file")
    args = parser.parse_args()
    try:
        credentials = admin_credentials(args.credentials_file)
    except ValueError as exc:
        parser.error(str(exc))

    rows: list[tuple[str, str, int, str]] = []
    with httpx.Client(base_url=args.base, trust_env=False, timeout=30.0) as client:
        health = client.get("/health/ready")
        rows.append(("GET", "/health/ready", health.status_code, health.text[:80]))

        login = client.post("/api/v1/auth/login", json=credentials)
        rows.append(
            (
                "POST",
                "/api/v1/auth/login",
                login.status_code,
                "authenticated" if login.status_code == 200 else "login failed",
            )
        )
        if login.status_code != 200:
            _report(rows)
            return 1
        token = login.json().get("access_token") or login.json().get("token")
        headers = {"Authorization": f"Bearer {token}"} if token else {}

        for method, path in ASKDATA + LEGACY:
            response = client.request(method, path, headers=headers)
            body = response.text.replace("\n", " ")[:100]
            rows.append((method, path, response.status_code, body))

    _report(rows)
    failed = [row for row in rows if row[2] >= 400]
    if failed:
        print(f"\n失败 {len(failed)} 项")
        return 1
    print(f"\n全部 {len(rows)} 项通过")
    return 0


def _report(rows: list[tuple[str, str, int, str]]) -> None:
    print(f"{'METHOD':7}{'PATH':46}{'CODE':6}BODY")
    for method, path, code, body in rows:
        print(f"{method:7}{path:46}{code:<6}{body}")
    print()
    print(json.dumps({"total": len(rows), "failed": sum(1 for r in rows if r[2] >= 400)}))


if __name__ == "__main__":
    sys.exit(main())
