import { describe, expect, it } from "vitest";
import {
  activeTask,
  effectiveApproval,
  label,
  metricUnit,
  metricValue,
  recoveryLabel,
} from "@/utils/display";
import { canEditReport, canExportReport } from "@/utils/workflows";
describe("业务状态与指标", () => {
  it("指标配置尚未加载时可正常渲染，核验等待与审批等待独立显示", () => {
    expect(metricUnit(undefined)).toBe("");
    expect(recoveryLabel("pending")).toBe("待核验");
    expect(label("pending")).toBe("待审批");
  });
  it("缺失和非有限样本不能变成零值", () => {
    for (const v of [null, undefined, "NaN", "+Inf", "-Inf"])
      expect(metricValue("host_cpu_percent", v)).toBeNull();
    expect(metricValue("host_cpu_percent", "0")).toBe(0);
  });
  it("LLM比例转百分比，延迟保留秒，容器CPU可超过100%", () => {
    expect(metricValue("llm_error_rate", "0.176")).toBeCloseTo(17.6);
    expect(metricValue("llm_latency_p95_seconds", "2.41")).toBe(2.41);
    expect(metricValue("container_cpu_percent", "215.6")).toBe(215.6);
  });
  it("审批过期后禁止继续操作，但不改写已消费或拒绝状态", () => {
    const expires_at = "2026-10-04T00:00:00Z",
      now = Date.parse("2026-10-04T01:00:00Z");
    expect(effectiveApproval({ status: "approved", expires_at }, now)).toBe(
      "expired",
    );
    expect(effectiveApproval({ status: "pending", expires_at }, now)).toBe(
      "expired",
    );
    expect(effectiveApproval({ status: "consumed", expires_at }, now)).toBe(
      "consumed",
    );
    expect(effectiveApproval({ status: "rejected", expires_at }, now)).toBe(
      "rejected",
    );
  });
  it("异步任务只有活动阶段需要轮询", () => {
    for (const state of [
      "queued",
      "running",
      "prechecking",
      "executing",
      "verifying",
      "sending",
    ])
      expect(activeTask(state)).toBe(true);
    for (const state of [
      "failed",
      "interrupted",
      "unknown",
      "succeeded",
      "draft",
      "approved",
      "sent",
    ])
      expect(activeTask(state)).toBe(false);
  });
  it("报告编辑需要管理权限且为草稿，导出只能在批准后开放", () => {
    expect(canEditReport("draft", true)).toBe(true);
    expect(canEditReport("draft", false)).toBe(false);
    for (const status of ["queued", "running", "approved", "failed"])
      expect(canEditReport(status, true)).toBe(false);
    for (const status of ["draft", "queued", "running", "failed"])
      expect(canExportReport(status)).toBe(false);
    expect(canExportReport("approved")).toBe(true);
  });
});
