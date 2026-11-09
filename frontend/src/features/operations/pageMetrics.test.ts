import { beforeEach, describe, expect, it, vi } from "vitest";
import { inventoryValues, metricNames, readPageMetrics } from "./pageMetrics";

const mocks = vi.hoisted(() => ({ all: vi.fn(), request: vi.fn() }));
vi.mock("@/api/client", async (original) => ({
  ...(await original<typeof import("@/api/client")>()),
  all: mocks.all,
  request: mocks.request,
}));
beforeEach(() => vi.resetAllMocks());

describe("按业务选择关键指标", () => {
  it("服务器、服务登记与 LLM 的统计口径各自独立", () => {
    expect(
      inventoryValues("servers", [
        { monitoring_enabled: true, credential_id: "key" },
        { monitoring_enabled: false, credential_id: null },
      ]),
    ).toEqual([2, 1, 1]);
    expect(
      inventoryValues("applications", [
        { enabled: true, owner: "owner" },
        { enabled: false, owner: " " },
      ]),
    ).toEqual([2, 1, 1]);
    expect(metricNames.servers).not.toEqual(metricNames.applications);
    expect(metricNames.servers).not.toEqual(metricNames["llm-endpoints"]);
  });
  it("过期、未知和停用的 LLM 探测不算可用或失败", () => {
    expect(
      inventoryValues("llm-endpoints", [
        { status: "healthy" },
        { status: "failed" },
        { status: "stale", last_probe_success: true },
        { status: "unknown" },
        { status: "disabled" },
      ]),
    ).toEqual([1, 1, 2, 1]);
  });
  it("预测最新结果必须完成且有效；过期结果不算有效", () => {
    expect(
      inventoryValues("predictions", [
        { enabled: true, latest_result: { status: "completed", fresh: true } },
        { enabled: true, latest_result: { status: "completed", fresh: false } },
        { enabled: false, latest_result: { status: "failed", fresh: false } },
        { enabled: true },
      ]),
    ).toEqual([3, 1, 1]);
  });
  it("知识可检索数量排除停用、过期与未完成索引的文档", () => {
    const now = Date.parse("2026-10-30T00:00:00Z");
    expect(
      inventoryValues(
        "knowledge",
        [
          { enabled: true, index_status: "ready" },
          { enabled: false, index_status: "ready" },
          {
            enabled: true,
            index_status: "ready",
            expires_at: "2026-10-29T00:00:00Z",
          },
          { enabled: true, index_status: "queued" },
          { enabled: true, index_status: "failed" },
        ],
        now,
      ),
    ).toEqual([5, 1, 1]);
  });
  it("分页库存通过完整列表读取聚合，不使用当前表格页", async () => {
    mocks.all.mockResolvedValue([
      { enabled: true, owner: "A" },
      { enabled: true, owner: "" },
      { enabled: false, owner: "B" },
    ]);
    const metrics = await readPageMetrics("applications");
    expect(metrics.map((m) => m.value)).toEqual([3, 2, 1]);
    expect(mocks.all).toHaveBeenCalledWith("/applications", undefined);
  });
});

describe("分页总量与失败状态", () => {
  it("执行统计使用总量头并合并执行阶段，传递取消信号", async () => {
    const counts: Record<string, number> = {
      queued: 3,
      prechecking: 2,
      executing: 4,
      verifying: 1,
      failed: 7,
    };
    mocks.request.mockImplementation(async (path: string) => {
      const q = new URL(path, "http://localhost").searchParams;
      expect(q.get("limit")).toBe("1");
      return {
        data: [{ id: "one-row" }],
        total: counts[q.get("status") || ""] ?? 400,
      };
    });
    const signal = new AbortController().signal;
    expect(
      (await readPageMetrics("executions", {}, signal)).map((m) => m.value),
    ).toEqual([400, 10, 7]);
    expect(
      mocks.request.mock.calls.every(([, init]) => init.signal === signal),
    ).toBe(true);
    expect(
      mocks.request.mock.calls.filter(([url]) => url.includes("start=")),
    ).toHaveLength(2);
  });
  it("缺少总量或读取失败显示不可用，正常零保留为零", async () => {
    mocks.request.mockImplementation(async (path: string) => {
      if (path.includes("acknowledged=false")) throw new Error("failed");
      return {
        data: [{ id: "one-row" }],
        total: path.includes("severity=") ? null : 0,
      };
    });
    expect((await readPageMetrics("alerts")).map((m) => m.value)).toEqual([
      0,
      null,
      null,
    ]);
  });
  it("巡检只使用接口支持的筛选，并保留任务范围", async () => {
    mocks.request.mockResolvedValue({ data: [], total: 0 });
    await readPageMetrics("inspection-runs", { plan_id: "plan-a" });
    for (const [path] of mocks.request.mock.calls) {
      const q = new URL(path, "http://localhost").searchParams;
      expect(q.get("plan_id")).toBe("plan-a");
      expect(q.has("start")).toBe(false);
    }
  });
});
