import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, request } from "@/api/client";
import { readPredictionRuns, readPredictionTargets } from "./api";

vi.mock("@/api/client", async (original) => ({
  ...(await original<typeof import("@/api/client")>()),
  api: vi.fn(),
  request: vi.fn(),
}));
beforeEach(() => vi.resetAllMocks());

describe("prediction target names", () => {
  it("resolves names from each run snapshot and reads repeated targets only once", async () => {
    vi.mocked(api).mockImplementation(
      async (path) =>
        ({ name: path.endsWith("old") ? "旧容器" : "新容器" }) as any,
    );
    const rows = await readPredictionTargets(
      [
        { config: { container_id: "old", server_id: "host" } },
        { config: { container_id: "new", server_id: "host" } },
        { config: { container_id: "old", server_id: "host" } },
      ],
      () => true,
      new AbortController().signal,
    );
    expect(rows.map((row) => row.target_name)).toEqual([
      "旧容器",
      "新容器",
      "旧容器",
    ]);
    expect(api).toHaveBeenCalledTimes(2);
  });
  it("keeps records readable when resource access is not granted", async () => {
    const rows = await readPredictionTargets(
      [{ config: { container_id: "hidden" } }],
      () => false,
      new AbortController().signal,
    );
    expect(api).not.toHaveBeenCalled();
    expect(rows[0]?.target_name).toBe("容器（无查看权限）");
  });
  it("preserves prediction results when an old target no longer exists", async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error("404"));
    const rows = await readPredictionTargets(
      [{ id: "run", config: { server_id: "deleted" } }],
      () => true,
      new AbortController().signal,
    );
    expect(rows[0]?.id).toBe("run");
    expect(rows[0]?.target_name).toBe("服务器（名称暂不可用）");
  });
});
describe("prediction history requests", () => {
  it("reads every page for the selected task, including forecasts issued before the visible history", async () => {
    const signal = new AbortController().signal;
    vi.mocked(request)
      .mockResolvedValueOnce({
        data: Array.from({ length: 100 }, (_, id) => ({ id })),
        total: 127,
      } as any)
      .mockResolvedValueOnce({
        data: Array.from({ length: 27 }, (_, id) => ({ id: id + 100 })),
        total: 127,
      } as any);
    const result = await readPredictionRuns("task", 172800, 259200, signal);
    expect(result).toHaveLength(127);
    const queries = vi.mocked(request).mock.calls.map(([path, init]) => {
      expect(init?.signal).toBe(signal);
      return new URLSearchParams(path.split("?")[1]);
    });
    expect(queries.map((q) => q.get("offset"))).toEqual(["0", "100"]);
    for (const q of queries) {
      expect(q.get("task_id")).toBe("task");
      expect(q.get("status")).toBe("completed");
      expect(q.get("start")).toBe("1970-01-02T00:00:00.000Z");
      expect(q.get("end")).toBe("1970-01-04T00:00:00.000Z");
    }
    expect(api).not.toHaveBeenCalled();
  });
  it("retrieves the last completed run for a task with no recent batches", async () => {
    const signal = new AbortController().signal;
    vi.mocked(request).mockResolvedValueOnce({ data: [], total: 0 } as any);
    vi.mocked(api).mockResolvedValueOnce([{ id: "old" }]);
    expect(await readPredictionRuns("task", 172800, 259200, signal)).toEqual([
      { id: "old" },
    ]);
    expect(api).toHaveBeenCalledWith(
      "/predictions?task_id=task&status=completed&limit=1",
      "GET",
      undefined,
      signal,
    );
  });
  it("does not present a partially loaded history as complete after a later page fails", async () => {
    vi.mocked(request)
      .mockResolvedValueOnce({
        data: Array.from({ length: 100 }, () => ({})),
        total: 127,
      } as any)
      .mockRejectedValueOnce(new Error("offline"));
    await expect(
      readPredictionRuns("task", 172800, 259200, new AbortController().signal),
    ).rejects.toThrow("offline");
  });
});
