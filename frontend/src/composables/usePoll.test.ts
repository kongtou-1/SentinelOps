import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
const hooks = vi.hoisted(() => ({
  mounted: [] as (() => void)[],
  unmounted: [] as (() => void)[],
}));
vi.mock("vue", async (importOriginal) => ({
  ...(await importOriginal<typeof import("vue")>()),
  onMounted: (f: () => void) => hooks.mounted.push(f),
  onUnmounted: (f: () => void) => hooks.unmounted.push(f),
}));
import { ref } from "vue";
import { usePoll } from "@/composables/usePoll";
describe("异步查询的生命周期", () => {
  let visibility: EventTarget;
  let doc: {
    hidden: boolean;
    addEventListener: EventTarget["addEventListener"];
    removeEventListener: EventTarget["removeEventListener"];
  };
  beforeEach(() => {
    vi.useFakeTimers();
    hooks.mounted.length = hooks.unmounted.length = 0;
    visibility = new EventTarget();
    doc = {
      hidden: false,
      addEventListener: visibility.addEventListener.bind(visibility),
      removeEventListener: visibility.removeEventListener.bind(visibility),
    };
    vi.stubGlobal("document", doc);
  });
  afterEach(() => {
    hooks.unmounted.forEach((f) => f());
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });
  it("按实际任务状态退避，终态停止", async () => {
    let active = true;
    const load = vi.fn(async () => {});
    usePoll(load, () =>
      active ? Math.min(10000, 2000 * load.mock.calls.length) : null,
    );
    hooks.mounted[0]!();
    await vi.advanceTimersByTimeAsync(2000);
    expect(load).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(3999);
    expect(load).toHaveBeenCalledTimes(2);
    active = false;
    await vi.advanceTimersByTimeAsync(1);
    expect(load).toHaveBeenCalledTimes(3);
    await vi.advanceTimersByTimeAsync(30000);
    expect(load).toHaveBeenCalledTimes(3);
  });
  it("隐藏时停止，恢复可见后查询，离开后移除监听", async () => {
    const load = vi.fn(async () => {});
    usePoll(load, () => 2000);
    hooks.mounted[0]!();
    await vi.advanceTimersByTimeAsync(0);
    doc.hidden = true;
    visibility.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(10000);
    expect(load).toHaveBeenCalledTimes(1);
    doc.hidden = false;
    visibility.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(0);
    expect(load).toHaveBeenCalledTimes(2);
    hooks.unmounted[0]!();
    visibility.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(10000);
    expect(load).toHaveBeenCalledTimes(2);
  });
  it("查询期间的筛选变更只排队一次刷新，不丢失也不并发", async () => {
    let resolve!: () => void;
    const load = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise<void>((r) => {
            resolve = r;
          }),
      )
      .mockResolvedValue(undefined);
    const { refresh } = usePoll(load, () => 30000);
    hooks.mounted[0]!();
    await refresh();
    await refresh();
    expect(load).toHaveBeenCalledTimes(1);
    resolve();
    await vi.advanceTimersByTimeAsync(0);
    expect(load).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(30000);
    expect(load).toHaveBeenCalledTimes(3);
  });
  it("编辑时取消已安排的查询，关闭弹窗后恢复自动刷新", async () => {
    const editing = ref(false);
    const load = vi.fn(async () => {});
    usePoll(load, () => (editing.value ? null : 2000));
    hooks.mounted[0]!();
    await vi.advanceTimersByTimeAsync(0);
    editing.value = true;
    await vi.advanceTimersByTimeAsync(10000);
    expect(load).toHaveBeenCalledTimes(1);
    editing.value = false;
    await vi.advanceTimersByTimeAsync(2000);
    expect(load).toHaveBeenCalledTimes(2);
    hooks.unmounted[0]!();
    editing.value = true;
    editing.value = false;
    await vi.advanceTimersByTimeAsync(10000);
    expect(load).toHaveBeenCalledTimes(2);
  });
  it("挂载后才读取调度条件，允许页面在 setup 后段初始化状态", async () => {
    let ready = false;
    const interval = vi.fn(() => {
      if (!ready) throw new ReferenceError("页面状态尚未初始化");
      return 2000;
    });
    const load = vi.fn(async () => {});
    usePoll(load, interval);
    expect(interval).not.toHaveBeenCalled();
    ready = true;
    hooks.mounted[0]!();
    await vi.advanceTimersByTimeAsync(2000);
    expect(load).toHaveBeenCalledTimes(2);
  });
});
