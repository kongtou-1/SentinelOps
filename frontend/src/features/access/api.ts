import { api, query } from "@/api/client";

export type PolicyTarget = {
  kind: string;
  id: string;
  userId?: string;
};

export function policyPath(target: PolicyTarget) {
  return (
    `/access-policies/${target.kind}/${target.id}` +
    query({ user_id: target.userId })
  );
}

export async function updatePolicies(
  changes: (PolicyTarget & { name: string; version: number })[],
  restricted: boolean,
) {
  let succeeded = 0;
  const failed: string[] = [];
  for (const row of changes) {
    try {
      await api(policyPath(row), "PUT", {
        restricted,
        expected_version: row.version,
      });
      succeeded++;
    } catch (e) {
      failed.push(
        `${row.name}：${e instanceof Error ? e.message : "保存失败"}`,
      );
    }
  }
  return { succeeded, failed };
}
