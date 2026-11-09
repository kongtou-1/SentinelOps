"""Static Java / MyBatis rule extraction without a compiler or a JVM.

This is a bounded lexical parser: it recognizes JPA entity and column
annotations, Repository query methods, ``@Query`` / ``@Select`` SQL literals,
MyBatis mapper XML, enums and business-service arithmetic. Constructs whose
SQL cannot be determined statically (JPQL, ``<if>`` blocks, ``${}``) are kept
as labelled text evidence with ``parse_state='dynamic'`` and an empty
``sql_text`` — they are never passed to the SQL pipeline or an executor.
"""

from __future__ import annotations

import hashlib
import re

from app.integrations.askdata_code.model import RuleDraft
from app.integrations.askdata_code.sql_analysis import analyze_sql

_TABLE = re.compile(r'@Table\s*\([^)]*?name\s*=\s*"([^"]+)"', re.DOTALL)
_COLUMN = re.compile(r'@(Join)?Column\s*\([^)]*?name\s*=\s*"([^"]+)"', re.DOTALL)
_CLASS = re.compile(r"\bclass\s+(\w+)")
_ENUM = re.compile(r"\benum\s+(\w+)\s*\{([^}]*)\}", re.DOTALL)
_QUERY = re.compile(r"@(Query|Select)\s*\((.*?)\)\s*(?=[;{]|\w)", re.DOTALL)
_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"', re.DOTALL)
_MAPPER_SELECT = re.compile(r"<select\b([^>]*)>(.*?)</select>", re.DOTALL | re.IGNORECASE)
_ID = re.compile(r'\bid\s*=\s*"([^"]+)"')
_DYNAMIC_XML = re.compile(
    r"<\s*(?:if|where|choose|when|otherwise|foreach|set|trim)\b|\$\{", re.IGNORECASE
)
_MYBATIS_PARAM = re.compile(r"#\{\s*([\w.]+)[^}]*\}|:\s*([\w.]+)")
_METHOD = re.compile(r"\b(find|read|get|query|count|list|select)(By|All)(\w+)\s*\(")
_ENUM_VALUE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*(?:\([^)]*\))?\s*,?", re.MULTILINE)
_METRIC = re.compile(r"\b(SUM|COUNT|AVG|MIN|MAX)\s*\(\s*([\w.]+)\s*\)", re.IGNORECASE)
_STATUS_EQ = re.compile(r"\b([\w.]*status[\w.]*)\s*(=|<>|!=)\s*'([^']+)'", re.IGNORECASE)
_DELETED_EQ = re.compile(r"\b(\w*delet\w*)\s*(?:=|<>|!=)\s*(0|false)\b", re.IGNORECASE)
_DELETED_NULL = re.compile(r"\b(\w*delet\w*)\s+IS\s+(NOT\s+)?NULL", re.IGNORECASE)
_TIME_FILTER = re.compile(r"\b(\w*(?:_at|date|time)\w*)\s*(>=|<=|>|<|BETWEEN)", re.IGNORECASE)
_UNIT_DECL = re.compile(
    r"(?:AMOUNT_UNIT|amountUnit|金额单位|单位)\s*[:=]\s*[\"']?(yuan|cent|元|分)[\"']?",
    re.IGNORECASE,
)
_UNIT = re.compile(r"([\w.]*(?:amount|cent|price|fee|pay)[\w.]*)\s*/\s*(100|1000)\b", re.IGNORECASE)
_DIVIDE_UNIT = re.compile(
    r"([\w.]*(?:amount|cent|price|fee|pay)[\w.]*)\s*\.\s*divide\s*\(\s*new\s+BigDecimal\s*\(\s*\"(100|1000)\"",
    re.IGNORECASE,
)
_SELECT_WORD = re.compile(r"^\s*(?:select|with)\b", re.IGNORECASE)


def _line_at(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


def _snippet(text: str, start: int, end: int, limit: int = 400) -> str:
    return text[start : min(end, start + limit)]


def _draft(path, line, kind, key, expression, snippet, *, state="static", sql="", detail=None):
    return RuleDraft(
        language="java",
        path=path,
        start_line=line,
        end_line=line,
        rule_kind=kind,
        object_key=key[:300],
        expression=expression[:400],
        parse_state=state,
        sql_text=sql,
        snippet=snippet,
        detail={"snippet_sha256": hashlib.sha256(snippet.encode()).hexdigest(), **(detail or {})},
    )


def _derived_sql_rules(sql: str, path: str, line: int, snippet: str, scope: str) -> list[RuleDraft]:
    rules: list[RuleDraft] = []
    for match in _METRIC.finditer(sql):
        column = match.group(2)
        rules.append(
            _draft(
                path,
                line,
                "metric",
                column,
                f"{match.group(1).lower()}({column})",
                snippet,
                detail={
                    "parse_scope": scope,
                    "function": match.group(1).lower(),
                    "columns": [column],
                },
            )
        )
    for match in _STATUS_EQ.finditer(sql):
        rules.append(
            _draft(
                path,
                line,
                "status_filter",
                match.group(1),
                f"{match.group(1)} {match.group(2)} {match.group(3)}",
                snippet,
                detail={"parse_scope": scope, "value": match.group(3)},
            )
        )
    for pattern, kind in ((_DELETED_EQ, "soft_delete"), (_DELETED_NULL, "soft_delete")):
        for match in pattern.finditer(sql):
            rules.append(
                _draft(
                    path,
                    line,
                    kind,
                    match.group(1),
                    match.group(0),
                    snippet,
                    detail={"parse_scope": scope},
                )
            )
    for match in _TIME_FILTER.finditer(sql):
        rules.append(
            _draft(
                path,
                line,
                "time_field",
                match.group(1),
                match.group(0),
                snippet,
                detail={"parse_scope": scope, "operator": match.group(2).upper()},
            )
        )
    for match in _UNIT.finditer(sql):
        rules.append(
            _draft(
                path,
                line,
                "amount_unit",
                match.group(1),
                match.group(0),
                snippet,
                detail={"parse_scope": scope, "scale": int(match.group(2)), "unit": "cent"},
            )
        )
    for rule in rules:
        rule.detail["source_sql"] = sql
    return rules


def _sql_literal(sql: str, path: str, line: int, snippet: str, scope: str, *, jpql: bool = False):
    """Return the fragment draft and the derived rules for one SQL literal."""
    if jpql or not _SELECT_WORD.match(sql):
        rules = [
            _draft(
                path,
                line,
                "sql_fragment",
                path,
                "JPQL / 非原生查询（不可静态确定为 SQL）",
                snippet,
                state="dynamic",
                detail={
                    "parse_scope": "jpql" if jpql else "unresolved_query",
                    "reason": "jpql_not_sql",
                },
            )
        ]
        # JPQL is never executable SQL, but its literals still document the
        # business filters and must keep a code location as evidence.
        rules.extend(_derived_sql_rules(sql, path, line, snippet, "jpql_text"))
        return rules
    analysis = analyze_sql(sql)
    rules = [
        _draft(
            path,
            line,
            "sql_fragment",
            ", ".join(sorted(analysis["tables"])) or path,
            sql[:300],
            snippet,
            state=analysis["parse_state"],
            sql=analysis["sql_text"],
            detail={
                "parse_scope": scope,
                "tables": sorted(analysis["tables"]),
                "reason": analysis["reason"],
                "raw_sql": sql[:2000] if analysis["parse_state"] == "dynamic" else "",
            },
        )
    ]
    rules.extend(_derived_sql_rules(sql, path, line, snippet, scope))
    return rules


def _mybatis(text: str, path: str) -> list[RuleDraft]:
    if "<mapper" not in text and "<select" not in text:
        return []
    rules: list[RuleDraft] = []
    for match in _MAPPER_SELECT.finditer(text):
        line = _line_at(text, match.start())
        snippet = _snippet(text, match.start(), match.end())
        identifier = _ID.search(match.group(1))
        scope = f"mybatis:{identifier.group(1)}" if identifier else "mybatis"
        raw_sql = match.group(2).strip()
        if _DYNAMIC_XML.search(raw_sql):
            rules.append(
                _draft(
                    path,
                    line,
                    "sql_fragment",
                    scope,
                    "MyBatis 动态 SQL（含条件分支，需人工确认）",
                    snippet,
                    state="dynamic",
                    detail={"parse_scope": scope, "reason": "dynamic_xml"},
                )
            )
            rules.extend(_derived_sql_rules(raw_sql, path, line, snippet, scope))
            continue
        sql = _MYBATIS_PARAM.sub(lambda m: ":" + (m.group(1) or m.group(2)), raw_sql)
        rules.extend(_sql_literal(sql, path, line, snippet, scope))
    return rules


def _java(text: str, path: str) -> list[RuleDraft]:
    rules: list[RuleDraft] = []
    entities: dict[str, str] = {}
    for match in _TABLE.finditer(text):
        table = match.group(1)
        following = _CLASS.search(text, match.end(), match.end() + 400)
        entity = following.group(1) if following else ""
        if entity:
            entities[entity] = table
        line = _line_at(text, match.start())
        snippet = _snippet(text, match.start(), match.end() + 200)
        rules.append(
            _draft(
                path,
                line,
                "table_mapping",
                table,
                f"@Table({table}) -> {entity or '?'}",
                snippet,
                detail={"parse_scope": "jpa_entity", "entity": entity},
            )
        )
        break

    for match in _COLUMN.finditer(text):
        is_join, column = bool(match.group(1)), match.group(2)
        line = _line_at(text, match.start())
        snippet = _snippet(text, match.start(), match.end() + 160)
        table = next(iter(entities.values()), "")
        key = f"{table}.{column}" if table else column
        rules.append(
            _draft(
                path,
                line,
                "join" if is_join else "column_mapping",
                key,
                f"@{('Join' if is_join else '')}Column({column})",
                snippet,
                detail={"parse_scope": "jpa_column", "column": column, "entity": table},
            )
        )

    for match in _ENUM.finditer(text):
        name, body = match.group(1), match.group(2)
        base = _line_at(text, match.start())
        for offset, value in enumerate(_ENUM_VALUE.findall(body)):
            rules.append(
                _draft(
                    path,
                    base + offset,
                    "enum_value",
                    f"{name}.{value}",
                    f"{name}.{value}",
                    _snippet(text, match.start(), match.end()),
                    detail={"parse_scope": "java_enum", "enum": name, "value": value},
                )
            )

    for match in _QUERY.finditer(text):
        annotation, arguments = match.group(1), match.group(2)
        literal = _STRING.search(arguments)
        if literal is None:
            continue
        sql = literal.group(1).replace('\\"', '"').strip()
        line = _line_at(text, match.start())
        snippet = _snippet(text, match.start(), match.end())
        native = (
            annotation == "Select"
            or re.search(r"nativeQuery\s*=\s*true", arguments, re.IGNORECASE) is not None
        )
        scope = "mybatis_annotation" if annotation == "Select" else "jpa_query"
        rules.extend(_sql_literal(sql, path, line, snippet, scope, jpql=not native))

    for match in _METHOD.finditer(text):
        tail = match.group(3)
        parts = re.findall(r"[A-Z][a-z0-9]*|[a-z0-9]+", tail)
        line = _line_at(text, match.start())
        snippet = _snippet(text, match.start(), match.end())
        for index, part in enumerate(parts):
            lowered = part.casefold()
            if "delet" in lowered:
                rules.append(
                    _draft(
                        path,
                        line,
                        "soft_delete",
                        part,
                        f"派生查询 {match.group(0).strip('(')}: {part}",
                        snippet,
                        detail={"parse_scope": "derived_query"},
                    )
                )
            elif "status" in lowered or "state" in lowered:
                following = parts[index + 1] if index + 1 < len(parts) else ""
                value = (
                    following.upper()
                    if following and following.lower() not in ("and", "or", "by")
                    else "?"
                )
                rules.append(
                    _draft(
                        path,
                        line,
                        "status_filter",
                        part,
                        f"{part} = {value}",
                        snippet,
                        detail={"parse_scope": "derived_query", "value": value},
                    )
                )

    for match in _UNIT_DECL.finditer(text):
        line = _line_at(text, match.start())
        rules.append(
            _draft(
                path,
                line,
                "amount_unit",
                "amount",
                match.group(0),
                _snippet(text, max(0, match.start() - 60), match.end() + 60),
                detail={"parse_scope": "business_service", "unit": match.group(1).upper()},
            )
        )
    for pattern in (_UNIT, _DIVIDE_UNIT):
        for match in pattern.finditer(text):
            line = _line_at(text, match.start())
            rules.append(
                _draft(
                    path,
                    line,
                    "amount_unit",
                    match.group(1),
                    match.group(0),
                    _snippet(text, max(0, match.start() - 80), match.end() + 80),
                    detail={
                        "parse_scope": "business_service",
                        "scale": int(match.group(2)),
                        "unit": "cent",
                    },
                )
            )
    return rules


def extract(path: str, text: str) -> list[RuleDraft]:
    rules = _mybatis(text, path) if path.lower().endswith(".xml") else _java(text, path)
    rules.sort(key=lambda item: (item.start_line, item.rule_kind, item.object_key, item.expression))
    return rules
