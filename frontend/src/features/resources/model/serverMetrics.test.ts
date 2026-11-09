import { beforeEach, describe, expect, it, vi } from "vitest";
import { readServerSamples } from "@/features/resources/model/serverMetrics";
import { api } from "@/api/client";
vi.mock("@/api/client", () => ({ api: vi.fn() }));
const mocked = vi.mocked(api);
beforeEach(() => vi.resetAllMocks());
describe("server summary samples", () => {
  it("uses one request for 50 servers and none for an empty page", async () => {
    mocked.mockResolvedValue([]);
    await readServerSamples([]);
    expect(mocked).not.toHaveBeenCalled();
    await readServerSamples(
      Array.from({ length: 50 }, (_, i) => ({ id: String(i) })),
    );
    expect(mocked).toHaveBeenCalledTimes(1);
    expect(
      new URLSearchParams(mocked.mock.calls[0]![0].split("?")[1]).getAll(
        "server_ids",
      ),
    ).toHaveLength(50);
  });
  it("preserves zero and a failed target while stale and failed metrics remain unknown", async () => {
    mocked.mockResolvedValue([
      {
        server_id: "s",
        metrics: {
          target_up: { availability: "unavailable", value: 0 },
          host_cpu_percent: { availability: "available", value: 0 },
          host_memory_percent: { availability: "stale", value: 88 },
          host_disk_percent: { availability: "unavailable", value: null },
        },
      },
    ]);
    expect((await readServerSamples([{ id: "s" }])).s).toEqual({
      target_up: 0,
      host_cpu_percent: 0,
      host_memory_percent: null,
      host_disk_percent: null,
    });
  });
  it("handles summary failure and missing servers without inventing healthy zeros", async () => {
    mocked.mockRejectedValue(new Error("offline"));
    const result = await readServerSamples([{ id: "s" }]);
    expect(Object.values(result.s!)).toEqual([null, null, null, null]);
  });
});
