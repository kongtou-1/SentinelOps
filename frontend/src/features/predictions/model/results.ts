import type { Row } from "@/types/records";
import { label } from "@/utils/display";

export function predictionTarget(config: Row) {
  if (config.container_id)
    return {
      path: "/containers/" + config.container_id,
      kind: "容器",
      permission: "containers:read",
    };
  if (config.llm_endpoint_id)
    return {
      path: "/llm-endpoints/" + config.llm_endpoint_id,
      kind: "LLM 服务",
      permission: "llm:read",
    };
  if (config.server_id)
    return {
      path: "/servers/" + config.server_id,
      kind: "服务器",
      permission: "servers:read",
    };
  return undefined;
}

export function predictionConclusion(
  row: Row,
  hours: string,
): { value: string; text: string; breachAt?: number } {
  if (row.status !== "completed")
    return {
      value: row.status,
      text: row.status === "failed" ? "预测失败" : label(row.status),
    };
  const horizon = row.horizons?.[hours];
  if (
    !horizon ||
    horizon.quality === "insufficient_data" ||
    !horizon.points?.length
  )
    return { value: "insufficient_data", text: "数据不足，无法判断" };
  if (horizon.quality === "provisional")
    return { value: "provisional", text: "初步预测，待验证" };
  const risk = horizon.risk;
  if (!risk || risk.level === "unknown")
    return { value: "unknown", text: "暂无法判断风险" };
  if (risk.level === "expected" || risk.level === "possible")
    return {
      value: risk.level,
      text: label(risk.level),
      breachAt: risk.first_breach_at || undefined,
    };
  if (risk.level === "normal")
    return horizon.quality === "interval_low_confidence"
      ? { value: "interval_low_confidence", text: "未见超限，区间可信度不足" }
      : { value: "normal", text: "预计不超限" };
  return { value: "unknown", text: "暂无法判断风险" };
}

export function predictionConclusions(row: Row) {
  const outcomes = ["1", "6", "24"].map((hours) => ({
    hours,
    ...predictionConclusion(row, hours),
  }));
  const first = outcomes[0]!;
  return outcomes.every(
    (item) =>
      item.value === first.value &&
      item.text === first.text &&
      item.breachAt === first.breachAt,
  )
    ? [{ ...first, hours: "1 / 6 / 24" }]
    : outcomes;
}

export function predictionSummary(row: Row) {
  const outcomes = ["1", "6", "24"].map((hours) =>
    predictionConclusion(row, hours),
  );
  if (row.status !== "completed") return outcomes[0]!;
  if (outcomes.every((item) => item.value === "insufficient_data"))
    return {
      value: "insufficient_data",
      text:
        row.quality?.status === "usable"
          ? "历史不足，暂无法预测"
          : "数据不足，暂无法预测",
    };
  const risk =
    outcomes.find((item) => item.value === "expected") ||
    outcomes.find((item) => item.value === "possible");
  if (risk) return risk;
  if (outcomes.every((item) => item.value === "normal")) return outcomes[0]!;
  if (outcomes.every((item) => item.value === "provisional"))
    return outcomes[0]!;
  if (outcomes.every((item) => item.value === "interval_low_confidence"))
    return { value: "interval_low_confidence", text: "预测区间可信度不足" };
  if (
    outcomes.some((item) =>
      ["normal", "interval_low_confidence"].includes(item.value),
    )
  )
    return { value: "provisional", text: "部分时段可判断风险" };
  return { value: "unknown", text: "暂无法判断风险" };
}

export function predictionDuration(minutes: number | null | undefined) {
  if (minutes == null || !Number.isFinite(minutes) || minutes < 0) return "—";
  const total = Math.round(minutes),
    days = Math.floor(total / 1440),
    hours = Math.floor((total % 1440) / 60),
    rest = total % 60;
  return [
    days ? days + " 天" : "",
    hours ? hours + " 小时" : "",
    rest || !total ? rest + " 分钟" : "",
  ]
    .filter(Boolean)
    .join(" ");
}
