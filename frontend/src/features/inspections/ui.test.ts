import { describe, expect, it } from "vitest";
import {
  nextExecution,
  runResult,
  type Run,
  type Coverage,
  type Plan,
} from "./ui";
import { date } from "@/utils/display";

describe("下次执行的状态说明", () => {
  it.each([
    [false, "cron", null, "任务已暂停"],
    [false, "cron", "2026-11-02T10:00:00Z", "任务已暂停"],
    [true, "manual", null, "仅手动执行"],
    [true, "event", null, "等待事件触发"],
    [true, "once", null, "单次计划无后续执行"],
    [true, "interval", null, "暂无执行安排"],
    [true, "cron", "2026-11-02T10:00:00Z", date("2026-11-02T10:00:00Z")],
  ])(
    "状态 %s / %s 的下次执行",
    (enabled, schedule_kind, next_run_at, expected) => {
      expect(
        nextExecution({ enabled, schedule_kind, next_run_at } as Plan),
      ).toBe(expected);
    },
  );
});

function run(
  values: Partial<Coverage> = {},
  overrides: Partial<Run> = {},
): Run {
  return {
    status: "completed",
    run_kind: "standard",
    snapshot: {},
    summary: {
      coverage: {
        discovery_complete: true,
        objects_discovered: 1,
        objects_expected: 1,
        checks_expected: 2,
        checks_completed: 2,
        checks_unfinished: 0,
        expected_is_final: true,
        conclusion: "pass",
        no_objects: false,
        collection_checks: 0,
        ...values,
      },
    },
    ...overrides,
  } as Run;
}
describe("报告的结论和覆盖范围", () => {
  it("独立 Agent 异常不会被规则通过覆盖", () => {
    expect(runResult(run({ conclusion: "fail" }))).toBe("发现异常");
  });
  it("未完成发现不承诺下一批自动补齐，也不显示全部通过", () => {
    const text = runResult(
      run(
        {
          discovery_complete: false,
          objects_expected: null,
          expected_is_final: false,
          conclusion: "unknown",
        },
        { status: "partial" },
      ),
    );
    expect(text).toContain("发现未完成");
    expect(text).not.toMatch(/全部通过|下一批/);
  });
  it("检查错误明确显示未完成数", () => {
    expect(
      runResult(
        run(
          { checks_completed: 1, checks_unfinished: 1, conclusion: "unknown" },
          { status: "partial" },
        ),
      ),
    ).toContain("1 项检查未完成");
  });
  it("所有检查执行失败时明确展示错误，不只显示待确认", () => {
    const text = runResult(
      run(
        {
          checks_completed: 0,
          checks_errors: 2,
          checks_unfinished: 2,
          conclusion: "unknown",
        },
        { status: "partial", error_code: "inspection_coverage_incomplete" },
      ),
    );
    expect(text).toContain("检查执行失败");
    expect(text).toContain("2 项报错");
    expect(text).toContain("未得出检查结论");
  });
  it("部分检查错误同时保留已完成数量", () => {
    const text = runResult(
      run(
        {
          checks_completed: 1,
          checks_errors: 1,
          checks_unfinished: 1,
          conclusion: "unknown",
        },
        { status: "partial" },
      ),
    );
    expect(text).toContain("已完成 1/2 项检查");
    expect(text).toContain("1 项执行出错");
  });
  it("样本通过仍说明未覆盖全部对象", () => {
    expect(runResult(run({}, { run_kind: "trial" }))).toBe(
      "样本检查完成，未覆盖全部对象",
    );
  });
  it("完整发现无对象时不显示全部正常", () => {
    expect(runResult(run({ no_objects: true, conclusion: "unknown" }))).toBe(
      "无检查对象",
    );
  });
});
