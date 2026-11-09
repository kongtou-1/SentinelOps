import { describe, expect, it } from "vitest";
import {
  canNormallyCloseIncident,
  incidentCloseReason,
} from "@/utils/workflows";

const recovered = {
  status: "recovered",
  alert_count: 2,
  firing_alert_count: 0,
  recovery_pending_alert_count: 0,
  recovery_verified_alert_count: 2,
  recovery_terminated_alert_count: 0,
};
describe("事故普通关闭条件", () => {
  it("触发中或待核验时禁用，并解释原因", () => {
    const firing = {
      ...recovered,
      status: "in_progress",
      firing_alert_count: 1,
    };
    expect(canNormallyCloseIncident(firing)).toBe(false);
    expect(incidentCloseReason(firing)).toContain("1 条告警触发中");
    const pending = {
      ...recovered,
      status: "open",
      recovery_pending_alert_count: 2,
    };
    expect(canNormallyCloseIncident(pending)).toBe(false);
    expect(incidentCloseReason(pending)).toContain("2 条告警恢复待核验");
  });
  it("已恢复可关闭，已关闭和已合并不可再次操作", () => {
    expect(canNormallyCloseIncident(recovered)).toBe(true);
    expect(incidentCloseReason(recovered)).toBe("");
    for (const status of ["closed", "merged"])
      expect(canNormallyCloseIncident({ ...recovered, status })).toBe(false);
  });
  it("核验全部完成或终止时可人工结束，缺失告警不能伪造恢复", () => {
    expect(
      canNormallyCloseIncident({
        ...recovered,
        status: "open",
        recovery_verified_alert_count: 1,
        recovery_terminated_alert_count: 1,
      }),
    ).toBe(true);
    expect(
      canNormallyCloseIncident({
        ...recovered,
        status: "open",
        alert_count: 0,
      }),
    ).toBe(false);
    expect(canNormallyCloseIncident()).toBe(false);
  });
});
