import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";
import { registerNavigationGuards } from "./guards";
import contract from "./__fixtures__/routes.json";

const { auth, tokens } = vi.hoisted(() => ({
  auth: {
    user: null as { id: string } | null,
    can: vi.fn(),
    load: vi.fn(),
    clear: vi.fn(),
  },
  tokens: vi.fn(),
}));
vi.mock("@/features/auth/store", () => ({ useAuth: () => auth }));
vi.mock("@/api/client", () => ({ tokens }));

function testRouter() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: contract.map(({ redirect, ...route }) =>
      redirect
        ? { path: route.path, redirect }
        : { ...route, component: { render: () => null } },
    ),
  });
  registerNavigationGuards(router);
  return router;
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal("sessionStorage", { removeItem: vi.fn() });
  auth.user = { id: "test-user" };
  auth.can.mockReturnValue(true);
  tokens.mockReturnValue({ access_token: "test-token" });
});

afterEach(() => vi.unstubAllGlobals());

describe("导航认证与权限", () => {
  it("登录页面不要求会话", async () => {
    tokens.mockReturnValue(null);
    const router = testRouter();
    await router.push("/login");
    expect(router.currentRoute.value.path).toBe("/login");
    expect(auth.load).not.toHaveBeenCalled();
  });

  it("未登录返回登录页并保留详情路径、查询与锚点", async () => {
    tokens.mockReturnValue(null);
    const router = testRouter();
    const target = "/reports/report-id?tab=history#evidence";
    await router.push(target);
    expect(router.currentRoute.value.path).toBe("/login");
    expect(router.currentRoute.value.query.redirect).toBe(target);
  });

  it("首次导航加载用户与权限", async () => {
    auth.user = null;
    const router = testRouter();
    await router.push("/servers");
    expect(auth.load).toHaveBeenCalledOnce();
    expect(auth.can).toHaveBeenCalledWith("servers:read");
    expect(router.currentRoute.value.path).toBe("/servers");
  });

  it("用户加载失败清理会话并保留跳转目标", async () => {
    auth.user = null;
    auth.load.mockRejectedValueOnce(new Error("expired"));
    const router = testRouter();
    await router.push("/incidents/incident-id/diagnosis");
    expect(auth.clear).toHaveBeenCalledOnce();
    expect(router.currentRoute.value.path).toBe("/login");
    expect(router.currentRoute.value.query.redirect).toBe(
      "/incidents/incident-id/diagnosis",
    );
  });

  it.each([
    ["/roles", "roles:read"],
    ["/discover/manage", "market:review"],
    ["/incidents/incident-id/diagnosis", "analyses:run"],
    ["/approvals/approval-id", "executions:read"],
  ])("拒绝没有权限的 %s", async (url, permission) => {
    auth.can.mockReturnValue(false);
    const router = testRouter();
    await router.push(url!);
    expect(auth.can).toHaveBeenCalledWith(permission);
    expect(router.currentRoute.value.path).toBe("/forbidden");
  });
});
