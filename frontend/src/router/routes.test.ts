import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";
import { routes } from "./routes";
import contract from "./__fixtures__/routes.json";

describe("路由兼容性", () => {
  it("保留原有 URL、权限、元数据、参数与重定向", () => {
    expect(routes.map(({ component, ...route }) => route)).toEqual(contract);
    expect(new Set(routes.map((route) => route.path)).size).toBe(routes.length);
  });

  it("详情、知识检索与市场编辑路径匹配到正确页面", () => {
    const router = createRouter({ history: createMemoryHistory(), routes });
    for (const [url, expected] of [
      ["/servers/server-id", "/servers/:id"],
      ["/incidents/incident-id/diagnosis", "/incidents/:id/diagnosis"],
      ["/knowledge/search", "/knowledge/search"],
      ["/discover/items/new", "/discover/items/new"],
      ["/discover/items/item-id/edit", "/discover/items/:id/edit"],
      ["/applications/application-id", "/applications/:id"],
      ["/unknown/path", "/:pathMatch(.*)*"],
    ]) {
      expect(router.resolve(url!).matched.at(-1)?.path).toBe(expected);
    }
  });
});

describe("路由页面懒加载", () => {
  beforeAll(() => {
    vi.stubGlobal("localStorage", { getItem: () => null, setItem: vi.fn() });
  });
  afterAll(() => vi.unstubAllGlobals());

  it.each(routes.filter((route) => typeof route.component === "function"))(
    "$path 可以加载页面模块",
    async (route) => {
      const load = route.component as () => Promise<{ default: unknown }>;
      expect((await load()).default).toBeDefined();
    },
    15000,
  );
});
