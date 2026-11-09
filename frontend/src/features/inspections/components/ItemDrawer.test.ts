import { beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick, reactive, type Ref } from "vue";
import type { Item } from "@/features/inspections/ui";
import ItemDrawer from "./ItemDrawer.vue";

const mocks = vi.hoisted(() => ({ api: vi.fn(), all: vi.fn(), poll: vi.fn() }));
vi.mock("vue", async (original) => {
  const vue = await original<typeof import("vue")>();
  return {
    ...vue,
    useSSRContext: () => ({ modules: new Set() }),
    useModel: (props: object, key: string) => vue.toRef(props, key as never),
  };
});
vi.mock("@/api/client", () => ({ api: mocks.api, all: mocks.all }));
vi.mock("@/features/auth/store", () => ({
  useAuth: () => ({ can: () => false }),
}));
vi.mock("@/composables/usePoll", () => ({ usePoll: mocks.poll }));
type State = {
  load: () => Promise<void>;
  item: Ref<Item | undefined>;
  tab: Ref<string>;
  comment: Ref<string>;
};
function setup() {
  const props = reactive({ modelValue: false, itemId: "a", active: true });
  const state = (
    ItemDrawer as unknown as { setup: (props: object, ctx: object) => State }
  ).setup(props, { expose: vi.fn(), emit: vi.fn() });
  return { props, state };
}
beforeEach(() => {
  vi.resetAllMocks();
  mocks.all.mockResolvedValue([]);
  mocks.api.mockResolvedValue({ id: "a", agent_status: "running" });
});
describe("对象详情的持续刷新", () => {
  it("运行时刷新，结束后取最终状态并保留用户所在标签和复核草稿", async () => {
    const { props, state } = setup();
    props.modelValue = true;
    await vi.waitFor(() =>
      expect(state.item.value?.agent_status).toBe("running"),
    );
    state.tab.value = "review";
    state.comment.value = "待核对原始资料";
    mocks.api.mockResolvedValue({ id: "a", agent_status: "completed" });
    props.active = false;
    await vi.waitFor(() =>
      expect(state.item.value?.agent_status).toBe("completed"),
    );
    expect(state.tab.value).toBe("review");
    expect(state.comment.value).toBe("待核对原始资料");
    expect(mocks.poll.mock.calls[0]![1]()).toBeNull();
    props.modelValue = false;
    await nextTick();
    mocks.api.mockClear();
    await state.load();
    expect(mocks.api).not.toHaveBeenCalled();
  });
  it("切换对象后不接收旧对象的迟到响应", async () => {
    const { props, state } = setup();
    props.modelValue = true;
    await vi.waitFor(() => expect(state.item.value?.id).toBe("a"));
    let finish!: (item: unknown) => void;
    mocks.api.mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const old = state.load();
    mocks.api.mockResolvedValue({ id: "b", agent_status: "completed" });
    props.itemId = "b";
    await vi.waitFor(() => expect(state.item.value?.id).toBe("b"));
    finish({ id: "a", agent_status: "running" });
    await old;
    expect(state.item.value?.id).toBe("b");
  });
});
