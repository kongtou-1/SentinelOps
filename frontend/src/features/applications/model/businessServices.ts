import { label } from "@/utils/display";

export type Observation = {
  container_id: string;
  state?: string | null;
  health?: string | null;
  cpu_host_percent?: number | null;
  memory_bytes?: number | null;
};

export function summarizeService(ids: string[], observations: Observation[]) {
  const samples = new Map(observations.map((o) => [o.container_id, o]));
  const rows = ids.map((id) => samples.get(id));
  const running = rows.filter((o) => o?.state === "running").length;
  const unknown = rows.filter((o) => !o?.state || o.state === "unknown").length;
  const issues: { container_id: string; reason: string; abnormal: boolean }[] =
    [];
  for (const id of ids) {
    const row = samples.get(id);
    if (row?.health === "unhealthy")
      issues.push({
        container_id: id,
        reason: "健康检查未通过",
        abnormal: true,
      });
    else if (!row?.state || row.state === "unknown")
      issues.push({
        container_id: id,
        reason: "未取得实时运行状态",
        abnormal: false,
      });
    else if (row.state !== "running")
      issues.push({
        container_id: id,
        reason: label(row.state),
        abnormal: true,
      });
    else if (row.health !== "healthy")
      issues.push({
        container_id: id,
        reason:
          row.health === "none"
            ? "未配置健康检查"
            : row.health === "starting"
              ? "健康检查启动中"
              : row.health === "stopped"
                ? "健康检查已停止"
                : "未取得健康检查结果",
        abnormal: row.health === "stopped",
      });
  }
  const status = issues.some((issue) => issue.abnormal)
    ? "failed"
    : !ids.length || issues.length
      ? "unknown"
      : "healthy";
  function total(key: "cpu_host_percent" | "memory_bytes") {
    const values = rows
      .map((o) => o?.[key])
      .filter((v): v is number => v != null && Number.isFinite(v) && v >= 0);
    return {
      value: values.length ? values.reduce((n, v) => n + v, 0) : null,
      sampled: values.length,
    };
  }
  return {
    total: ids.length,
    running,
    unknown,
    status,
    issues,
    cpu: total("cpu_host_percent"),
    memory: total("memory_bytes"),
  };
}
