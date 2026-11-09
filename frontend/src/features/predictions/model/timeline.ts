import type { Models } from "@/api/client";

export type ForecastPoint = {
  timestamp: number;
  value: number;
  lower?: number;
  upper?: number;
  issuedAt?: number;
};
export type PredictionRun = Omit<Models["ResultResponse"], "horizons"> & {
  horizons: Record<
    string,
    {
      points?: ForecastPoint[];
      model?: string;
      quality?: string;
      risk?: { level?: string; first_breach_at?: number };
    }
  >;
};
type Task = Pick<
  Models["TaskResponse"],
  | "id"
  | "metric"
  | "server_id"
  | "container_id"
  | "llm_endpoint_id"
  | "selector"
>;
const selectorKey = (selector: unknown) =>
  JSON.stringify(
    Object.entries((selector || {}) as Record<string, unknown>).sort(),
  );

export function predictionTimeline(
  runs: PredictionRun[],
  task: Task,
  horizon: string,
  start: number,
  now: number,
) {
  const resource = task.container_id
    ? "container_id"
    : task.llm_endpoint_id
      ? "llm_endpoint_id"
      : "server_id";
  const completed = runs
    .filter(
      (run) =>
        run.task_id === task.id &&
        run.status === "completed" &&
        run.config.metric === task.metric &&
        run.config[resource] === task[resource] &&
        selectorKey(run.config.selector) === selectorKey(task.selector) &&
        Number.isFinite(Date.parse(run.finished_at || "")) &&
        Date.parse(run.finished_at!) / 1000 <= now,
    )
    .sort(
      (a, b) =>
        Date.parse(a.created_at) - Date.parse(b.created_at) ||
        a.id.localeCompare(b.id),
    );
  const latest = completed.at(-1);
  const history = new Map<number, ForecastPoint>();
  // A point may only use a forecast that was available before its target time.
  const compatible = completed
    .filter(
      (run) =>
        run.config.source_epoch === latest?.config.source_epoch &&
        run.config.docker_id === latest?.config.docker_id &&
        run.config.server_id === latest?.config.server_id,
    )
    .sort((a, b) => Date.parse(a.finished_at!) - Date.parse(b.finished_at!));
  for (const run of compatible) {
    const issuedAt = Date.parse(run.finished_at!) / 1000;
    for (const point of run.horizons[horizon]?.points || []) {
      if (
        point.timestamp >= start &&
        point.timestamp <= now &&
        point.timestamp > issuedAt
      )
        history.set(point.timestamp, { ...point, issuedAt });
    }
  }
  return {
    latest,
    history: [...history.values()].sort((a, b) => a.timestamp - b.timestamp),
    future: (latest?.horizons[horizon]?.points || []).filter(
      (point) => point.timestamp > now,
    ),
  };
}
