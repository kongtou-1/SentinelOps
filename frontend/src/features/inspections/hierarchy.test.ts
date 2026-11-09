import { describe, expect, it } from "vitest";
import type { Models } from "@/api/client";
import { groupInspectionPlans } from "./hierarchy";

const plan = (
  id: string,
  application_id: string | null,
  application_name: string | null,
) =>
  ({
    id,
    application_id,
    application_name,
  }) as Models["InspectionPlanResponse"];

describe("服务下的巡检计划", () => {
  it("同一服务归在一起，同名服务不合并，独立巡检不混入服务", () => {
    const groups = groupInspectionPlans([
      plan("c1", null, null),
      plan("a1", "service-a", "问答服务"),
      plan("a2", "service-a", "问答服务"),
      plan("b1", "service-b", "问答服务"),
    ]);
    expect(groups.map((g) => [g.id, g.name, g.plans.map((p) => p.id)])).toEqual(
      [
        ["service-a", "问答服务", ["a1", "a2"]],
        ["service-b", "问答服务", ["b1"]],
        ["standalone", "独立巡检", ["c1"]],
      ],
    );
  });
});
