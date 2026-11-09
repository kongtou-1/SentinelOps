import { describe, it, expect } from "vitest";
import { summarizeService } from "@/features/applications/model/businessServices";
import { resourceSlices } from "@/features/resources/model/containerServices";

describe("business service summaries", () => {
  const observations = [
    {
      container_id: "web",
      state: "running",
      health: "none",
      cpu_host_percent: 10,
      memory_bytes: 100,
    },
    {
      container_id: "api",
      state: "running",
      health: "healthy",
      cpu_host_percent: 20,
      memory_bytes: 200,
    },
    {
      container_id: "db",
      state: "running",
      cpu_host_percent: 5,
      memory_bytes: 50,
    },
  ];
  it("sums multiple containers into one service without including another service", () => {
    const a = summarizeService(["web", "api"], observations),
      b = summarizeService(["db"], observations);
    expect(a.cpu).toEqual({ value: 30, sampled: 2 });
    expect(a.memory.value).toBe(300);
    expect(a.status).toBe("unknown");
    expect(a.issues).toEqual([
      { container_id: "web", reason: "未配置健康检查", abnormal: false },
    ]);
    const slices = resourceSlices(100, 50, [
      { name: "问答服务", value: a.cpu.value },
      { name: "数据库服务", value: b.cpu.value },
    ])!;
    expect(slices.map((s) => s.value)).toEqual([30, 5, 15, 50]);
  });
  it("keeps partial sampling visible instead of inventing zeros", () => {
    const result = summarizeService(["web", "missing"], observations);
    expect(result.status).toBe("unknown");
    expect(result.unknown).toBe(1);
    expect(result.cpu).toEqual({ value: 10, sampled: 1 });
    expect(summarizeService(["missing"], observations).cpu.value).toBeNull();
  });
  it("keeps applications without containers or current observations unknown", () => {
    expect(summarizeService([], observations).status).toBe("unknown");
    const ids = ["web", "api"];
    expect(summarizeService(ids, observations).status).toBe("unknown");
    expect(summarizeService(["api"], observations).status).toBe("healthy");
    // A later failed collection must not reuse the previous healthy result.
    expect(summarizeService(ids, []).status).toBe("unknown");
    expect(summarizeService(ids, []).unknown).toBe(2);
  });
  it("reports observed failure even when another application member has no sample", () => {
    expect(
      summarizeService(
        ["api", "missing"],
        [{ container_id: "api", state: "running", health: "unhealthy" }],
      ).status,
    ).toBe("failed");
    expect(
      summarizeService(
        ["api"],
        [{ container_id: "api", state: "running", health: "starting" }],
      ).status,
    ).toBe("unknown");
  });
  it("distinguishes a partial outage, health failure and fully stopped service", () => {
    expect(
      summarizeService(["api"], [{ container_id: "api", state: "restarting" }])
        .status,
    ).toBe("failed");
    expect(
      summarizeService(
        ["web", "api"],
        [observations[0]!, { container_id: "api", state: "exited" }],
      ).status,
    ).toBe("failed");
    expect(
      summarizeService(
        ["api"],
        [{ container_id: "api", state: "running", health: "unhealthy" }],
      ).status,
    ).toBe("failed");
    expect(
      summarizeService(["api"], [{ container_id: "api", state: "exited" }])
        .status,
    ).toBe("failed");
  });
  it("distinguishes missing runtime, missing health result and an unconfigured check", () => {
    const result = summarizeService(["missing", "web", "db"], observations);
    expect(result.unknown).toBe(1);
    expect(result.issues).toEqual([
      {
        container_id: "missing",
        reason: "未取得实时运行状态",
        abnormal: false,
      },
      { container_id: "web", reason: "未配置健康检查", abnormal: false },
      { container_id: "db", reason: "未取得健康检查结果", abnormal: false },
    ]);
  });
  it("does not mistake missing CPU or memory data for an unknown health state", () => {
    const result = summarizeService(
      ["api"],
      [{ container_id: "api", state: "running", health: "healthy" }],
    );
    expect(result.status).toBe("healthy");
    expect(result.issues).toEqual([]);
    expect(result.cpu.value).toBeNull();
    expect(result.memory.value).toBeNull();
  });
});
