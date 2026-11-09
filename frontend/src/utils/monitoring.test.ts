import { describe, expect, it } from "vitest";
import { chartPoints, parseTimeWindow } from "@/utils/monitoring";
describe("monitoring chart gaps", () => {
  it("preserves one explicit time window when drilling into related resources", () => {
    expect(
      parseTimeWindow("2026-10-16T10:00:00+08:00", "2026-10-16T11:00:00+08:00"),
    ).toEqual({
      start: "2026-10-16T02:00:00.000Z",
      end: "2026-10-16T03:00:00.000Z",
    });
    expect(parseTimeWindow("invalid", "2026-10-16")).toBeUndefined();
    expect(parseTimeWindow("2026-10-16", "2026-10-15")).toBeUndefined();
    expect(parseTimeWindow("2026-09-01", "2026-10-16")).toBeUndefined();
  });
  it("retains a true zero and breaks lines at invalid samples and missing steps", () => {
    expect(
      chartPoints(
        [
          [1000, "0"],
          [1030, "NaN"],
          [1090, "12"],
        ],
        "host_cpu_percent",
        30,
      ),
    ).toEqual([
      [1000000, 0],
      [1030000, null],
      [1060000, null],
      [1090000, 12],
    ]);
  });
  it("preserves backend nulls and percentage conversion", () => {
    expect(
      chartPoints(
        [
          [1000, "0"],
          [1030, null],
          [1060, "0.5"],
        ],
        "llm_success_rate",
        30,
      ),
    ).toEqual([
      [1000000, 0],
      [1030000, null],
      [1060000, 50],
    ]);
  });
});
