import { beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick, type Ref } from "vue";
import type { Models } from "@/api/client";
import RecycleBin from "./RecycleBin.vue";

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  request: vi.fn(),
  confirm: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
}));
vi.mock("vue", async (original) => ({
  ...(await original<typeof import("vue")>()),
  useSSRContext: () => ({ modules: new Set() }),
}));
vi.mock("@/api/client", () => ({
  api: mocks.api,
  request: mocks.request,
  query: () => "",
}));
vi.mock("@/composables/usePoll", () => ({ usePoll: vi.fn() }));
vi.mock("@/utils/confirmOperation", () => ({
  confirmOperation: mocks.confirm,
}));
vi.mock("element-plus", () => ({
  ElMessage: { success: mocks.success, error: mocks.error },
}));

type Entry = Models["RecycleEntry"];
type Page = {
  rows: Ref<Entry[]>;
  page: Ref<number>;
  busy: Ref<string>;
  error: Ref<string>;
  remove: (row: Entry) => Promise<void>;
  restore: (row: Entry) => Promise<void>;
};
const row: Entry = {
  kind: "servers",
  id: "server-1",
  name: "测试主机",
  label: "服务器",
  created_at: "2026-10-30T01:00:00Z",
  updated_at: "2026-10-31T01:00:00Z",
  deleted_at: "2026-10-31T01:00:00.123456Z",
  restore_note: "恢复后保持停用",
  blocked_reason: "请先恢复关联资源",
};
function setup() {
  return (
    RecycleBin as unknown as { setup: (props: object, ctx: object) => Page }
  ).setup({}, { expose: vi.fn() });
}
beforeEach(() => {
  vi.resetAllMocks();
  mocks.api.mockResolvedValue([]);
  mocks.request.mockResolvedValue({ data: [], total: 0 });
});

describe("回收站永久删除", () => {
  it("确认框指出资源名称与不可恢复，取消后不发送写请求", async () => {
    const page = setup();
    mocks.confirm.mockResolvedValue(false);
    await page.remove(row);
    expect(mocks.confirm).toHaveBeenCalledWith(
      expect.stringMatching(/服务器“测试主机”.*无法恢复/),
      "永久删除",
    );
    expect(mocks.api).not.toHaveBeenCalled();
    expect(page.busy.value).toBe("");
  });

  it("绑定删除时间，删除成功刷新列表和类型数量，恢复阻塞不影响发起删除", async () => {
    const page = setup();
    page.rows.value = [row];
    mocks.confirm.mockResolvedValue(true);
    await page.remove(row);
    expect(mocks.api).toHaveBeenCalledWith(
      "/system/recycle-bin/servers/server-1",
      "DELETE",
      { expected_deleted_at: row.deleted_at },
    );
    expect(mocks.api).toHaveBeenCalledWith("/system/recycle-bin/types");
    expect(mocks.request).toHaveBeenCalled();
    expect(mocks.success).toHaveBeenCalledWith("资源已永久删除");
    expect(page.rows.value).toEqual([]);
  });

  it("删除最后一行后回到上一页", async () => {
    const page = setup();
    page.page.value = 2;
    await nextTick();
    page.rows.value = [row];
    mocks.confirm.mockResolvedValue(true);
    await page.remove(row);
    await nextTick();
    expect(page.page.value).toBe(1);
  });

  it("关联或状态冲突保留行，展示服务端原因，允许重新操作", async () => {
    const page = setup();
    page.rows.value = [row];
    mocks.confirm.mockResolvedValue(true);
    mocks.api.mockRejectedValueOnce(new Error("仍有容器引用，无法永久删除"));
    await page.remove(row);
    expect(page.rows.value).toEqual([row]);
    expect(page.error.value).toContain("仍有容器引用");
    expect(mocks.success).not.toHaveBeenCalled();
    expect(mocks.error).toHaveBeenCalledWith(page.error.value);
    expect(page.busy.value).toBe("");
    expect(mocks.request).not.toHaveBeenCalled();
  });

  it("确认或删除进行中不重复删除，也不同时恢复", async () => {
    const page = setup();
    let confirm!: (result: boolean) => void;
    mocks.confirm.mockReturnValueOnce(
      new Promise((done) => {
        confirm = done;
      }),
    );
    const pending = page.remove(row);
    await page.remove(row);
    await page.restore(row);
    expect(mocks.confirm).toHaveBeenCalledTimes(1);
    confirm(true);
    await pending;
    expect(
      mocks.api.mock.calls.filter((args) => args[1] === "DELETE"),
    ).toHaveLength(1);
  });
});
