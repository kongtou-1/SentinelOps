import { beforeEach, describe, expect, it, vi } from "vitest";
import { createSSRApp } from "vue";
import { renderToString } from "vue/server-renderer";
import { ElAlert, ElTooltip } from "element-plus";
import LLMStatusOverview from "./LLMStatusOverview.vue";

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  loads: [] as (() => Promise<void>)[],
}));
vi.mock("@/api/client", () => ({ api: mocks.api }));
vi.mock("@/composables/usePoll", () => ({
  usePoll: (load: () => Promise<void>) => {
    mocks.loads.push(load);
    return { refresh: load };
  },
}));

function render(resources: Record<string, string>[]) {
  const app = createSSRApp(LLMStatusOverview, { resources });
  app.component("el-alert", ElAlert);
  app.component("el-tooltip", ElTooltip);
  app.directive("loading", { getSSRProps: () => ({}) });
  return renderToString(app);
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.loads.length = 0;
});

describe("多 LLM 服务状态", () => {
  it("同时显示各服务的名称、模型和地址，分别查询对应端点", async () => {
    const html = await render([
      {
        id: "provider-a",
        name: "主通道",
        model: "shared-model",
        base_url: "https://a.example/v1",
      },
      {
        id: "provider-b",
        name: "备用通道",
        model: "shared-model",
        base_url: "https://b.example/v1",
      },
    ]);
    expect(html).toContain("主通道 LLM 状态");
    expect(html).toContain("备用通道 LLM 状态");
    expect(html.match(/shared-model/g)).toHaveLength(2);
    expect(html).toContain("https://a.example/v1");
    expect(html).toContain("https://b.example/v1");
    expect(html.match(/近 30 分钟探测历史/g)).toHaveLength(2);
    expect(html).not.toContain("el-select");
    mocks.api.mockResolvedValue({
      request_count: 0,
      success_count: 0,
      buckets: [],
    });
    await Promise.all(mocks.loads.map((load) => load()));
    expect(mocks.api.mock.calls.map(([url]) => url)).toEqual([
      "/llm-endpoints/provider-a/status",
      "/llm-endpoints/provider-b/status",
    ]);
  });

  it("没有服务时显示空状态，不生成无所属端点的统计卡片或请求", async () => {
    const html = await render([]);
    expect(html).toContain("暂无可展示的 LLM 服务");
    expect(html).not.toContain("近 30 分钟探测历史");
    expect(mocks.loads).toHaveLength(0);
    expect(mocks.api).not.toHaveBeenCalled();
  });
});
