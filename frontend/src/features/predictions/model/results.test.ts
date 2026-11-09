import { describe, expect, it } from "vitest";
import {
  predictionConclusion,
  predictionConclusions,
  predictionTarget,
  predictionSummary,
  predictionDuration,
} from "./results";

const result = (quality: string, level: string, first_breach_at?: number) => ({
  status: "completed",
  horizons: {
    "24": {
      quality,
      risk: { level, first_breach_at },
      points: [{ timestamp: 1200, value: 1 }],
    },
  },
});

describe("human-readable prediction conclusions", () => {
  it("keeps sampling completeness separate from prediction readiness", () => {
    expect(
      predictionSummary({
        ...result("insufficient_data", "unknown"),
        quality: { status: "usable", samples: 5 },
        fresh: true,
      }),
    ).toEqual({ value: "insufficient_data", text: "历史不足，暂无法预测" });
    expect(predictionSummary(result("insufficient_data", "unknown")).text).toBe(
      "数据不足，暂无法预测",
    );
  });
  it("surfaces risk even when other horizons lack data and labels partial results", () => {
    expect(predictionSummary(result("validated", "expected", 1200)).value).toBe(
      "expected",
    );
    expect(predictionSummary(result("validated", "normal"))).toEqual({
      value: "provisional",
      text: "部分时段可判断风险",
    });
    const interval = result("interval_low_confidence", "normal").horizons["24"];
    expect(
      predictionSummary({
        status: "completed",
        horizons: { "1": interval, "6": interval, "24": interval },
      }).value,
    ).toBe("interval_low_confidence");
  });
  it("shows short histories and missing time spans in readable units", () => {
    expect(predictionDuration(5 * 5)).toBe("25 分钟");
    expect(predictionDuration(158 * 5)).toBe("13 小时 10 分钟");
    expect(predictionDuration(238 * 5)).toBe("19 小时 50 分钟");
    expect(predictionDuration((1793098800 - 1793027700) / 60)).toBe(
      "19 小时 45 分钟",
    );
    expect(predictionDuration(1440)).toBe("1 天");
    expect(predictionDuration(0)).toBe("0 分钟");
    expect(predictionDuration(undefined)).toBe("—");
  });
  it("merges equal horizon conclusions without hiding different risks or breach times", () => {
    const horizon = result("insufficient_data", "unknown").horizons["24"];
    const same = {
      status: "completed",
      horizons: { "1": horizon, "6": horizon, "24": horizon },
    };
    expect(predictionConclusions(same)).toHaveLength(1);
    expect(predictionConclusions(same)[0]?.hours).toBe("1 / 6 / 24");
    const mixed = {
      status: "completed",
      horizons: {
        "1": result("validated", "normal").horizons["24"],
        "6": result("validated", "expected", 1200).horizons["24"],
        "24": result("validated", "expected", 1800).horizons["24"],
      },
    };
    expect(
      predictionConclusions(mixed).map((item) => [
        item.hours,
        item.text,
        item.breachAt,
      ]),
    ).toEqual([
      ["1", "预计不超限", undefined],
      ["6", "预计超限", 1200],
      ["24", "预计超限", 1800],
    ]);
  });
  it("does not equate a finished run or fresh data with a usable prediction", () => {
    expect(
      predictionConclusion(
        { ...result("insufficient_data", "unknown"), fresh: true },
        "24",
      ),
    ).toEqual({ value: "insufficient_data", text: "数据不足，无法判断" });
    expect(predictionConclusion(result("validated", "normal"), "1").value).toBe(
      "insufficient_data",
    );
  });
  it("keeps preliminary forecasts distinct from validated risk decisions", () => {
    expect(
      predictionConclusion(result("provisional", "unknown"), "24"),
    ).toEqual({ value: "provisional", text: "初步预测，待验证" });
    expect(
      predictionConclusion(result("interval_low_confidence", "normal"), "24")
        .value,
    ).toBe("interval_low_confidence");
    expect(predictionConclusion(result("validated", "normal"), "24").text).toBe(
      "预计不超限",
    );
  });
  it("preserves the first expected or possible breach time", () => {
    expect(
      predictionConclusion(result("validated", "expected", 1200), "24"),
    ).toEqual({ value: "expected", text: "预计超限", breachAt: 1200 });
    expect(
      predictionConclusion(result("validated", "possible", 1800), "24"),
    ).toEqual({ value: "possible", text: "可能超限", breachAt: 1800 });
  });
  it("does not reuse a forecast conclusion while a run is queued or has failed", () => {
    for (const status of ["queued", "running", "failed", "interrupted"])
      expect(
        predictionConclusion({ ...result("validated", "normal"), status }, "24")
          .value,
      ).toBe(status);
  });
  it("resolves the actual prediction target before its parent server", () => {
    expect(
      predictionTarget({ server_id: "host", container_id: "old-container" })
        ?.path,
    ).toBe("/containers/old-container");
    expect(
      predictionTarget({ server_id: "host", llm_endpoint_id: "model" })?.path,
    ).toBe("/llm-endpoints/model");
    expect(predictionTarget({ server_id: "host" })?.path).toBe("/servers/host");
    expect(predictionTarget({})).toBeUndefined();
  });
});
