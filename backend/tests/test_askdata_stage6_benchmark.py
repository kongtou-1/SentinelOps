"""Stage 6: frozen case set and judge for the quality comparison."""

import time

import pytest

from app.services import askdata_benchmark as benchmark
from app.services.askdata_query import CHINESE_ANSWER_RULE

CASES = benchmark.load_cases()["cases"]
BY_ID = {case["id"]: case for case in CASES}


def test_frozen_case_set_is_complete_and_labelled():
    assert len(CASES) >= 30
    counts = {name: 0 for name in benchmark.CATEGORIES}
    for case in CASES:
        counts[case["category"]] += 1
        assert case["provenance"], case["id"]
        assert len(case["question"]) >= 4, case["id"]
    assert counts["basic"] >= 10
    assert counts["python"] >= 10
    assert counts["java"] >= 10
    # Both applications must be represented, otherwise the code-rule contrast is
    # only testing one rule set.
    assert {case["app"] for case in CASES} == {"none", "a", "b"}
    gap_cases = [case for case in CASES if case["expect"]["outcome"] == "clarify"]
    assert len(gap_cases) >= 2


def test_case_gaps_cover_columns_missing_from_the_schema():
    document = benchmark.load_cases()
    schema = f"{document['dataset']['schema']} {document['dataset']['reference']}".lower()
    for case in CASES:
        if case["expect"]["outcome"] != "clarify":
            continue
        assert case["reference_sql"] == ""
        assert case["reference_text"]
        # A gap is only honest when the columns really are absent from the schema.
        for column in case["expect"]["sql_none"]:
            assert column.lower() not in schema, (case["id"], column)


def test_reference_answers_pass_the_judge():
    verdicts = benchmark.score_reference(CASES)
    summary = benchmark.summarize(verdicts)
    assert summary["total"] == len(CASES)
    assert summary["failed"] == 0, summary["failures"]


def test_wrong_statements_are_all_rejected():
    verdicts = benchmark.discriminate(CASES)
    summary = benchmark.summarize(verdicts)
    assert summary["total"] >= 16
    assert summary["failed"] == 0, summary["failures"]


def test_judge_explains_why_a_statement_is_wrong():
    case = BY_ID["python-a-01"]
    verdict = benchmark.evaluate(
        case,
        {"sql": case["wrong_sql"], "text": "", "certification": "certified"},
    )
    assert verdict["passed"] is False
    details = " ".join(verdict["reasons"])
    assert "禁止片段" in details or "缺少" in details


def test_judge_requires_clarification_instead_of_an_invented_column():
    case = BY_ID["java-a-05"]
    invented = benchmark.evaluate(
        case,
        {
            "sql": "SELECT refund_reason, COUNT(*) FROM orders GROUP BY refund_reason",
            "text": "统计完成",
        },
    )
    assert invented["passed"] is False
    assert any("禁止片段" in reason for reason in invented["reasons"])

    silent = benchmark.evaluate(case, {"sql": "", "text": "好的，我先看看数据。"})
    assert silent["passed"] is False

    reported = benchmark.evaluate(
        case,
        {"sql": "", "text": case["reference_text"], "clarification": case["reference_text"]},
    )
    assert reported["passed"] is True


def test_missing_certification_is_reported_only_for_rule_based_cases():
    rule_case = BY_ID["python-a-02"]
    verdict = benchmark.evaluate(
        rule_case, {"sql": rule_case["reference_sql"], "text": "共 3 单", "certification": None}
    )
    assert verdict["passed"] is False
    assert any("依据等级" in reason for reason in verdict["reasons"])

    basic = BY_ID["basic-01"]
    plain = benchmark.evaluate(basic, {"sql": basic["reference_sql"], "text": "共 120 单"})
    assert plain["passed"] is True


def test_three_modes_isolate_business_and_code_rules():
    business = {
        "acronyms": ["订单：已完成支付的订单"],
        "evidence": "业务规则（发布口径）：\n- 订单状态：status = 'PAID'",
    }
    code = {
        "domain_rules": [{"name": "amount", "description": "金额为分，需除以 100"}],
        "sql_examples": [
            {"question": "已支付订单数", "sql": "SELECT 1", "evidence": "orders.py:36"}
        ],
        "evidence": "代码业务规则（来自源码快照，仅作口径依据）：\n- [status_filter] status = PAID",
    }
    structure = benchmark.context_for_mode("structure", business, code)
    model = benchmark.context_for_mode("model", business, code)
    full = benchmark.context_for_mode("model_code", business, code)

    assert structure == {"acronyms": [], "evidence": "", "sql_examples": []}
    assert benchmark.contains_rule(model, "status = 'PAID'")
    assert not benchmark.contains_rule(model, "除以 100")
    assert model["sql_examples"] == []

    assert benchmark.contains_rule(full, "status = 'PAID'")
    assert benchmark.contains_rule(full, "除以 100")
    assert len(full["sql_examples"]) == 1
    # Code evidence must precede the business rules, matching _payload.
    assert full["evidence"].startswith("代码业务规则")
    # Acronyms keep code rules first, then supplements.
    assert full["acronyms"][0] == code["domain_rules"][0]


def test_mode_matrix_shows_monotonic_enrichment():
    business = {"acronyms": ["a", "b"], "evidence": "- 规则一\n- 规则二"}
    code = {
        "domain_rules": ["c"],
        "sql_examples": [{"question": "q", "sql": "SELECT 1", "evidence": "p:1"}],
        "evidence": "- 代码规则一",
    }
    matrix = benchmark.mode_matrix(business, code)
    assert list(matrix) == list(benchmark.MODES)
    assert matrix["structure"]["acronyms"] == 0
    assert matrix["model"]["acronyms"] == 2
    assert matrix["model_code"]["acronyms"] == 3
    assert matrix["structure"]["evidence_chars"] == 0
    assert matrix["model"]["evidence_chars"] > 0
    assert matrix["model_code"]["evidence_chars"] > matrix["model"]["evidence_chars"]
    assert matrix["model_code"]["sql_examples"] == 1


def test_context_rejects_unknown_mode():
    with pytest.raises(benchmark.BenchmarkError):
        benchmark.context_for_mode("full", {}, {})


def test_invalid_case_set_is_rejected(tmp_path):
    broken = {
        "format": "sentinelops.askdata.benchmark",
        "version": 1,
        "cases": [
            {
                "id": "x-01",
                "category": "unknown",
                "question": "问题",
                "provenance": [],
                "expect": {"outcome": "sql"},
                "reference_sql": "",
            }
        ],
    }
    path = tmp_path / "cases.json"
    path.write_text(__import__("json").dumps(broken, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(benchmark.BenchmarkError) as error:
        benchmark.load_cases(path)
    message = str(error.value)
    assert "分类非法" in message
    assert "未记录口径出处" in message
    assert "缺少参考 SQL" in message


def test_prompt_and_fixtures_carry_no_credentials():
    fixtures = benchmark.CASES_PATH.parent
    report = benchmark.scan_paths([fixtures])
    assert report == {}
    assert benchmark.scan_for_credentials(CHINESE_ANSWER_RULE) == []


def test_credential_scan_detects_realistic_secrets():
    samples = [
        "api_key = 'sk-abcdefghijklmnopqrstuvwx'",
        'password = "hunter2hunter2"',
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123",
        'DATABASE_URL="postgresql://ops:secret@127.0.0.1:5432/ops"',
    ]
    for sample in samples:
        assert benchmark.scan_for_credentials(sample), sample
    # Hit labels must not leak the value itself.
    labels = benchmark.scan_for_credentials(samples[0])
    assert all("abcdefghij" not in label for label in labels)


def test_credential_scan_ignores_declarations_and_prompt_text():
    source_lines = [
        'password: Mapped[str] = mapped_column(String(200))',
        'api_key_encrypted: Mapped[str] = mapped_column(LargeBinary)',
        'password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD") or getpass.getpass(',
        'sys.stdout.write("password=" + os.environ.get("SOURCE_GIT_PASSWORD", "") + "\\n\\n")',
    ]
    for line in source_lines:
        assert benchmark.scan_for_credentials(line) == [], line


def test_judging_a_full_case_set_is_fast():
    started = time.perf_counter()
    for case in CASES:
        benchmark.evaluate(case, {"sql": case["reference_sql"], "text": "ok"})
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, elapsed


def test_signal_coverage_separates_the_three_modes():
    business = {"acronyms": ["订单：交易主表", "is_deleted：软删标记"], "evidence": ""}
    code = {
        "domain_rules": [
            {
                "name": "status",
                "description": "orders.status = PAID（已认证口径，status_filter）",
            },
            {
                "name": "is_deleted",
                "description": "orders.is_deleted = 0（已认证口径，soft_delete）",
            },
        ],
        "sql_examples": [],
        "evidence": "代码业务规则：\n- [status_filter] orders.status = PAID",
    }
    contexts = {mode: benchmark.context_for_mode(mode, business, code) for mode in benchmark.MODES}
    own = [case for case in CASES if case["app"] == "a"]
    coverage = benchmark.coverage(own, contexts)

    assert coverage["structure"]["covered"] == 0
    # A bare column name in the term list is not a business rule.
    assert coverage["model"]["covered"] == 0
    assert coverage["model_code"]["covered"] > coverage["model"]["covered"]
    missing = coverage["model_code"]["missing"]
    assert "status:paid" not in missing
    assert "soft_delete:flag" not in missing
    # Signals the synthetic context does not carry are still reported as gaps.
    assert "unit:cent" in missing


def test_code_context_mirrors_the_persisted_payload_shape():
    from app.integrations.askdata_code import extract_rules

    root = benchmark.CASES_PATH.parent.parent / "askdata_stage4" / "app_a"
    files = {
        str(path.relative_to(root)).replace("\\", "/"): path.read_text(encoding="utf-8")
        for path in root.rglob("*")
        if path.is_file()
    }
    rules, gaps = extract_rules(files)
    context = benchmark.code_context_from_rules(rules)

    assert gaps["parsed_files"] == 3
    assert set(context) == {"domain_rules", "sql_examples", "evidence"}
    assert len(context["domain_rules"]) <= 24
    assert len(context["sql_examples"]) <= 2
    assert context["evidence"].startswith("代码业务规则")
    # Business-defining rules must survive the cap ahead of mappings.
    kinds = [item["description"] for item in context["domain_rules"]]
    assert any("status_filter" in item for item in kinds)
    assert any("soft_delete" in item for item in kinds)
