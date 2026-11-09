import { api, query, request } from "@/api/client";
import type { PredictionRun } from "@/features/predictions/model/timeline";
import type { Row } from "@/types/records";
import { predictionTarget } from "@/features/predictions/model/results";

export async function readPredictionTargets(
  rows: Row[],
  can: (permission: string) => boolean,
  signal: AbortSignal,
): Promise<Row[]> {
  const targets = new Map(
    rows.map((row) => {
      const target = predictionTarget(row.config || {});
      return [target?.path, target] as const;
    }),
  );
  const names = new Map<string, string>();
  await Promise.all(
    [...targets.values()].map(async (target) => {
      if (!target) return;
      if (!can(target.permission)) {
        names.set(target.path, target.kind + "（无查看权限）");
        return;
      }
      try {
        const resource = await api<Row>(target.path, "GET", undefined, signal);
        names.set(target.path, resource.name || target.kind + "（未命名）");
      } catch {
        names.set(target.path, target.kind + "（名称暂不可用）");
      }
    }),
  );
  return rows.map((row) => ({
    ...row,
    target_name:
      names.get(predictionTarget(row.config || {})?.path || "") ||
      "未记录预测对象",
  }));
}

export async function readPredictionRuns(
  taskId: string,
  start: number,
  end: number,
  signal: AbortSignal,
) {
  const runs: PredictionRun[] = [];
  // The longest forecast is 24h, so include batches issued before the visible history.
  for (let offset = 0; ; offset += 100) {
    const result = await request<PredictionRun[]>(
      "/predictions" +
        query({
          task_id: taskId,
          status: "completed",
          start: new Date((start - 86400) * 1000).toISOString(),
          end: new Date(end * 1000).toISOString(),
          offset,
          limit: 100,
        }),
      { signal },
    );
    runs.push(...result.data);
    if (
      result.data.length < 100 ||
      (result.total !== null && runs.length >= result.total)
    )
      break;
  }
  if (!runs.length)
    return api<PredictionRun[]>(
      "/predictions" +
        query({
          task_id: taskId,
          status: "completed",
          limit: 1,
        }),
      "GET",
      undefined,
      signal,
    );
  return runs;
}
