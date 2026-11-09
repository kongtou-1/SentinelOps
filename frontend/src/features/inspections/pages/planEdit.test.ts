import { beforeEach, describe, expect, it, vi } from "vitest";
import { ref } from "vue";
import PlanEdit from "./PlanEdit.vue";
import {
  newCheck,
  taskDefaults,
  type TaskInput,
} from "@/features/inspections/task";
const mocks = vi.hoisted(() => ({ api: vi.fn(), push: vi.fn() }));
vi.mock("vue", async (original) => ({
  ...(await original<typeof import("vue")>()),
  onMounted: vi.fn(),
  useSSRContext: () => ({ modules: new Set() }),
}));
vi.mock("vue-router", () => ({
  useRoute: () => ({ params: {}, query: {} }),
  useRouter: () => ({ push: mocks.push }),
}));
vi.mock("@/api/client", () => ({ api: mocks.api, all: vi.fn() }));
vi.mock("@/features/auth/store", () => ({
  useAuth: () => ({ can: () => true }),
}));
vi.mock("element-plus", () => ({ ElMessage: { success: vi.fn() } }));
type Editor = {
  form: ReturnType<typeof ref<TaskInput>>;
  objects: { value: { id: string; facts: string }[] };
  checks: { value: { validate: () => void } };
  trialId: { value: string };
  error: { value: string };
  trial: () => Promise<void>;
};
function editor() {
  const value = (
    PlanEdit as unknown as { setup: (props: object, ctx: object) => Editor }
  ).setup({}, { expose: vi.fn() });
  value.form.value = {
    ...taskDefaults(),
    name: "当前表单",
    object_mode: "fixed",
    check_items: [newCheck("rule")],
  };
  value.objects.value = [{ id: "project-2", facts: '{"amount": 27}' }];
  value.checks.value = { validate: vi.fn() };
  return value;
}
beforeEach(() => {
  vi.resetAllMocks();
  mocks.api.mockResolvedValue({ id: "trial-1" });
});
describe("未保存任务试运行", () => {
  it("提交当前表单且保留编辑页，不创建正式任务", async () => {
    const value = editor();
    await value.trial();
    expect(mocks.api).toHaveBeenCalledExactlyOnceWith(
      "/inspection-trials",
      "POST",
      expect.objectContaining({
        plan: expect.objectContaining({
          name: "当前表单",
          fixed_objects: [{ id: "project-2", facts: { amount: 27 } }],
        }),
        sample_size: 3,
      }),
    );
    expect(value.trialId.value).toBe("trial-1");
    expect(mocks.push).not.toHaveBeenCalled();
  });
  it("错误输入阻止查询与模型调用，并说明对象位置", async () => {
    const value = editor();
    value.objects.value[0]!.facts = "invalid-json";
    await value.trial();
    expect(mocks.api).not.toHaveBeenCalled();
    expect(value.error.value).toContain("project-2");
  });
});
