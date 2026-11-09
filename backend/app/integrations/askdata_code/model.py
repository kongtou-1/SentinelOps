"""Shared value types for the static source-rule extractors."""

from __future__ import annotations

from dataclasses import dataclass, field

RULE_KINDS = frozenset(
    {
        "table_mapping",
        "column_mapping",
        "join",
        "status_filter",
        "soft_delete",
        "amount_unit",
        "time_field",
        "metric",
        "enum_value",
        "filter",
        "sql_fragment",
    }
)


@dataclass(frozen=True)
class RuleDraft:
    """One code rule with the exact repository location that produced it."""

    language: str
    path: str
    start_line: int
    end_line: int
    rule_kind: str
    object_key: str
    expression: str
    parse_state: str = "static"
    sql_text: str = ""
    snippet: str = ""
    detail: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.rule_kind not in RULE_KINDS:
            raise ValueError(f"unknown_code_rule_kind:{self.rule_kind}")
        if self.parse_state not in ("static", "dynamic"):
            raise ValueError(f"unknown_parse_state:{self.parse_state}")
        if self.parse_state == "dynamic" and self.sql_text:
            raise ValueError("dynamic_fragment_cannot_carry_executable_sql")

    def fingerprint(self) -> str:
        import hashlib

        material = "|".join(
            (
                self.language,
                self.path,
                str(self.start_line),
                str(self.end_line),
                self.rule_kind,
                self.object_key,
                self.expression,
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()
