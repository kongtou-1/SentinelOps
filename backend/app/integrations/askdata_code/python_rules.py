"""Python AST rule extraction: SQLAlchemy, Django ORM, native SQL and enums.

The AST is walked, never executed. Imports in a monitored repository are only
read as names; no module from that repository is imported into the platform.
"""

from __future__ import annotations

import ast
import hashlib
import re

from app.integrations.askdata_code.model import RuleDraft
from app.integrations.askdata_code.sql_analysis import analyze_sql

_COLUMN = ("Column", "mapped_column", "column", "Field")
_AGGREGATES = ("sum", "count", "avg", "min", "max", "Sum", "Count", "Avg", "Min", "Max")
_FILTERS = ("filter", "filter_by", "where", "having", "exclude")
_SQL_CALLS = ("text", "execute", "exec_driver_sql", "raw", "cursor")
_UNIT = re.compile(r"amount|cent|fen|price|money|fee|total|pay", re.IGNORECASE)
_UNIT_CONSTANT = re.compile(r"AMOUNT_UNIT|MONEY_UNIT|AMOUNT_SCALE|UNIT", re.IGNORECASE)
_DELETED = re.compile(r"delet|removed|void", re.IGNORECASE)
_STATUS = re.compile(r"status|state|stage", re.IGNORECASE)
_TIME = re.compile(r"(^|_)(at|date|time|day|month|year)($|_)|created_at|paid_at|updated_at")


def _line(node) -> tuple[int, int]:
    start = int(getattr(node, "lineno", 1) or 1)
    end = int(getattr(node, "end_lineno", start) or start)
    return start, end


def _snippet(text: str, node, limit: int = 400) -> str:
    lines = text.splitlines()
    start, end = _line(node)
    return "\n".join(lines[start - 1 : min(end, start + 12)])[:limit]


def _literal(node, enums: dict):
    if isinstance(node, ast.Constant):
        if node.value is None:
            return "NULL"
        if isinstance(node.value, bool):
            return "TRUE" if node.value else "FALSE"
        return str(node.value)
    if isinstance(node, ast.Attribute):
        parts, current = [], node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        names = list(reversed(parts))
        for candidate in names[1:]:
            for values in enums.values():
                if names[0] in values["owners"] and candidate in values["values"]:
                    return str(values["values"][candidate])
        return None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value = _literal(node.operand, enums)
        return f"-{value}" if value is not None else None
    if isinstance(node, (ast.List, ast.Tuple)):
        values = [_literal(item, enums) for item in node.elts]
        if all(value is not None for value in values):
            return "(" + ", ".join(values) + ")"
    return None


def _operator(node) -> str:
    return {
        ast.Eq: "=",
        ast.NotEq: "!=",
        ast.Lt: "<",
        ast.LtE: "<=",
        ast.Gt: ">",
        ast.GtE: ">=",
        ast.In: "IN",
        ast.NotIn: "NOT IN",
        ast.Is: "IS",
        ast.IsNot: "IS NOT",
    }.get(type(node), "?")


def _column_ref(node, tables: dict) -> str | None:
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name):
            owner = tables.get(node.value.id, node.value.id)
            return f"{owner}.{node.attr}"
        inner = _column_ref(node.value, tables)
        return f"{inner}.{node.attr}" if inner else node.attr
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _classify(column: str) -> str:
    lowered = column.casefold()
    if _DELETED.search(lowered):
        return "soft_delete"
    if _STATUS.search(lowered):
        return "status_filter"
    if _TIME.search(lowered):
        return "time_field"
    return "filter"


def _class_info(tree, text, path, rules: list, tables: dict, enums: dict, fields: dict):
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        line_start, line_end = _line(node)
        is_enum = any(
            (isinstance(base, ast.Name) and base.id.endswith("Enum"))
            or (isinstance(base, ast.Attribute) and base.attr.endswith("Enum"))
            for base in node.bases
        )
        if is_enum:
            values = {}
            for item in node.body:
                if isinstance(item, ast.Assign) and isinstance(item.value, ast.Constant):
                    for target in item.targets:
                        if isinstance(target, ast.Name):
                            values[target.id] = str(item.value.value)
                            item_start, item_end = _line(item)
                            rules.append(
                                RuleDraft(
                                    language="python",
                                    path=path,
                                    start_line=item_start,
                                    end_line=item_end,
                                    rule_kind="enum_value",
                                    object_key=f"{node.name}.{target.id}",
                                    expression=f"{node.name}.{target.id} = {item.value.value}",
                                    snippet=_snippet(text, item),
                                    detail={
                                        "parse_scope": "enum",
                                        "enum": node.name,
                                        "value": str(item.value.value),
                                    },
                                )
                            )
            enums[node.name] = {"owners": {node.name}, "values": values}
            continue

        table_name = None
        for item in node.body:
            target = None
            if isinstance(item, ast.Assign):
                target = item.targets[0] if item.targets else None
                if isinstance(target, ast.Name) and target.id == "__tablename__":
                    if isinstance(item.value, ast.Constant):
                        table_name = str(item.value.value)
            if isinstance(item, ast.ClassDef) and item.name == "Meta":
                for meta in item.body:
                    if (
                        isinstance(meta, ast.Assign)
                        and isinstance(meta.targets[0], ast.Name)
                        and meta.targets[0].id == "db_table"
                        and isinstance(meta.value, ast.Constant)
                    ):
                        table_name = str(meta.value.value)
        if table_name:
            tables[node.name] = table_name
            rules.append(
                RuleDraft(
                    language="python",
                    path=path,
                    start_line=line_start,
                    end_line=line_end,
                    rule_kind="table_mapping",
                    object_key=table_name,
                    expression=f"class {node.name} -> {table_name}",
                    snippet=_snippet(text, node),
                    detail={"parse_scope": "orm_model", "entity": node.name},
                )
            )
        columns: dict[str, str] = {}
        for item in node.body:
            if not isinstance(item, (ast.Assign, ast.AnnAssign)):
                continue
            target = item.targets[0] if isinstance(item, ast.Assign) and item.targets else None
            target = target or (item.target if isinstance(item, ast.AnnAssign) else None)
            if not isinstance(target, ast.Name):
                continue
            value = item.value
            if not isinstance(value, ast.Call):
                continue
            func = value.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name not in _COLUMN:
                continue
            column = next(
                (
                    arg.value
                    for arg in value.args
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                ),
                target.id,
            )
            columns[target.id] = column
            table_key = table_name or node.name
            item_start, item_end = _line(item)
            rules.append(
                RuleDraft(
                    language="python",
                    path=path,
                    start_line=item_start,
                    end_line=item_end,
                    rule_kind="column_mapping",
                    object_key=f"{table_key}.{column}",
                    expression=f"{node.name}.{target.id} -> {table_key}.{column}",
                    snippet=_snippet(text, item),
                    detail={"parse_scope": "orm_column", "entity": node.name, "column": column},
                )
            )
            for arg in list(value.args) + [kw.value for kw in value.keywords]:
                if not isinstance(arg, ast.Call):
                    continue
                fk_name = (
                    arg.func.id if isinstance(arg.func, ast.Name) else getattr(arg.func, "attr", "")
                )
                if fk_name == "ForeignKey" and arg.args and isinstance(arg.args[0], ast.Constant):
                    target_ref = str(arg.args[0].value)
                    rules.append(
                        RuleDraft(
                            language="python",
                            path=path,
                            start_line=item_start,
                            end_line=item_end,
                            rule_kind="join",
                            object_key=f"{table_key}.{column} -> {target_ref}",
                            expression=f"JOIN {target_ref} ON {table_key}.{column}",
                            snippet=_snippet(text, item),
                            detail={"parse_scope": "orm_foreign_key", "reference": target_ref},
                        )
                    )
        if columns:
            fields[node.name] = columns


def _comparison(node, text, path, tables, enums, scope) -> list[RuleDraft]:
    rules: list[RuleDraft] = []
    if not isinstance(node, ast.Compare) or len(node.ops) != 1:
        return rules
    column = _column_ref(node.left, tables)
    value = _literal(node.comparators[0], enums)
    if column is None:
        return rules
    operator = _operator(node.ops[0])
    if value is None:
        return [
            RuleDraft(
                language="python",
                path=path,
                start_line=_line(node)[0],
                end_line=_line(node)[1],
                rule_kind="filter",
                object_key=column,
                expression=f"{column} {operator} <运行时取值>",
                parse_state="dynamic",
                snippet=_snippet(text, node),
                detail={"parse_scope": scope, "reason": "runtime_value"},
            )
        ]
    expression = f"{column} {operator} {value}"
    rules.append(
        RuleDraft(
            language="python",
            path=path,
            start_line=_line(node)[0],
            end_line=_line(node)[1],
            rule_kind=_classify(column),
            object_key=column,
            expression=expression,
            snippet=_snippet(text, node),
            detail={"parse_scope": scope, "value": value, "operator": operator},
        )
    )
    return rules


def _sql_fragment(node, text, path, argument, scope) -> RuleDraft | None:
    start, end = _line(node)
    snippet = _snippet(text, node)
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        candidate = argument.value.strip()
        analysis = analyze_sql(candidate)
        return RuleDraft(
            language="python",
            path=path,
            start_line=start,
            end_line=end,
            rule_kind="sql_fragment",
            object_key=", ".join(sorted(analysis["tables"])) if analysis["tables"] else path,
            expression=candidate[:300],
            parse_state=analysis["parse_state"],
            sql_text=analysis["sql_text"],
            snippet=snippet,
            detail={
                "parse_scope": scope,
                "tables": sorted(analysis["tables"]),
                "reason": analysis["reason"],
                "raw_sql": candidate[:2000] if analysis["parse_state"] == "dynamic" else "",
            },
        )
    if isinstance(argument, (ast.JoinedStr, ast.BinOp)) or (
        isinstance(argument, ast.Call) and getattr(argument.func, "attr", "") == "format"
    ):
        return RuleDraft(
            language="python",
            path=path,
            start_line=start,
            end_line=end,
            rule_kind="sql_fragment",
            object_key=path,
            expression="动态拼接 SQL（不可静态确定）",
            parse_state="dynamic",
            snippet=snippet,
            detail={"parse_scope": scope, "reason": "dynamic_sql"},
        )
    return None


def _expression_rules(tree, text, path, tables, enums) -> list[RuleDraft]:
    rules: list[RuleDraft] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name in _FILTERS:
                scope = "django_orm" if name == "exclude" else "sqlalchemy_filter"
                for arg in node.args:
                    comparisons = _comparison(arg, text, path, tables, enums, scope)
                    if name == "exclude":
                        comparisons = []  # Negated/composed Q expressions need review.
                    rules.extend(comparisons)
                    if isinstance(arg, ast.Call) and getattr(arg.func, "attr", "") == "Q":
                        rules.append(
                            RuleDraft(
                                language="python",
                                path=path,
                                start_line=_line(arg)[0],
                                end_line=_line(arg)[1],
                                rule_kind="filter",
                                object_key="Q",
                                expression="Django Q 组合条件（需人工确认）",
                                parse_state="dynamic",
                                snippet=_snippet(text, arg),
                                detail={"parse_scope": "django_q", "reason": "composed_filter"},
                            )
                        )
                for keyword in node.keywords:
                    if keyword.arg is None:
                        continue
                    value = _literal(keyword.value, enums)
                    operator = "!=" if name == "exclude" else "="
                    composed = name == "exclude" and len(node.keywords) > 1
                    expression = (
                        f"{keyword.arg} {operator} {value if value is not None else '<运行时取值>'}"
                    )
                    if composed:
                        expression = "Django exclude 组合条件（需人工确认）"
                    rules.append(
                        RuleDraft(
                            language="python",
                            path=path,
                            start_line=_line(node)[0],
                            end_line=_line(node)[1],
                            rule_kind=_classify(keyword.arg),
                            object_key=keyword.arg,
                            expression=expression,
                            parse_state="static"
                            if value is not None and not composed
                            else "dynamic",
                            snippet=_snippet(text, node),
                            detail={"parse_scope": scope, "keyword": keyword.arg},
                        )
                    )
            if name in ("is_", "isnot", "is_not") and isinstance(func, ast.Attribute):
                column = _column_ref(func.value, tables)
                if column:
                    operator = "IS" if name == "is_" else "IS NOT"
                    value = _literal(node.args[0], enums) if node.args else None
                    rendered = value if value is not None else "<运行时取值>"
                    rules.append(
                        RuleDraft(
                            language="python",
                            path=path,
                            start_line=_line(node)[0],
                            end_line=_line(node)[1],
                            rule_kind=_classify(column),
                            object_key=column,
                            expression=f"{column} {operator} {rendered}",
                            parse_state="static" if value is not None else "dynamic",
                            snippet=_snippet(text, node),
                            detail={"parse_scope": "sqlalchemy_is", "operator": operator},
                        )
                    )
            if name in _AGGREGATES and node.args:
                column = _column_ref(node.args[0], tables)
                if column:
                    expression = f"{name.lower()}({column})"
                    rules.append(
                        RuleDraft(
                            language="python",
                            path=path,
                            start_line=_line(node)[0],
                            end_line=_line(node)[1],
                            rule_kind="metric",
                            object_key=column,
                            expression=expression,
                            snippet=_snippet(text, node),
                            detail={
                                "parse_scope": "aggregate_function",
                                "function": name.lower(),
                                "columns": [column],
                            },
                        )
                    )
            if name in ("annotate", "aggregate"):
                for keyword in node.keywords:
                    if isinstance(keyword.value, ast.Call):
                        inner = keyword.value.func
                        inner_name = (
                            inner.id if isinstance(inner, ast.Name) else getattr(inner, "attr", "")
                        )
                        if inner_name in _AGGREGATES and keyword.value.args:
                            column = _literal(keyword.value.args[0], enums) or "?"
                            rules.append(
                                RuleDraft(
                                    language="python",
                                    path=path,
                                    start_line=_line(node)[0],
                                    end_line=_line(node)[1],
                                    rule_kind="metric",
                                    object_key=str(column),
                                    expression=f"{keyword.arg} = {inner_name.lower()}({column})",
                                    snippet=_snippet(text, node),
                                    detail={
                                        "parse_scope": "django_aggregate",
                                        "alias": keyword.arg,
                                    },
                                )
                            )
            if name in _SQL_CALLS and node.args:
                draft = _sql_fragment(node, text, path, node.args[0], "native_sql")
                if draft is not None:
                    rules.append(draft)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.Mult)):
            try:
                segment = ast.get_source_segment(text, node) or ""
            except Exception:
                segment = ""
            if not _UNIT.search(segment):
                continue
            divisor = _literal(node.right, enums)
            if divisor not in ("100", "1000"):
                continue
            rules.append(
                RuleDraft(
                    language="python",
                    path=path,
                    start_line=_line(node)[0],
                    end_line=_line(node)[1],
                    rule_kind="amount_unit",
                    object_key=segment[:120],
                    expression=segment[:200],
                    snippet=_snippet(text, node),
                    detail={
                        "parse_scope": "arithmetic_unit",
                        "scale": int(divisor),
                        "operation": "divide" if isinstance(node.op, ast.Div) else "multiply",
                        "unit": "cent"
                        if "cent" in segment.casefold() or "fen" in segment.casefold()
                        else "scaled",
                    },
                )
            )
    return rules


def _module_constants(tree, text, path, rules: list[RuleDraft]):
    """Explicit unit declarations are first-class business rules."""
    for item in tree.body:
        if not isinstance(item, ast.Assign) or not isinstance(item.value, ast.Constant):
            continue
        value = item.value.value
        if not isinstance(value, str):
            continue
        for target in item.targets:
            if not isinstance(target, ast.Name) or not _UNIT_CONSTANT.search(target.id):
                continue
            start, end = _line(item)
            rules.append(
                RuleDraft(
                    language="python",
                    path=path,
                    start_line=start,
                    end_line=end,
                    rule_kind="amount_unit",
                    object_key=target.id,
                    expression=f"{target.id} = {value}",
                    snippet=_snippet(text, item),
                    detail={"parse_scope": "module_constant", "unit": value.upper()},
                )
            )


def extract(path: str, text: str) -> list[RuleDraft]:
    tree = ast.parse(text)
    tables: dict[str, str] = {}
    enums: dict = {}
    fields: dict = {}
    rules: list[RuleDraft] = []
    _class_info(tree, text, path, rules, tables, enums, fields)
    _module_constants(tree, text, path, rules)
    rules.extend(_expression_rules(tree, text, path, tables, enums))
    # Stable order keeps fingerprints deterministic within a snapshot file.
    rules.sort(key=lambda item: (item.start_line, item.rule_kind, item.object_key, item.expression))
    for rule in rules:
        rule.detail.setdefault("snippet_sha256", hashlib.sha256(rule.snippet.encode()).hexdigest())
    return rules
