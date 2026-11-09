import { beforeEach, describe, expect, it, vi } from "vitest";
import { reactive, type Ref } from "vue";
import type { Models } from "@/api/client";
import type { Item, Run } from "@/features/inspections/ui";
import ExecutionEvidence from "./ExecutionEvidence.vue";

const mocks = vi.hoisted(() => ({
  request: vi.fn(),
  api: vi.fn(),
  poll: vi.fn(),
}));
vi.mock("vue", async (original) => ({
  ...(await original<typeof import("vue")>()),
  useSSRContext: () => ({ modules: new Set() }),
}));
vi.mock("@/api/client", async (original) => ({
  ...(await original<typeof import("@/api/client")>()),
  request: mocks.request,
  api: mocks.api,
}));
vi.mock("@/composables/usePoll", () => ({ usePoll: mocks.poll }));
type Check = Models["InspectionCheckResultResponse"];
type State = {
  load: () => Promise<void>;
  selectObject: (id: string) => void;
  rows: Ref<{ id: string }[]>;
  selected: Ref<Check | undefined>;
  selectedItem: Ref<Item | undefined>;
};
const object = (id: string) =>
  ({
    id,
    run_id: "run-1",
    plan_id: "plan-1",
    record_version: "1",
    observed_at: "2026-11-02T00:00:00Z",
    created_at: "2026-11-02T00:00:00Z",
    record_id: id,
    evidence: { facts: { name: id } },
    checks: [],
    agent_status: "running",
    verdict: "unassessable",
    rule_verdict: null,
    final_status: "unknown",
    explanation: "检查中",
    agent_result: {},
  }) as Item;
const check = (id: string, kind: Check["kind"] = "agent") =>
  ({
    id: "check-" + id,
    run_id: "run-1",
    plan_id: "plan-1",
    check_id: "quality",
    status: "unknown",
    severity: null,
    summary: "",
    error_code: null,
    started_at: null,
    finished_at: null,
    created_at: "2026-11-02T00:00:00Z",
    item_id: id,
    object_key: id,
    kind,
    scope: "object",
    result: { name: "质量检查" },
    execution_status: "running",
  }) as Check;
function setup(values: Partial<Run> = {}, selectedCheck?: Check) {
  const props = reactive({
    run: {
      id: "run-1",
      status: "evaluating",
      snapshot: { checks: {} },
      summary: {},
      ...values,
    } as Run,
    selectedCheck,
  });
  const state = (
    ExecutionEvidence as unknown as {
      setup: (props: object, ctx: object) => State;
    }
  ).setup(props, { expose: vi.fn() });
  return { props, state };
}
beforeEach(() => {
  vi.resetAllMocks();
  mocks.request.mockImplementation(async (path: string) => {
    const url = new URL(path, "http://test");
    if (url.pathname.endsWith("/items"))
      return { data: [object("a"), object("b")], total: 2 };
    if (url.pathname.endsWith("/check-results"))
      return {
        data: [check(url.searchParams.get("item_id") || "collection")],
        total: 1,
      };
    return {
      data: [{ id: url.searchParams.get("check_result_id") }],
      total: 1,
    };
  });
});
describe("按对象查看检查过程", () => {
  it("默认选择对象，切换后只请求对应检查项的证据", async () => {
    const { state } = setup();
    await state.load();
    expect(state.rows.value[0]?.id).toBe("check-a");
    state.selectObject("b");
    await vi.waitFor(() => expect(state.rows.value[0]?.id).toBe("check-b"));
    expect(mocks.request).toHaveBeenCalledWith(
      expect.stringContaining("item_id=b"),
    );
  });
  it("切换对象后，迟到的旧证据不能覆盖新对象", async () => {
    const normal = mocks.request.getMockImplementation()!;
    let finish!: (value: unknown) => void;
    mocks.request.mockImplementation((path: string) =>
      path.includes("check_result_id=check-a")
        ? new Promise((resolve) => {
            finish = resolve;
          })
        : normal(path),
    );
    const { state } = setup();
    const old = state.load();
    await vi.waitFor(() => expect(finish).toBeDefined());
    state.selectObject("b");
    await vi.waitFor(() => expect(state.rows.value[0]?.id).toBe("check-b"));
    finish({ data: [{ id: "old-a" }], total: 1 });
    await old;
    expect(state.rows.value[0]?.id).toBe("check-b");
  });
  it("从规则结果定位过程时不混入 Agent 工具证据", async () => {
    mocks.request.mockImplementation(async (path: string) => ({
      data: path.includes("/items") ? [object("a")] : [check("a", "rule")],
      total: 1,
    }));
    const { state } = setup({}, check("a", "rule"));
    await state.load();
    expect(state.selected.value?.kind).toBe("rule");
    expect(state.rows.value).toEqual([]);
    expect(
      mocks.request.mock.calls.some(([path]) => path.includes("/traces")),
    ).toBe(false);
  });
  it("运行结束后仍读取一次最终过程并停止周期刷新", async () => {
    const { state, props } = setup();
    await state.load();
    mocks.request.mockClear();
    props.run = { ...props.run, status: "completed" };
    await vi.waitFor(() =>
      expect(mocks.request).toHaveBeenCalledWith(
        expect.stringContaining("/traces"),
      ),
    );
    expect(mocks.poll.mock.calls[0]![1]()).toBeNull();
  });
  it("旧版自主巡检读取对象中的工具记录，并随刷新更新", async () => {
    mocks.api.mockResolvedValue({
      ...object("a"),
      evidence: {
        tool_calls: [
          {
            id: "e1",
            tool: "request_inspection_api",
            success: true,
            observed_at: "2026-11-02T00:00:00Z",
            data: { count: 10 },
          },
        ],
      },
    });
    const { state } = setup({ snapshot: {} });
    await state.load();
    expect(state.rows.value[0]?.id).toBe("e1");
    mocks.api.mockResolvedValue({
      ...object("a"),
      agent_status: "completed",
      evidence: { tool_calls: [] },
    });
    await state.load();
    expect(state.selectedItem.value?.agent_status).toBe("completed");
    expect(state.rows.value).toEqual([]);
    expect(
      mocks.request.mock.calls.some(([path]) => path.includes("/traces")),
    ).toBe(false);
  });
});
