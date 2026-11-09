import { describe, expect, it } from "vitest";
import {
  summarizeRuntime,
  summarizeDockerHealth,
  summarizeMonitors,
} from "@/features/applications/model/serviceHealth";

describe("independent application health signals", () => {
  const ids = ["api", "metrics"];
  const observations = [
    { container_id: "api", state: "running", health: "healthy" },
    { container_id: "metrics", state: "running", health: "none" },
  ];
  it("keeps running containers running when Docker checks are unconfigured", () => {
    expect(summarizeRuntime(ids, observations)).toMatchObject({
      status: "healthy",
      running: 2,
      total: 2,
      unknown: 0,
    });
    expect(summarizeDockerHealth(ids, observations)).toMatchObject({
      status: "unknown",
      reason: "1 个通过；1 个未配置",
    });
    expect(
      summarizeMonitors([
        { enabled: true, importance: "critical", status: "healthy" },
      ]).status,
    ).toBe("healthy");
  });
  it("keeps Docker failures visible without treating them as stopped processes", () => {
    const unhealthy = [
      { container_id: "api", state: "running", health: "unhealthy" },
    ];
    expect(summarizeRuntime(["api"], unhealthy).status).toBe("healthy");
    expect(summarizeDockerHealth(["api"], unhealthy).status).toBe("failed");
  });
  it("reports a stopped process even when other runtime samples are missing", () => {
    expect(
      summarizeRuntime(ids, [{ container_id: "api", state: "exited" }]),
    ).toMatchObject({
      status: "failed",
      running: 0,
      unknown: 1,
    });
  });
  it("does not reuse healthy runtime observations after collection fails", () => {
    expect(summarizeRuntime(ids, []).status).toBe("unknown");
    expect(summarizeRuntime([], observations).status).toBe("unknown");
    expect(summarizeRuntime(ids, [observations[0]!]).unknown).toBe(1);
  });
  it("distinguishes an unconfigured Docker check from a missing result", () => {
    expect(summarizeDockerHealth(["metrics"], observations).status).toBe(
      "unconfigured",
    );
    expect(summarizeDockerHealth(["metrics"], []).status).toBe("unknown");
    expect(summarizeDockerHealth(["api"], observations).status).toBe("healthy");
  });
  it("retains starting and stopped Docker check results", () => {
    expect(
      summarizeDockerHealth(ids, [
        { container_id: "api", health: "starting" },
        { container_id: "metrics", health: "stopped" },
      ]),
    ).toMatchObject({ status: "failed", reason: "1 个启动中；1 个已停止" });
  });
});

describe("platform service monitors", () => {
  const critical = { enabled: true, importance: "critical", status: "healthy" };
  it("distinguishes missing configuration from failed reads", () => {
    expect(summarizeMonitors([]).text).toBe("未配置");
    expect(summarizeMonitors(undefined)).toMatchObject({
      status: "unknown",
      text: "读取失败",
    });
  });
  it("distinguishes paused monitors and optional-only monitoring", () => {
    expect(summarizeMonitors([{ ...critical, enabled: false }]).status).toBe(
      "paused",
    );
    expect(
      summarizeMonitors([{ ...critical, importance: "optional" }]).text,
    ).toBe("未配置关键监测");
    expect(
      summarizeMonitors([
        { ...critical, enabled: false },
        { ...critical, importance: "optional" },
      ]).text,
    ).toBe("关键监测已暂停");
  });
  it("requires all enabled critical probes to pass", () => {
    expect(
      summarizeMonitors([critical, { ...critical, status: "unknown" }]).status,
    ).toBe("unknown");
    expect(
      summarizeMonitors([critical, { ...critical, status: "failed" }]).status,
    ).toBe("failed");
    expect(
      summarizeMonitors([
        critical,
        { ...critical, enabled: false, status: "failed" },
      ]).status,
    ).toBe("healthy");
  });
  it("never treats an expired success or failure as current evidence", () => {
    expect(
      summarizeMonitors([critical, { ...critical, status: "stale" }]).status,
    ).toBe("stale");
    expect(
      summarizeMonitors([
        { ...critical, status: "failed" },
        { ...critical, status: "stale" },
      ]).status,
    ).toBe("failed");
  });
  it("reports optional failures without overriding critical API availability", () => {
    expect(
      summarizeMonitors([
        critical,
        { ...critical, importance: "optional", status: "failed" },
      ]),
    ).toMatchObject({
      status: "healthy",
      reason: "1 项关键监测通过；1 项辅助监测异常",
    });
  });
  it("keeps optional probe expiration visible", () => {
    expect(
      summarizeMonitors([
        { ...critical, importance: "optional", status: "stale" },
      ]),
    ).toMatchObject({ text: "未配置关键监测", reason: "1 项辅助监测结果过期" });
  });
});
