import { describe, expect, it } from "vitest";
import {
  configuredChecks,
  conditionRequirement,
  inputRows,
} from "./configuration";
import { newCondition, newCheck } from "./task";
import type { Plan } from "./ui";

describe("检查内容展示", () => {
  it("保留零、真假、空值及文本的判断含义", () => {
    const condition = { ...newCondition(), kind: "equals" as const };
    expect(conditionRequirement({ ...condition, expected: 0 })).toBe("等于 0");
    expect(conditionRequirement({ ...condition, expected: false })).toBe(
      "等于 否",
    );
    expect(conditionRequirement({ ...condition, expected: "false" })).toBe(
      "等于 “false”",
    );
    expect(conditionRequirement({ ...condition, expected: null })).toBe(
      "等于 空值",
    );
  });
  it("完整展示范围、绝对值和单位要求", () => {
    expect(
      conditionRequirement({
        ...newCondition(),
        kind: "numeric_range",
        minimum: 0,
        maximum: 10,
        absolute: true,
        unit: "元",
        unit_path: "currency",
      }),
    ).toBe("数值取绝对值后不小于 0，不大于 10；单位字段“currency”必须为“元”");
    expect(
      conditionRequirement({
        ...newCondition(),
        kind: "count_range",
        maximum: 3,
      }),
    ).toBe("项目数量不大于 3");
    expect(
      conditionRequirement({
        ...newCondition(),
        kind: "field_equals",
        compare_path: "total",
      }),
    ).toBe("与字段“total”的值相同");
  });
  it("嵌套输入显示为字段和值，保留数组顺序", () => {
    expect(
      inputRows({
        criteria: { minimum: 0, required: false },
        names: ["甲", "乙"],
        optional: null,
      }),
    ).toEqual([
      { name: "criteria / minimum", value: "0" },
      { name: "criteria / required", value: "否" },
      { name: "names · 第 1 项", value: "甲" },
      { name: "names · 第 2 项", value: "乙" },
      { name: "optional", value: "未设置" },
    ]);
    expect(inputRows({})).toEqual([]);
  });
  it("新旧任务均显示实际检查内容，不修改保存的定义", () => {
    const check = newCheck("agent");
    expect(
      configuredChecks({
        execution_mode: "checks",
        check_items: [check],
      } as Plan),
    ).toEqual([check]);
    const plan = {
      execution_mode: "records",
      rules: [newCondition()],
      agent_enabled: true,
      skill_id: "method",
      agent_source_ids: ["source"],
      agent_parameters: { count: 0 },
    } as unknown as Plan;
    const before = structuredClone(plan);
    const result = configuredChecks(plan);
    expect(result.map((item) => item.kind)).toEqual(["rule", "agent"]);
    expect(result[1]).toMatchObject({
      skill_id: "method",
      source_ids: ["source"],
      parameters: { count: 0 },
    });
    expect(plan).toEqual(before);
  });
});
