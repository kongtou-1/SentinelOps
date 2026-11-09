import { label } from "@/utils/display";
import type { Observation } from "@/features/applications/model/businessServices";

type Monitor = {
  enabled: boolean;
  importance: string;
  status: string;
};

export function summarizeRuntime(ids: string[], observations: Observation[]) {
  const samples = new Map(observations.map((row) => [row.container_id, row]));
  const states = ids.map((id) => samples.get(id)?.state || "unknown");
  const running = states.filter((state) => state === "running").length;
  const unknown = states.filter((state) => state === "unknown").length;
  const failed = states.length - running - unknown;
  return {
    total: ids.length,
    running,
    unknown,
    status: failed ? "failed" : !ids.length || unknown ? "unknown" : "healthy",
    reason: !ids.length
      ? "尚未关联容器"
      : [...new Set(states.filter((state) => state !== "running"))]
          .map((state) =>
            state === "unknown" ? "未取得实时运行状态" : label(state),
          )
          .join("；"),
  };
}

export function summarizeDockerHealth(
  ids: string[],
  observations: Observation[],
) {
  const samples = new Map(observations.map((row) => [row.container_id, row]));
  const checks = ids.map((id) => samples.get(id)?.health || "unknown");
  const counts = [
    ["healthy", "通过"],
    ["unhealthy", "未通过"],
    ["none", "未配置"],
    ["starting", "启动中"],
    ["stopped", "已停止"],
    ["unknown", "结果未知"],
  ].map(([state, text]) => ({
    count: checks.filter((check) => check === state).length,
    text,
  }));
  const failed = checks.some((check) =>
    ["unhealthy", "stopped"].includes(check),
  );
  const unconfigured = !ids.length || checks.every((check) => check === "none");
  const healthy =
    ids.length > 0 && checks.every((check) => check === "healthy");
  return {
    status: failed
      ? "failed"
      : unconfigured
        ? "unconfigured"
        : healthy
          ? "healthy"
          : "unknown",
    text: failed
      ? "检查异常"
      : unconfigured
        ? "未配置"
        : healthy
          ? "全部通过"
          : "检查不完整",
    reason: counts
      .filter((item) => item.count)
      .map((item) => `${item.count} 个${item.text}`)
      .join("；"),
  };
}

export function summarizeMonitors(monitors: Monitor[] | undefined) {
  if (!monitors)
    return {
      status: "unknown",
      text: "读取失败",
      reason: "服务监测数据暂不可用",
    };
  if (!monitors.length)
    return { status: "unconfigured", text: "未配置", reason: "" };
  const active = monitors.filter((monitor) => monitor.enabled);
  if (!active.length)
    return {
      status: "paused",
      text: "已暂停",
      reason: `${monitors.length} 项监测已暂停`,
    };
  const critical = active.filter(
    (monitor) => monitor.importance === "critical",
  );
  const reason = [
    { name: "关键", rows: critical },
    {
      name: "辅助",
      rows: active.filter((monitor) => monitor.importance !== "critical"),
    },
  ]
    .flatMap((group) =>
      [
        ["healthy", "通过"],
        ["failed", "异常"],
        ["stale", "结果过期"],
        ["unknown", "待判定"],
      ].map(([state, text]) => {
        const count = group.rows.filter(
          (monitor) => monitor.status === state,
        ).length;
        return count ? `${count} 项${group.name}监测${text}` : "";
      }),
    )
    .filter(Boolean)
    .join("；");
  if (!critical.length)
    return monitors.some((monitor) => monitor.importance === "critical")
      ? { status: "paused", text: "关键监测已暂停", reason }
      : { status: "unconfigured", text: "未配置关键监测", reason };
  if (critical.some((monitor) => monitor.status === "failed"))
    return { status: "failed", text: "异常", reason };
  if (critical.some((monitor) => monitor.status === "stale"))
    return { status: "stale", text: "结果过期", reason };
  if (critical.some((monitor) => monitor.status !== "healthy"))
    return { status: "unknown", text: "待判定", reason };
  return { status: "healthy", text: "正常", reason };
}
