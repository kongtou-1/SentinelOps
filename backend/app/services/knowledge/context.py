"""Deterministic context budgets; persistence is never truncated by these projections."""

import json


def carry_records(records, references=(), *, max_items=30, max_chars=128000):
    """Keep cited records first, then recent records; never modify their original contents."""
    priority = set(references)
    ordered = [r for r in records if r["id"] in priority]
    ordered += [r for r in reversed(records) if r["id"] not in priority]
    selected, size = set(), 0
    for record in ordered:
        length = len(json.dumps(record, ensure_ascii=False, default=str)) + 2
        if len(selected) >= max_items:
            break
        if length + size <= max_chars:
            selected.add(record["id"])
            size += length
    return [record for record in records if record["id"] in selected]


def bounded(value, limit):
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return value
    return {"truncated": True, "total_chars": len(text), "excerpt": text[: limit - 150]}


def recent_messages(messages, limit=12000):
    result, remaining = [], limit
    for message in reversed(messages):
        if remaining < 200:
            break
        content = message.content
        if len(content) > min(3000, remaining):
            content = content[: min(3000, remaining) - 30] + "\n[历史消息已截取]"
        result.append({"role": message.role, "content": content})
        remaining -= len(content) + 100
    return list(reversed(result))


def evidence_catalog(evidence, limit=8000):
    """Initial prompts carry metadata; history tools load exact old evidence on demand."""
    result = []
    for item in reversed(evidence):
        entry = {
            key: item.get(key)
            for key in (
                "id",
                "source",
                "asset_id",
                "observed_at",
                "error_code",
                "history_ref",
            )
        }
        if len(json.dumps([*result, entry], ensure_ascii=False)) > limit:
            break
        result.append(entry)
    return list(reversed(result))
