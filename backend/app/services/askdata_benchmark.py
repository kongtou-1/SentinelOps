"""Stage-6 acceptance harness for the ask-data quality comparison.

The frozen case set lives in ``tests/fixtures/askdata_stage6/cases.json``. Each
case carries the human-confirmed expectation (which tables and SQL fragments a
correct statement must contain, which it must not contain, and whether the
answer has to clarify instead of inventing a column) plus a reference statement
used only as a self-check and as an offline scoring target.

Nothing here calls a model or a database: :func:`evaluate` grades an already
produced answer, so the same judge scores reference statements (self-check),
injected pipelines (deterministic tests) and real model output (recorded run)
with one code path.

The three comparison modes mirror ``askdata_query._payload`` exactly for the
parts they control:

* ``structure``  – schema only: no business rules, no supplements, no code rules.
* ``model``      – business rules and supplements (database business model).
* ``model_code`` – business rules, supplements and frozen code rules.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

CASES_PATH = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "askdata_stage6" / "cases.json"
)
CATEGORIES = ("basic", "python", "java")
MODES = ("structure", "model", "model_code")
MODE_LABELS = {
    "structure": "仅结构",
    "model": "数据库业务模型",
    "model_code": "业务模型 + 代码规则",
}

# Mirrors ``askdata_code_rules.context_payload``: business-defining rules must
# survive the 24-item cap before mappings and raw fragments.
_RULE_PRIORITY = {
    "status_filter": 0,
    "soft_delete": 0,
    "amount_unit": 1,
    "time_field": 1,
    "metric": 2,
    "filter": 2,
    "sql_fragment": 3,
    "join": 4,
}

_REQUIRED = ("id", "category", "question", "provenance", "expect", "reference_sql")
_OPERATOR_RE = re.compile(r"\s*([=<>!+\-*/(),])\s*")
_SPACE_RE = re.compile(r"\s+")


class BenchmarkError(ValueError):
    """Raised when the frozen case set is incomplete or inconsistent."""


def normalize_sql(text: str) -> str:
    """Lower-case, de-quote and collapse SQL so fragments compare predictably."""
    value = str(text or "").lower()
    for quote in ('"', "`", "'"):
        value = value.replace(quote, " ")
    value = _SPACE_RE.sub(" ", value).strip().rstrip(";")
    return _OPERATOR_RE.sub(r"\1", value)


def _as_answer(answer) -> dict:
    if answer is None:
        answer = {}
    if not isinstance(answer, dict):
        raise BenchmarkError("answer 必须是包含 sql/text 的字典")
    return {
        "sql": str(answer.get("sql") or ""),
        "text": str(answer.get("text") or ""),
        "clarification": answer.get("clarification") or None,
        "certification": answer.get("certification") or None,
        "error_code": answer.get("error_code") or None,
    }


def load_cases(path: Path | str | None = None) -> dict:
    """Load and validate the frozen case set."""
    target = Path(path) if path else CASES_PATH
    if not target.is_file():
        raise BenchmarkError(f"样例文件不存在：{target}")
    document = json.loads(target.read_text(encoding="utf-8"))
    if document.get("format") != "sentinelops.askdata.benchmark":
        raise BenchmarkError("样例文件格式标识不正确")
    cases = document.get("cases") or []
    problems: list[str] = []
    seen: set[str] = set()
    for index, case in enumerate(cases):
        label = case.get("id") or f"#{index + 1}"
        missing = [key for key in _REQUIRED if key not in case]
        if missing:
            problems.append(f"{label} 缺少字段：{'、'.join(missing)}")
            continue
        if case["id"] in seen:
            problems.append(f"{label} 编号重复")
        seen.add(case["id"])
        if case["category"] not in CATEGORIES:
            problems.append(f"{label} 分类非法：{case['category']}")
        if not case["provenance"]:
            problems.append(f"{label} 未记录口径出处")
        expect = case["expect"]
        outcome = expect.get("outcome")
        if outcome not in ("sql", "clarify"):
            problems.append(f"{label} 预期结果类型非法：{outcome}")
            continue
        if outcome == "sql":
            if not str(case.get("reference_sql") or "").strip():
                problems.append(f"{label} 缺少参考 SQL")
            if not expect.get("tables"):
                problems.append(f"{label} 未声明涉及的表")
        else:
            if not expect.get("gap_terms"):
                problems.append(f"{label} 缺口样例未声明缺口关键词")
            if not str(case.get("reference_text") or "").strip():
                problems.append(f"{label} 缺口样例缺少参考答复")
    if problems:
        raise BenchmarkError("样例集校验失败：" + "；".join(problems))
    return document


def evaluate(case: dict, answer) -> dict:
    """Grade one answer against one frozen expectation."""
    expect = case["expect"]
    payload = _as_answer(answer)
    sql = normalize_sql(payload["sql"])
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    if expect.get("outcome") == "clarify":
        forbidden = [normalize_sql(item) for item in expect.get("sql_none") or []]
        hits = [item for item in forbidden if item and item in sql]
        add("未臆造缺失字段", not hits, "命中禁止片段：" + "、".join(hits) if hits else "")
        text = f"{payload['text']}\n{payload['clarification'] or ''}"
        gap_terms = expect.get("gap_terms") or []
        reported = any(term and term in text for term in gap_terms)
        add(
            "已澄清或报告缺口",
            reported,
            "" if reported else "答复未说明缺失的字段或口径",
        )
    else:
        add("已生成可执行 SQL", bool(sql.strip()), "" if sql.strip() else "答复中没有 SQL")
        missing_tables = [
            name
            for name in expect.get("tables") or []
            if not re.search(rf"\b{re.escape(str(name).lower())}\b", sql)
        ]
        add(
            "引用正确的表",
            not missing_tables,
            "缺少表：" + "、".join(missing_tables) if missing_tables else "",
        )
        missing_all = [
            fragment
            for fragment in expect.get("sql_all") or []
            if normalize_sql(fragment) not in sql
        ]
        add(
            "包含必需口径片段",
            not missing_all,
            "缺少：" + "、".join(missing_all) if missing_all else "",
        )
        groups = expect.get("sql_any") or []
        missing_groups = [
            group for group in groups if not any(normalize_sql(item) in sql for item in group)
        ]
        add(
            "命中等价口径",
            not missing_groups,
            "未命中：" + "，或 ".join("/".join(group) for group in missing_groups)
            if missing_groups
            else "",
        )
        banned = [normalize_sql(item) for item in expect.get("sql_none") or []]
        hits = [item for item in banned if item and item in sql]
        add(
            "未使用错误口径",
            not hits,
            "命中禁止片段：" + "、".join(hits) if hits else "",
        )
        allowed = expect.get("certification_in") or []
        if allowed:
            actual = payload["certification"]
            add(
                "引用依据等级符合预期",
                actual in allowed,
                f"实际 {actual or '未返回'}，预期 {'/'.join(allowed)}",
            )

    passed = all(item["ok"] for item in checks)
    reasons = [f"{item['name']}：{item['detail']}" for item in checks if not item["ok"]]
    return {
        "id": case["id"],
        "category": case["category"],
        "app": case.get("app", "none"),
        "outcome": expect.get("outcome"),
        "passed": passed,
        "checks": checks,
        "reasons": reasons,
    }


def summarize(verdicts: list[dict]) -> dict:
    """Pass counts overall and per category, plus the failing case ids."""
    by_category = {name: {"total": 0, "passed": 0} for name in CATEGORIES}
    failures = []
    for verdict in verdicts:
        bucket = by_category.setdefault(verdict["category"], {"total": 0, "passed": 0})
        bucket["total"] += 1
        if verdict["passed"]:
            bucket["passed"] += 1
        else:
            failures.append({"id": verdict["id"], "reasons": verdict["reasons"]})
    total = len(verdicts)
    passed = sum(1 for verdict in verdicts if verdict["passed"])
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "rate": round(passed / total, 4) if total else 0.0,
        "by_category": by_category,
        "failures": failures,
    }


def context_for_mode(mode: str, business: dict | None = None, code: dict | None = None) -> dict:
    """Assemble the prompt context for one comparison mode."""
    if mode not in MODES:
        raise BenchmarkError(f"未知对比模式：{mode}")
    business = business or {"acronyms": [], "evidence": ""}
    code = code or {"domain_rules": [], "sql_examples": [], "evidence": ""}
    if mode == "structure":
        return {"acronyms": [], "evidence": "", "sql_examples": []}
    business_acronyms = [str(item) for item in business.get("acronyms") or []]
    business_evidence = str(business.get("evidence") or "")
    if mode == "model":
        return {
            "acronyms": business_acronyms,
            "evidence": business_evidence,
            "sql_examples": [],
        }
    code_rules = list(code.get("domain_rules") or [])
    examples = [
        {
            "question": str(item.get("question", "")),
            "sql": str(item.get("sql", "")),
            "evidence": str(item.get("evidence", "")),
        }
        for item in (code.get("sql_examples") or [])[:2]
    ]
    return {
        "acronyms": [*code_rules, *business_acronyms],
        "evidence": "\n".join(
            part for part in (str(code.get("evidence") or ""), business_evidence) if part
        ),
        "sql_examples": examples,
    }


def mode_fingerprint(context: dict) -> dict:
    """Compact, comparable description of one assembled context."""
    evidence = str(context.get("evidence") or "")
    lines = [line for line in evidence.splitlines() if line.strip().startswith("- ")]
    return {
        "acronyms": len(context.get("acronyms") or []),
        "evidence_lines": len(lines),
        "evidence_chars": len(evidence),
        "sql_examples": len(context.get("sql_examples") or []),
    }


def mode_matrix(business: dict | None = None, code: dict | None = None) -> dict:
    """Fingerprints for all three modes, in comparison order."""
    return {
        mode: mode_fingerprint(context_for_mode(mode, business, code)) for mode in MODES
    }


def contains_rule(context: dict, marker: str) -> bool:
    """Whether a rule marker reached the assembled context."""
    needle = str(marker)
    if any(needle in str(item) for item in context.get("acronyms") or []):
        return True
    return needle in str(context.get("evidence") or "")


# Predicate-shaped needles: a bare column name in a catalog-derived term list is
# not a business rule, so only an actual predicate counts as coverage.
SIGNAL_TERMS = {
    "status:paid": ("status = paid", "status=paid"),
    "status:settled": ("status = settled", "status=settled"),
    "soft_delete:flag": ("is_deleted = 0", "is_deleted = false", "is_deleted=0"),
    "soft_delete:null": ("deleted_at is null",),
    "unit:cent": ("cent", "/ 100", "/100"),
    "unit:yuan": ("yuan",),
    "time:created_at": ("created_at >=", "created_at>="),
    "time:paid_at": ("paid_at >=", "paid_at>="),
    "exclude:refunded": ("refunded",),
    "exclude:refund": ("refund",),
}


def context_text(context: dict) -> str:
    """Flatten one assembled context into comparable text."""
    parts = [str(item) for item in context.get("acronyms") or []]
    parts.append(str(context.get("evidence") or ""))
    for example in context.get("sql_examples") or []:
        parts.append(f"{example.get('question', '')} {example.get('sql', '')}")
    return normalize_sql("\n".join(parts))


def context_support(case: dict, context: dict) -> dict:
    """How many of the case's confirmed signals the context actually carries."""
    signals = list(case["expect"].get("signals") or [])
    text = context_text(context)
    missing = []
    for signal in signals:
        needles = [normalize_sql(item) for item in SIGNAL_TERMS.get(signal, ())]
        if not any(needle and needle in text for needle in needles):
            missing.append(signal)
    return {
        "total": len(signals),
        "covered": len(signals) - len(missing),
        "missing": missing,
    }


def coverage(cases: list[dict], contexts: dict) -> dict:
    """Aggregate signal coverage across the cases that carry signals."""
    per_mode = {}
    for mode in MODES:
        context = contexts.get(mode) or {}
        total = covered = 0
        missing: dict[str, int] = {}
        for case in cases:
            support = context_support(case, context)
            if not support["total"]:
                continue
            total += support["total"]
            covered += support["covered"]
            for signal in support["missing"]:
                missing[signal] = missing.get(signal, 0) + 1
        per_mode[mode] = {
            "label": MODE_LABELS[mode],
            "signals": total,
            "covered": covered,
            "rate": round(covered / total, 4) if total else 0.0,
            "missing": dict(sorted(missing.items(), key=lambda item: (-item[1], item[0]))),
        }
    return per_mode


def code_context_from_rules(rules, limit: int = 24) -> dict:
    """Offline twin of ``askdata_code_rules.context_payload``.

    ``context_payload`` reads persisted rows; this variant takes the extractor
    drafts directly so the acceptance run can compare modes without a database.
    Field names and ordering match the persisted path.
    """
    ordered = sorted(
        rules,
        key=lambda rule: (
            rule.parse_state != "static",
            _RULE_PRIORITY.get(rule.rule_kind, 5),
            rule.path,
            rule.start_line,
        ),
    )
    domain, examples, lines = [], [], []
    for rule in ordered:
        provenance = f"{rule.path}:{rule.start_line}"
        text = f"{rule.expression}（已认证口径，{rule.rule_kind}，代码出处 {provenance}）"
        if len(domain) < limit:
            domain.append({"name": rule.object_key or rule.rule_kind, "description": text})
        if len(lines) < limit:
            lines.append(f"- [{rule.rule_kind}] {text}")
        if (
            rule.sql_text
            and rule.parse_state == "static"
            and len(examples) < 2
        ):
            examples.append(
                {
                    "question": rule.expression[:200],
                    "sql": rule.sql_text,
                    "evidence": provenance,
                }
            )
    return {
        "domain_rules": domain,
        "sql_examples": examples,
        "evidence": "\n".join(["代码业务规则（来自源码快照，仅作口径依据）：", *lines])
        if lines
        else "",
    }


_SECRET_PATTERNS = (
    (re.compile(r"sk-[a-z0-9]{16,}", re.I), "sk- 形式的模型密钥"),
    (re.compile(r"nvapi-[a-z0-9_\-]{16,}", re.I), "nvapi- 形式的模型密钥"),
    (
        re.compile(
            r"(?i)(api[_-]?key|password|passwd|secret|access[_-]?token)"
            r"\s*[:=]\s*['\"][^'\"\s]{8,}['\"]"
        ),
        "内联凭据赋值",
    ),
    (re.compile(r"(?i)bearer\s+[a-z0-9._\-]{20,}"), "Bearer 令牌"),
    (re.compile(r"(?i)(postgres(?:ql)?|mysql)://[^\s:/]+:[^\s@]+@"), "带密码的连接串"),
)


def scan_for_credentials(text: str) -> list[str]:
    """Return human-readable hit labels; never echo the matched secret."""
    hits: list[str] = []
    for pattern, label in _SECRET_PATTERNS:
        if pattern.search(str(text or "")):
            if label not in hits:
                hits.append(label)
    return hits


def scan_paths(paths) -> dict:
    """Scan files or directories for credential-shaped strings."""
    report: dict[str, list[str]] = {}
    for item in paths:
        target = Path(item)
        if target.is_file():
            files = [target]
        elif target.is_dir():
            files = sorted(target.rglob("*"))
        else:
            files = []
        for file in files:
            if not file.is_file() or file.suffix.lower() in {".pyc", ".zip", ".png", ".jpg"}:
                continue
            try:
                text = file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            hits = scan_for_credentials(text)
            if hits:
                report[str(file)] = hits
    return report


def score_reference(cases: list[dict]) -> list[dict]:
    """Self-check: every frozen case must accept its own confirmed answer."""
    verdicts = []
    for case in cases:
        expect = case["expect"]
        if expect.get("outcome") == "clarify":
            answer = {"sql": "", "text": case.get("reference_text", "")}
        else:
            answer = {
                "sql": case["reference_sql"],
                "text": "已按业务口径生成查询。",
                "certification": (expect.get("certification_in") or [None])[0],
            }
        verdicts.append(evaluate(case, answer))
    return verdicts


def discriminate(cases: list[dict]) -> list[dict]:
    """Every case carrying a wrong statement must be rejected by the judge."""
    verdicts = []
    for case in cases:
        wrong = case.get("wrong_sql")
        if not wrong:
            continue
        verdict = evaluate(
            case,
            {
                "sql": wrong,
                "text": "已生成查询。",
                "certification": (case["expect"].get("certification_in") or [None])[0],
            },
        )
        # Invert: a wrong statement must NOT pass.
        verdict["expected_rejected"] = True
        verdict["passed"] = not verdict["passed"]
        if not verdict["passed"]:
            verdict["reasons"] = ["错误口径未被拦截"]
        else:
            verdict["reasons"] = []
        verdicts.append(verdict)
    return verdicts


@dataclass
class BenchmarkRun:
    """A scored comparison run, either injected or recorded from a real model."""

    mode: str
    verdicts: list[dict] = field(default_factory=list)

    @property
    def summary(self) -> dict:
        return summarize(self.verdicts)


def score_answers(cases: list[dict], answers: dict) -> dict:
    """Score recorded answers (``{case_id: {sql, text, ...}}``) per mode."""
    runs = {}
    for mode in MODES:
        mode_answers = answers.get(mode) or {}
        verdicts = [evaluate(case, mode_answers.get(case["id"], {})) for case in cases]
        runs[mode] = {
            "label": MODE_LABELS[mode],
            "summary": summarize(verdicts),
            "verdicts": verdicts,
        }
    return runs
