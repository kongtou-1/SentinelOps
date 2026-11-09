"""Stage-6 acceptance: frozen cases, judge self-check and mode comparison.

Deterministic by design: no model channel and no business database are needed.
What it proves is that the frozen case set is self-consistent, that the judge
accepts the confirmed statements and rejects the wrong ones, and that the three
comparison modes really deliver different business rules to the prompt.

Real-model answers are scored with the same judge through ``--answers``:

    python scripts/accept_askdata_stage6.py --answers .local/stage6-answers.json
    {"structure": {"basic-01": {"sql": "...", "text": "..."}}, "model": {...}}

The answers file is produced by a manual comparison run against live channels;
``PROVENANCE`` in the report records which side a number came from.
"""

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from app.integrations.askdata_code import extract_rules
from app.services import askdata_benchmark as benchmark
from app.services.askdata_query import CHINESE_ANSWER_RULE

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "backend" / "tests" / "fixtures" / "askdata_stage4"
REPORT = ROOT / ".local" / "askdata-stage6-report.json"

BUSINESS_CONTEXT = {
    "acronyms": ["订单：交易主表", "营收：订单金额合计"],
    "evidence": "",
}


def load_code_context(app):
    root = FIXTURES / app
    files = {
        str(path.relative_to(root)).replace("\\", "/"): path.read_text(encoding="utf-8")
        for path in root.rglob("*")
        if path.is_file()
    }
    rules, gaps = extract_rules(files)
    return benchmark.code_context_from_rules(rules), rules, gaps


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--answers", help="recorded answers JSON for offline scoring")
    parser.add_argument("--write", action="store_true", help="write the report JSON")
    options = parser.parse_args()

    started = time.perf_counter()
    document = benchmark.load_cases()
    cases = document["cases"]

    reference = benchmark.score_reference(cases)
    reference_summary = benchmark.summarize(reference)
    wrong = benchmark.discriminate(cases)
    wrong_summary = benchmark.summarize(wrong)
    judge_seconds = 0.0
    for _ in range(10):
        judge_started = time.perf_counter()
        for case in cases:
            benchmark.evaluate(case, {"sql": case["reference_sql"], "text": "ok"})
        judge_seconds += time.perf_counter() - judge_started
    judge_seconds /= 10

    apps = {}
    for app, letter in (("app_a", "a"), ("app_b", "b")):
        extract_started = time.perf_counter()
        code_context, rules, gaps = load_code_context(app)
        extract_seconds = round(time.perf_counter() - extract_started, 4)
        contexts = {
            mode: benchmark.context_for_mode(mode, BUSINESS_CONTEXT, code_context)
            for mode in benchmark.MODES
        }
        own = [case for case in cases if case["app"] == letter]
        apps[app] = {
            "cases": len(own),
            "rules": len(rules),
            "gaps": gaps,
            "extract_seconds": extract_seconds,
            "matrix": benchmark.mode_matrix(BUSINESS_CONTEXT, code_context),
            "coverage": benchmark.coverage(own, contexts),
            "sample_evidence": str(code_context["evidence"]).splitlines()[:3],
        }

    scan = benchmark.scan_paths([benchmark.CASES_PATH.parent, ROOT / "backend" / "app"])
    prompt_hits = benchmark.scan_for_credentials(CHINESE_ANSWER_RULE)

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "provenance": "deterministic harness (no live model, no business database)",
        "dataset": document["dataset"],
        "cases": len(cases),
        "by_category": {
            name: sum(1 for case in cases if case["category"] == name)
            for name in benchmark.CATEGORIES
        },
        "judge_seconds": round(judge_seconds, 4),
        "reference": reference_summary,
        "discrimination": wrong_summary,
        "apps": apps,
        "credential_scan": {"hits": scan, "prompt_hits": prompt_hits},
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }

    if options.answers:
        answers = json.loads(Path(options.answers).read_text(encoding="utf-8"))
        report["recorded_run"] = {
            "source": str(options.answers),
            "modes": benchmark.score_answers(cases, answers),
        }

    print(f"样例 {len(cases)} 条：基础 {report['by_category']['basic']} / "
          f"Python {report['by_category']['python']} / Java {report['by_category']['java']}")
    print(f"参考口径自检：{reference_summary['passed']}/{reference_summary['total']} 通过")
    print(f"错误口径拦截：{wrong_summary['passed']}/{wrong_summary['total']} 被拒")
    for app, data in apps.items():
        coverage = data["coverage"]
        print(f"{app}：抽取规则 {data['rules']} 条；口径覆盖率 "
              f"structure {coverage['structure']['rate']:.0%} / "
              f"model {coverage['model']['rate']:.0%} / "
              f"model_code {coverage['model_code']['rate']:.0%}")
    print(f"凭据扫描：命中 {len(scan)} 个文件，提示词命中 {len(prompt_hits)} 项")
    print(f"耗时 {report['elapsed_seconds']}s")

    if options.write:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告已写入 {REPORT.relative_to(ROOT)}")

    failed = (
        reference_summary["failed"]
        or wrong_summary["failed"]
        or scan
        or prompt_hits
    )
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
