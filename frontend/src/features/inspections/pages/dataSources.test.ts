import { beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick } from "vue";
import DataSources from "./DataSources.vue";
import { sourceListState } from "@/features/inspections/navigation";
import type { DataSource } from "@/features/inspections/ui";

const mocks = vi.hoisted(() => ({ request: vi.fn(), unmount: vi.fn() }));
vi.mock("vue", async (original) => ({
  ...(await original<typeof import("vue")>()),
  onUnmounted: mocks.unmount,
  useSSRContext: () => ({ modules: new Set() }),
}));
vi.mock("@/api/client", () => ({
  api: vi.fn(),
  all: vi.fn(),
  request: mocks.request,
  query: (values: Record<string, string | number>) =>
    "?" +
    new URLSearchParams(
      Object.entries(values).map(([key, value]) => [key, String(value)]),
    ),
}));
vi.mock("@/features/auth/store", () => ({
  useAuth: () => ({ can: () => true }),
}));
vi.mock("@/composables/usePoll", () => ({ usePoll: vi.fn() }));

type Page = {
  view: { value: "api" | "database" | "databases" };
  page: { value: number };
  search: { value: string };
  rows: { value: DataSource[] };
  creatingKind: { value: "api" | "database" };
  load: () => Promise<void>;
  edit: () => void;
};
function setup() {
  return (
    DataSources as unknown as { setup: (props: object, ctx: object) => Page }
  ).setup({}, { expose: vi.fn() });
}
beforeEach(() => {
  vi.resetAllMocks();
  Object.assign(sourceListState, { search: "", page: 1, view: "api" });
  mocks.request.mockResolvedValue({ data: [], total: 0 });
});

describe("API与数据库入口", () => {
  it("API页只加载API，数据库查询页从第一页加载数据库查询", async () => {
    const value = setup();
    await value.load();
    expect(mocks.request).toHaveBeenLastCalledWith(
      expect.stringContaining("kind=api"),
    );
    value.page.value = 3;
    await nextTick();
    value.view.value = "database";
    await nextTick();
    await nextTick();
    expect(value.page.value).toBe(1);
    expect(mocks.request).toHaveBeenLastCalledWith(
      expect.stringContaining("kind=database&offset=0"),
    );
    value.edit();
    expect(value.creatingKind.value).toBe("database");
    value.view.value = "api";
    await nextTick();
    value.edit();
    expect(value.creatingKind.value).toBe("api");
  });

  it("已接入数据库页不请求巡检取数配置", async () => {
    const value = setup();
    value.view.value = "databases";
    await nextTick();
    await value.load();
    expect(mocks.request).not.toHaveBeenCalled();
  });

  it("切换到数据库查询后，较晚返回的API响应不会覆盖列表", async () => {
    const value = setup();
    let resolve!: (result: { data: DataSource[]; total: number }) => void;
    mocks.request.mockReturnValueOnce(
      new Promise((done) => {
        resolve = done;
      }),
    );
    const loading = value.load();
    value.view.value = "database";
    await nextTick();
    resolve({ data: [{ id: "old-api" } as DataSource], total: 1 });
    await loading;
    expect(value.rows.value).toEqual([]);
  });
});

describe("数据源列表上下文", () => {
  it("离开后再次进入保留分类、搜索词和页码", () => {
    const value = setup();
    value.view.value = "database";
    value.search.value = "订单";
    value.page.value = 3;
    mocks.unmount.mock.calls[0]![0]();
    const restored = setup();
    expect(restored.view.value).toBe("database");
    expect(restored.search.value).toBe("订单");
    expect(restored.page.value).toBe(3);
  });
});
