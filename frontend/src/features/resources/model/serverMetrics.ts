import { api } from "@/api/client";
import { metricValue } from "@/utils/display";
import type { Row } from "@/types/records";
export const serverMetrics = [
  "target_up",
  "host_cpu_percent",
  "host_memory_percent",
  "host_disk_percent",
];
export type ServerSamples = Record<string, Record<string, number | null>>;
export async function readServerSamples(
  rows: Row[],
  signal?: AbortSignal,
): Promise<ServerSamples> {
  const empty = Object.fromEntries(
    rows.map((r) => [
      r.id,
      Object.fromEntries(serverMetrics.map((m) => [m, null])),
    ]),
  );
  if (!rows.length) return empty;
  try {
    const params = new URLSearchParams();
    for (const row of rows) params.append("server_ids", row.id);
    const summaries = await api<Row[]>(
      "/monitoring/servers-summary?" + params,
      "GET",
      undefined,
      signal,
    );
    for (const summary of summaries) {
      if (!empty[summary.server_id]) continue;
      for (const metric of serverMetrics) {
        const sample = summary.metrics[metric];
        // A failed, conclusively observed target is zero; missing telemetry is null.
        empty[summary.server_id][metric] =
          sample?.availability === "available" ||
          (metric === "target_up" && sample?.value === 0)
            ? metricValue(metric, sample.value)
            : null;
      }
    }
  } catch {
    /* The whole summary is unknown when the service cannot be reached. */
  }
  return empty;
}
