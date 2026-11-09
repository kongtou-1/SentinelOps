import type { Models } from "@/api/client";

type Plan = Models["InspectionPlanResponse"];

export function groupInspectionPlans(plans: Plan[]) {
  const groups = new Map<string, { id: string; name: string; plans: Plan[] }>();
  for (const plan of plans) {
    const id = plan.application_id || "standalone";
    let group = groups.get(id);
    if (!group) {
      group = {
        id,
        name: plan.application_id
          ? plan.application_name || "已删除的服务"
          : "独立巡检",
        plans: [],
      };
      groups.set(id, group);
    }
    group.plans.push(plan);
  }
  return [...groups.values()].sort((a, b) =>
    a.id === "standalone" ? 1 : b.id === "standalone" ? -1 : 0,
  );
}
