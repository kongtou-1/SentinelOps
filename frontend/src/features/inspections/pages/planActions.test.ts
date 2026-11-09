import { beforeEach, describe, expect, it, vi } from "vitest";
import { unref } from "vue";
import PlanList from "./PlanList.vue";
import PlanDetail from "./PlanDetail.vue";
import type { Plan } from "@/features/inspections/ui";

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  request: vi.fn(),
  confirm: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  push: vi.fn(),
}));
vi.mock("vue", async (importOriginal) => ({
  ...(await importOriginal<typeof import("vue")>()),
  useSSRContext: () => ({ modules: new Set() }),
}));
vi.mock("element-plus", () => ({
  ElMessageBox: { confirm: mocks.confirm },
  ElMessage: { success: mocks.success, error: mocks.error },
}));
vi.mock("@/api/client", () => ({
  api: mocks.api,
  request: mocks.request,
  query: () => "",
}));
vi.mock("@/features/auth/store", () => ({
  useAuth: () => ({ can: () => true }),
}));
vi.mock("vue-router", () => ({
  useRoute: () => ({ params: { planId: "plan-1" }, query: {} }),
  useRouter: () => ({ push: mocks.push }),
}));
vi.mock("@/composables/usePoll", () => ({ usePoll: vi.fn() }));
vi.mock("@/features/inspections/components/PlanEditor.vue", () => ({
  default: {},
}));

type Actions = {
  plan: { value: Plan };
  busy: unknown;
  pending: unknown;
  state: (row?: Plan) => Promise<void>;
  run?: (row: Plan) => Promise<void>;
  start?: () => Promise<void>;
};
function setup(component: unknown): Actions {
  return (
    component as { setup: (props: object, ctx: object) => Actions }
  ).setup({}, { expose: vi.fn() });
}
function plan(enabled = true) {
  return { id: "plan-1", name: "订单巡检", enabled, version: 3 } as Plan;
}

beforeEach(() => {
  vi.resetAllMocks();
  mocks.api.mockImplementation(async (path: string, method?: string) => {
    if (method === "POST") return { id: "run-1" };
    if (path === "/inspection-plans/plan-1" && !method) return plan();
    return [];
  });
  mocks.request.mockResolvedValue({ data: [], total: 0 });
});

describe.each([
  ["计划列表", PlanList],
  ["计划详情", PlanDetail],
])("%s 的关键操作", (_name, component) => {
  function actions(row: Plan) {
    const value = setup(component);
    if (value.plan) value.plan.value = row;
    return value;
  }
  for (const enabled of [true, false]) {
    const label = enabled ? "暂停" : "启用";
    it.each(["cancel", "close"])(
      `${label}确认被 %s 后不发送请求`,
      async (reason) => {
        const row = plan(enabled),
          value = actions(row);
        mocks.confirm.mockRejectedValue(reason);
        await value.state(row);
        expect(mocks.api).not.toHaveBeenCalled();
        expect(mocks.success).not.toHaveBeenCalled();
        expect(mocks.error).not.toHaveBeenCalled();
        expect(unref(value.busy)).toBeFalsy();
      },
    );
    it(`${label}确认前不提交，并保持确认时的状态和版本`, async () => {
      const row = plan(enabled),
        value = actions(row);
      let confirm!: () => void;
      mocks.confirm.mockReturnValue(
        new Promise<void>((resolve) => {
          confirm = resolve;
        }),
      );
      const work = value.state(row);
      expect(mocks.api).not.toHaveBeenCalled();
      expect(mocks.confirm).toHaveBeenCalledWith(
        expect.stringContaining("订单巡检"),
        `${label}任务`,
        expect.objectContaining({ cancelButtonText: "取消" }),
      );
      if (enabled)
        expect(mocks.confirm.mock.calls[0]![0]).toContain("进行中的巡检会继续");
      row.enabled = !enabled;
      row.version = 4;
      confirm();
      await work;
      expect(mocks.api).toHaveBeenCalledWith(
        "/inspection-plans/plan-1/state",
        "PUT",
        {
          enabled: !enabled,
          expected_version: 3,
        },
      );
      expect(mocks.success).toHaveBeenCalledWith(
        expect.stringContaining(`已${label}`),
      );
      expect(unref(value.busy)).toBeFalsy();
    });
  }
  it("取消立即执行不创建巡检、不跳转", async () => {
    const row = plan(),
      value = actions(row);
    mocks.confirm.mockRejectedValue("cancel");
    await (value.run ? value.run(row) : value.start!());
    expect(mocks.api).not.toHaveBeenCalled();
    expect(mocks.push).not.toHaveBeenCalled();
    expect(mocks.error).not.toHaveBeenCalled();
  });
  it("暂停的任务确认立即执行后只提交一次，并跳转到执行记录", async () => {
    const row = plan(false),
      value = actions(row);
    let confirm!: () => void;
    mocks.confirm.mockReturnValue(
      new Promise<void>((resolve) => {
        confirm = resolve;
      }),
    );
    const execute = () => (value.run ? value.run(row) : value.start!());
    const work = execute();
    await execute();
    expect(mocks.confirm).toHaveBeenCalledTimes(1);
    expect(mocks.api).not.toHaveBeenCalled();
    confirm();
    await work;
    expect(mocks.api).toHaveBeenCalledExactlyOnceWith(
      "/inspection-plans/plan-1/runs",
      "POST",
    );
    expect(mocks.success).toHaveBeenCalled();
    expect(mocks.push).toHaveBeenCalledWith("/inspections/runs/run-1");
    expect(unref(value.busy)).toBeFalsy();
  });
  it("提交失败显示错误且恢复按钮，不显示成功", async () => {
    const row = plan(),
      value = actions(row);
    mocks.confirm.mockResolvedValue("confirm");
    mocks.api.mockRejectedValue(new Error("计划版本已变化"));
    await value.state(row);
    expect(mocks.error).toHaveBeenCalledWith("计划版本已变化");
    expect(mocks.success).not.toHaveBeenCalled();
    expect(unref(value.busy)).toBeFalsy();
    expect(unref(value.pending)).toBeFalsy();
  });
});
