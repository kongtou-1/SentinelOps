"""Static source-code business-rule extraction (ask-data stage 4).

These modules are pure: they read text from already-captured, hash-checked
snapshots and return rule drafts. They never import, execute or otherwise run
code from a monitored repository, and they never open a database connection.

The upstream SQL analysis used here is the pinned Auto-Ontology sqlglot
extractor; only statically resolvable SELECT fragments are handed to the
upstream query-history / metric-expression flow.
"""

from __future__ import annotations

from app.integrations.askdata_code.model import RULE_KINDS, RuleDraft

PY_SUFFIXES = (".py",)
JAVA_SUFFIXES = (".java", ".xml", ".kt")


def language_for(path: str) -> str | None:
    lowered = path.lower()
    if lowered.endswith(PY_SUFFIXES):
        return "python"
    if lowered.endswith(JAVA_SUFFIXES):
        return "java"
    return None


def extract_file(path: str, text: str) -> list[RuleDraft]:
    """Extract rules from one snapshot file. Unreadable syntax is not fatal."""
    from app.integrations.askdata_code import java_rules, python_rules

    language = language_for(path)
    if language == "python":
        return python_rules.extract(path, text)
    if language == "java":
        return java_rules.extract(path, text)
    return []


def extract_rules(
    files: dict[str, str],
    *,
    max_files: int = 400,
    max_rules: int = 800,
) -> tuple[list[RuleDraft], dict]:
    """Bounded extraction over a snapshot's text files.

    Returns ``(rules, gaps)``. Files beyond the budget are reported as a data
    gap rather than silently skipped; every rule keeps its own path and lines.
    """
    selected = sorted(
        (path, text) for path, text in files.items() if language_for(path) is not None
    )
    gaps: dict = {"scanned_files": 0, "parsed_files": 0}
    if len(selected) > max_files:
        gaps["source_file_limit"] = len(selected) - max_files
        selected = selected[:max_files]
    rules: list[RuleDraft] = []
    for path, text in selected:
        gaps["scanned_files"] += 1
        try:
            found = extract_file(path, text)
        except (SyntaxError, ValueError, RecursionError) as exc:
            gaps.setdefault("parse_failures", []).append(path)
            gaps["last_parse_error"] = type(exc).__name__
            continue
        gaps["parsed_files"] += 1
        rules.extend(found)
        if len(rules) >= max_rules:
            gaps["source_rule_limit"] = len(rules)
            rules = rules[:max_rules]
            break
    return rules, gaps


__all__ = ["RULE_KINDS", "RuleDraft", "extract_file", "extract_rules", "language_for"]
