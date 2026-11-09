"""Assemble one object using explicit, bounded read-only key lookups."""

import json

from fastapi import HTTPException

from app.schemas.inspections import InspectionRecord
from app.services import service_databases
from app.services.inspections import sources
from app.services.inspections.checkers import field


def compose(db, plan, run, record, guard):
    if not plan.related_sources:
        return record
    original = record.model_dump(mode="json")
    facts = {"main": record.facts}
    evidence = [{"source": "main", "record_id": record.id, "evidence": record.evidence}]
    versions = [
        {
            "name": "main",
            "id": record.id,
            "version": record.version,
            "updated_at": record.updated_at.isoformat(),
        }
    ]
    identities = [[record.id, record.version, service_databases.digest(record.facts)]]
    issues = []
    for source in plan.related_sources:
        name = source["name"]
        facts[name] = [] if source["cardinality"] == "many" else None
        rows, problem = [], None
        guard()
        try:
            key = field(record.facts, source["main_field"]) if source["main_field"] else record.id
            if isinstance(key, bool) or not isinstance(key, (str, int)) or not str(key):
                raise ValueError("invalid_join_key")
            rows, truncated = sources.lookup_records(db, plan, run, source, str(key))
            if any(str(field(row.facts, source["match_field"])) != str(key) for row in rows):
                rows, problem = [], "join_key_mismatch"
            elif truncated:
                problem = (
                    "duplicate_matches"
                    if source["cardinality"] == "one"
                    else "related_record_limit"
                )
            elif not rows and source["required"]:
                problem = "related_record_missing"
        except HTTPException:
            raise
        except (KeyError, IndexError, TypeError):
            rows = []
            problem = "join_field_missing"
        except Exception as exc:
            rows = []
            problem = (
                "invalid_join_key"
                if str(exc) == "invalid_join_key"
                else "related_source_unavailable"
            )
        guard()
        if source["cardinality"] == "many":
            facts[name] = [row.facts for row in rows]
        elif len(rows) == 1 and not problem:
            facts[name] = rows[0].facts
        if problem:
            issues.append({"source": name, "reason": problem})
        identities.append(
            [name, problem, [[r.id, r.version, service_databases.digest(r.facts)] for r in rows]]
        )
        for row in rows:
            versions.append(
                {
                    "name": name,
                    "id": row.id,
                    "version": row.version,
                    "updated_at": row.updated_at.isoformat(),
                }
            )
        evidence.append(
            {
                "source": name,
                "records": [{"record_id": row.id, "evidence": row.evidence} for row in rows],
            }
        )
    facts["_inspection"] = {"sources": versions, "issues": issues}
    value = {
        **original,
        "version": service_databases.digest(identities),
        "facts": facts,
        "evidence": evidence,
    }
    if len(json.dumps(value, ensure_ascii=True).encode()) > 32768:
        value["facts"] = {
            "_inspection": {
                "sources": versions[:8],
                "issues": [{"source": "assembly", "reason": "assembled_record_too_large"}],
            }
        }
        value["evidence"] = []
    return InspectionRecord.model_validate(value)
