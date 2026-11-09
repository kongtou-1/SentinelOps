import { beforeEach, describe, expect, it, vi } from "vitest";
import { reactive, type Ref } from "vue";
import RunTrajectory from "./RunTrajectory.vue";
import type { Run } from "@/features/inspections/ui";
import type { ProgressEvent } from "@/features/inspections/progress";

const mocks = vi.hoisted(() => ({ api: vi.fn(), poll: vi.fn() }));
vi.mock("vue", async (original) => ({
  ...(await original<typeof import("vue")>()),
  useSSRContext: () => ({ modules: new Set() }),
  onUnmounted: vi.fn(),
}));
vi.mock("@/api/client", async (original) => ({
  ...(await original<typeof import("@/api/client")>()),
  api: mocks.api,
}));
vi.mock("@/composables/usePoll", () => ({ usePoll: mocks.poll }));

type State = {
  load: () => Promise<void>;
  loadOlder: () => Promise<void>;
  rows: Ref<ProgressEvent[]>;
  error: Ref<string>;
  cursor: Ref<string | null>;
  following: Ref<boolean>;
  viewport: Ref<HTMLElement | undefined>;
};
function event(id: string, time = 0): ProgressEvent {
  return {
    id,
    occurred_at: new Date(1793750400000 + time * 1000).toISOString(),
    kind: "tool",
    status: "success",
    summary: "",
    data: {},
  };
}
function response(
  events: ProgressEvent[],
  next_cursor: string | null = null,
  status = "evaluating",
) {
  return { events, next_cursor, status, current: null };
}
function setup() {
  const props = reactive({ run: { id: "run-1", status: "evaluating" } as Run });
  const state = (
    RunTrajectory as unknown as { setup: (props: object, ctx: object) => State }
  ).setup(props, { expose: vi.fn() });
  return { state, props };
}
beforeEach(() => vi.resetAllMocks());
describe("实时运行轨迹", () => {
  it("追加新步骤且不重复已有证据，阅读历史时保持位置", async () => {
    const { state } = setup();
    mocks.api.mockResolvedValueOnce(response([event("a")], "older"));
    await state.load();
    state.following.value = false;
    state.viewport.value = { scrollTop: 40, scrollHeight: 600 } as HTMLElement;
    mocks.api.mockResolvedValueOnce(response([event("a"), event("b", 1)], "a"));
    await state.load();
    expect(state.rows.value.map((e) => e.id)).toEqual(["a", "b"]);
    expect(state.viewport.value.scrollTop).toBe(40);
    expect(state.cursor.value).toBe("older");
  });
  it("两次刷新间超过一页时补齐遗漏的步骤", async () => {
    const { state } = setup();
    mocks.api.mockResolvedValueOnce(response([event("a")]));
    await state.load();
    mocks.api.mockResolvedValueOnce(response([event("d", 3)], "gap-2"));
    mocks.api.mockResolvedValueOnce(response([event("c", 2)], "gap-1"));
    mocks.api.mockResolvedValueOnce(response([event("a"), event("b", 1)]));
    await state.load();
    expect(state.rows.value.map((e) => e.id)).toEqual(["a", "b", "c", "d"]);
    expect(mocks.api).toHaveBeenCalledWith(
      "/inspection-runs/run-1/progress?before=gap-1",
    );
  });
  it("按实际时间排列微秒精度和整秒时间戳", async () => {
    const { state } = setup();
    mocks.api.mockResolvedValueOnce(
      response([
        { ...event("a"), occurred_at: "2026-11-04T00:00:00.000002Z" },
        { ...event("b"), occurred_at: "2026-11-04T00:00:00Z" },
        { ...event("c"), occurred_at: "2026-11-04T00:00:00.000001Z" },
      ]),
    );
    await state.load();
    expect(state.rows.value.map((e) => e.id)).toEqual(["b", "c", "a"]);
  });
  it("加载更早步骤时保留已有轨迹和阅读位置", async () => {
    const { state } = setup();
    mocks.api.mockResolvedValueOnce(response([event("b", 1)], "before-b"));
    await state.load();
    state.viewport.value = { scrollTop: 45, scrollHeight: 800 } as HTMLElement;
    mocks.api.mockResolvedValueOnce(response([event("a")]));
    await state.loadOlder();
    expect(state.rows.value.map((e) => e.id)).toEqual(["a", "b"]);
    expect(state.following.value).toBe(false);
    expect(state.viewport.value.scrollTop).toBe(45);
    expect(state.cursor.value).toBeNull();
  });
  it("失败时保留轨迹并显示更新错误，恢复后继续追加", async () => {
    const { state } = setup();
    mocks.api.mockResolvedValueOnce(response([event("a")]));
    await state.load();
    mocks.api.mockRejectedValueOnce(new Error("连接中断"));
    await state.load();
    expect(state.rows.value).toHaveLength(1);
    expect(state.error.value).toContain("连接中断");
    mocks.api.mockResolvedValueOnce(response([event("b", 1)]));
    await state.load();
    expect(state.rows.value).toHaveLength(2);
    expect(state.error.value).toBe("");
  });
  it("读取最终步骤后停止轮询", async () => {
    const { state } = setup();
    mocks.api.mockResolvedValueOnce(
      response([event("final")], null, "cancelled"),
    );
    await state.load();
    expect(state.rows.value[0]?.id).toBe("final");
    expect(mocks.poll.mock.calls[0]![1]()).toBeNull();
  });
  it("切换记录后拒绝迟到的旧轨迹", async () => {
    const { state, props } = setup();
    let finish!: (value: unknown) => void;
    mocks.api.mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const old = state.load();
    mocks.api.mockResolvedValue(response([event("new")]));
    props.run = { id: "run-2", status: "evaluating" } as Run;
    await vi.waitFor(() => expect(state.rows.value[0]?.id).toBe("new"));
    finish(response([event("old")]));
    await old;
    expect(state.rows.value.map((e) => e.id)).toEqual(["new"]);
  });
});
