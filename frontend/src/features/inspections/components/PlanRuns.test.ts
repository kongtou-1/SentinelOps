import { beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick } from "vue";
import PlanRuns from "./PlanRuns.vue";
import type { Run } from "@/features/inspections/ui";
const mocks = vi.hoisted(() => ({
  request: vi.fn(),
  all: vi.fn(),
  api: vi.fn(),
  confirm: vi.fn(),
  emit: vi.fn(),
}));
vi.mock("vue", async (original) => ({
  ...(await original<typeof import("vue")>()),
  useSSRContext: () => ({ modules: new Set() }),
}));
vi.mock("@/api/client", () => ({
  request: mocks.request,
  api: mocks.api,
  all: mocks.all,
  query: (values: Record<string, string | number>) =>
    "?" +
    new URLSearchParams(Object.entries(values).map(([k, v]) => [k, String(v)])),
}));
vi.mock("@/features/auth/store", () => ({
  useAuth: () => ({ can: () => true }),
}));
vi.mock("@/utils/confirmOperation", () => ({
  confirmOperation: mocks.confirm,
}));
vi.mock("element-plus", () => ({
  ElMessage: { success: vi.fn(), error: vi.fn() },
}));
vi.mock("@/composables/usePoll", () => ({ usePoll: vi.fn() }));
type Table = {
  rows: { value: Run[] };
  page: { value: number };
  status: { value: string };
  runKind: { value: string };
  hasObjects: { value: string };
  load: () => Promise<void>;
  remove: (row: Run) => Promise<void>;
  exportRows: (signal: AbortSignal) => Promise<Run[]>;
};
function setup() {
  return (
    PlanRuns as unknown as { setup: (props: object, ctx: object) => Table }
  ).setup(
    {
      planId: "task-1",
      planName: "订单巡检",
      refresh: 0,
      returnTo: "/inspections?task=task-1",
    },
    { expose: vi.fn(), emit: mocks.emit },
  );
}
beforeEach(() => {
  vi.resetAllMocks();
  mocks.request.mockResolvedValue({ data: [], total: 0 });
});
describe("任务下的巡检记录", () => {
  it("默认仅查有对象的记录，切换对象筛选同步分页与导出", async () => {
    const table = setup();
    await table.load();
    expect(mocks.request.mock.calls.at(-1)![0]).toContain("has_objects=true");
    table.page.value = 3;
    await nextTick();
    table.hasObjects.value = "false";
    await nextTick();
    await nextTick();
    expect(table.page.value).toBe(1);
    expect(mocks.request.mock.calls.at(-1)![0]).toContain("has_objects=false");
    expect(mocks.request.mock.calls.at(-1)![0]).toContain("offset=0");
    const signal = new AbortController().signal;
    await table.exportRows(signal);
    expect(mocks.all).toHaveBeenLastCalledWith(
      expect.stringContaining("has_objects=false"),
      signal,
    );
    table.hasObjects.value = "";
    await nextTick();
    expect(mocks.request.mock.calls.at(-1)![0]).toContain("has_objects=");
  });
  it("翻页与导出始终绑定当前任务和记录筛选", async () => {
    const table = setup();
    table.status.value = "failed";
    table.runKind.value = "standard";
    await nextTick();
    table.page.value = 2;
    await nextTick();
    const url = mocks.request.mock.calls.at(-1)![0] as string;
    expect(url).toContain("plan_id=task-1");
    expect(url).toContain("status=failed");
    expect(url).toContain("offset=10");
    expect(url).toContain("run_kind=standard");
    const signal = new AbortController().signal;
    await table.exportRows(signal);
    expect(mocks.all).toHaveBeenCalledWith(
      expect.stringContaining("plan_id=task-1&status=failed&run_kind=standard"),
      signal,
    );
  });
  it("切换筛选回到第一页，迟到的旧响应不会覆盖新记录", async () => {
    const table = setup();
    let resolve!: (r: { data: Run[]; total: number }) => void;
    mocks.request.mockReturnValueOnce(
      new Promise((done) => {
        resolve = done;
      }),
    );
    const old = table.load();
    table.status.value = "failed";
    await nextTick();
    resolve({ data: [{ id: "old" } as Run], total: 1 });
    await old;
    expect(table.rows.value).toEqual([]);
    expect(table.page.value).toBe(1);
  });
});

describe("删除巡检记录", () => {
  it("取消确认不删除，确认后只删除选中记录并刷新任务", async () => {
    const table = setup();
    const row = { id: "run-7", created_at: "2026-10-31T00:00:00Z" } as Run;
    mocks.confirm.mockResolvedValueOnce(false);
    await table.remove(row);
    expect(mocks.api).not.toHaveBeenCalled();
    mocks.confirm.mockResolvedValueOnce(true);
    await table.remove(row);
    expect(mocks.api).toHaveBeenCalledWith("/inspection-runs/run-7", "DELETE");
    expect(mocks.emit).toHaveBeenCalledWith("changed");
  });
});
