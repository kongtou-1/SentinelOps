"""Clearly synthetic long-text/table fixtures for export layout verification; no database writes."""

import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.reports.exports import export_report
from app.utils.canonical_json import digest


def main():
    directory = Path(__file__).resolve().parents[2] / ".local/report-qa"
    directory.mkdir(parents=True, exist_ok=True)
    now, incident_id = datetime.now(UTC), uuid4()
    records = [
        {
            "id": "incident:" + str(incident_id),
            "kind": "incident",
            "record_id": str(incident_id),
            "occurred_at": now.isoformat(),
            "content_hash": "0" * 64,
            "data": {
                "title": "排版验收模拟事故",
                "started_at": now.isoformat(),
                "recovered_at": now.isoformat(),
                "closed_at": now.isoformat(),
            },
        }
    ]
    for index in range(12):
        identifier = uuid4()
        records.append(
            {
                "id": "timeline:" + str(identifier),
                "kind": "timeline",
                "record_id": str(identifier),
                "occurred_at": now.isoformat(),
                "content_hash": "1" * 64,
                "data": {
                    "event_type": "comment_added",
                    "content": {
                        "comment": f"第 {index + 1} 条模拟长备注 " + "检查跨页表头和自动换行。" * 30
                    },
                },
            }
        )
    frozen = {
        "title": "排版验收模拟事故",
        "captured_at": now.isoformat(),
        "records": records,
        "data_gaps": [],
    }
    row = SimpleNamespace(
        id=uuid4(),
        incident_id=incident_id,
        revision=1,
        reviewed_by=uuid4(),
        reviewed_at=now,
        snapshot=frozen,
        snapshot_hash=digest(frozen),
    )
    body = {
        "summary": "模拟长文本排版验证。" * 180,
        "impact": "仅为排版验证，无真实事故影响。",
        "causes": [
            {"confidence": "unknown", "description": "模拟内容不用于事实判断。", "evidence_ids": []}
        ],
        "response_and_recovery": "核对长文本、中文、跨页表头、页码和完整来源附录。",
        "lessons": ["保持正文可读"],
        "improvements": [{"action": "核对所有页图", "owner": "验收脚本", "deadline": "验收时"}],
        "data_gaps": ["本样例仅为模拟排版测试"],
        "evidence_ids": [records[0]["id"]],
    }
    for format in ("pdf", "docx"):
        (directory / ("stress." + format)).write_bytes(export_report(row, body, format))
    print("Synthetic long-text/table layout fixtures generated")


if __name__ == "__main__":
    main()
