import { describe, expect, it } from "vitest";
import { configs } from "./lists";
import { actionName, precheckName, riskName } from "@/features/actions/catalog";

describe("常用数据导出字段", () => {
  it("仅开放选定业务记录，排除凭据、用户及原始资料列表", () => {
    expect(
      Object.keys(configs)
        .filter((key) => configs[key]!.exportColumns)
        .sort(),
    ).toEqual([
      "alerts",
      "audit-logs",
      "executions",
      "incidents",
      "predictions",
    ]);
  });
  it("预测导出分别保留三个窗口的结论，缺少数据不输出正常", () => {
    const columns = configs.predictions!.exportColumns!;
    const row = {
      status: "completed",
      horizons: {
        "1": {
          quality: "usable",
          points: [{ timestamp: 1, value: 2 }],
          risk: { level: "expected", first_breach_at: 1800000000 },
        },
        "6": { quality: "insufficient_data", points: [] },
        "24": { quality: "provisional", points: [{ timestamp: 1, value: 2 }] },
      },
    };
    expect(columns.find((c) => c.label === "1 小时结论")!.value(row, 0)).toBe(
      "预计超限",
    );
    expect(columns.find((c) => c.label === "6 小时结论")!.value(row, 0)).toBe(
      "数据不足，无法判断",
    );
    expect(columns.find((c) => c.label === "24 小时结论")!.value(row, 0)).toBe(
      "初步预测，待验证",
    );
  });
  it("保留完整编号并使用中文动作与风险名称", () => {
    const row = { id: "full-record-identifier", action: "restart_container" };
    const columns = configs.executions!.exportColumns!;
    expect(columns[0]!.value(row, 0)).toBe(row.id);
    expect(columns.find((c) => c.label === "执行动作")!.value(row, 0)).toBe(
      "重启容器",
    );
    expect(actionName("restart_container")).toBe("重启容器");
    expect(riskName("medium")).toBe("中");
    expect(precheckName("container_identity")).toContain("身份");
  });
});
