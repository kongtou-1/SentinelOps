import { describe, expect, it } from "vitest";
import type { Models } from "@/api/client";
import {
  actualValue,
  checkTitle,
  displayValue,
  expectation,
  itemConclusion,
  itemTitle,
} from "./display";

const item = (values: Partial<Models["InspectionItemResponse"]> = {}) =>
  ({
    record_id: "a".repeat(64),
    record_version: "b".repeat(64),
    evidence: { original_verdict: "pass", facts: {} },
    checks: [],
    agent_status: "disabled",
    verdict: "supported",
    ...values,
  }) as Models["InspectionItemResponse"];

describe("巡检结果的业务表达", () => {
  it("自主巡检直接展示检查结论", () => {
    expect(
      itemConclusion(
        item({
          evidence: { tool_calls: [] },
          agent_result: { summary: "详情金额必须大于0，实际为-1，检查未通过" },
        }),
      ),
    ).toBe("详情金额必须大于0，实际为-1，检查未通过");
  });
  it("优先使用业务名称，无名称时不把内部标识当标题", () => {
    expect(itemTitle(item(), 3)).toBe("检查结果 3");
    expect(
      itemTitle(
        item({ evidence: { facts: { main: { name: "压力表检定证书" } } } }),
        1,
      ),
    ).toBe("压力表检定证书");
    expect(
      itemTitle(item({ evidence: { facts: { certificate_no: "JL-001" } } }), 1),
    ).toBe("证书 JL-001");
    expect(
      itemTitle(
        item({ checks: [{ title: "业务成功标志", status: "fail" }] }),
        1,
      ),
    ).toBe("业务成功标志");
    expect(
      itemTitle(
        item({
          verdict: "contradicted",
          evidence: { original_verdict: "fail" },
          checks: [{ title: "检查项", status: "pass" }],
        }),
        1,
      ),
    ).toBe("原判定与检查依据不符");
    expect(
      itemTitle(
        item({
          record_id: "DEMO-CERT-002",
          evidence: { facts: { main: { instrument: "DEMO-A" } } },
        }),
        1,
      ),
    ).toBe("DEMO-A · 证书 DEMO-CERT-002");
    expect(
      itemTitle(
        item({ evidence: { facts: { main: { instrument: "DEMO-A" } } } }),
        1,
      ),
    ).toBe("DEMO-A");
  });
  it("检查通过也能与原未通过判定矛盾，不能误写成检查失败", () => {
    const text = itemConclusion(
      item({
        verdict: "contradicted",
        evidence: { original_verdict: "fail" },
        checks: [{ title: "业务成功", status: "pass" }],
      }),
    );
    expect(text).toContain("原判定为未通过");
    expect(text).toContain("规则检查均通过");
  });
  it("同批多条记录优先用业务编号区分，不重复使用相同的检查项标题", () => {
    const values = { checks: [{ title: "误差检查", status: "fail" }] };
    expect(itemTitle(item({ ...values, record_id: "ORDER-001" }), 1)).toBe(
      "ORDER-001",
    );
    expect(itemTitle(item({ ...values, record_id: "ORDER-002" }), 2)).toBe(
      "ORDER-002",
    );
  });
  it("呈现检查失败与评审失败的不同原因", () => {
    expect(
      itemConclusion(
        item({ checks: [{ title: "业务成功标志", status: "fail" }] }),
      ),
    ).toContain("业务成功标志");
    expect(
      itemConclusion(item({ verdict: "needs_review", agent_status: "failed" })),
    ).toContain("Agent 评审失败");
    expect(displayValue(false)).toBe("否");
    expect(displayValue(0)).toBe("0");
  });
  it("证书检查显示项目序号、实际单位和单边限值", () => {
    const check = {
      title: "误差检查",
      path: "pages.1.error",
      actual: "0.08",
      minimum: null,
      maximum: "0.05",
      unit: "mm",
    };
    expect(checkTitle(check)).toBe("第 2 项 · 误差检查");
    expect(actualValue(check)).toBe("0.08 mm");
    expect(expectation(check)).toBe("≤ 0.05 mm");
    expect(expectation({ expected: false })).toBe("否");
  });
  it("原判定未通过且检查未通过时，表示一致而非误报", () => {
    expect(
      itemConclusion(
        item({
          verdict: "supported",
          evidence: { original_verdict: "fail" },
          checks: [{ title: "业务成功", status: "fail" }],
        }),
      ),
    ).toContain("与原判定一致");
  });
});
