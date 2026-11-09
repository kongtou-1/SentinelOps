import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { desktopFetch } = vi.hoisted(() => ({ desktopFetch: vi.fn() }));
vi.mock("@/platform/desktop", () => ({
  isDesktop: true,
  desktopFetch,
  saveBlob: vi.fn(),
}));
beforeEach(() => {
  vi.resetModules();
  desktopFetch.mockReset();
  const data = new Map<string, string>();
  vi.stubGlobal("sessionStorage", {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => data.set(k, v),
    removeItem: (k: string) => data.delete(k),
  });
  vi.stubGlobal("window", new EventTarget());
});
afterEach(() => vi.unstubAllGlobals());

describe("桌面认证", () => {
  const tokens = {
    access_token: "old",
    refresh_token: "refresh",
    token_type: "bearer",
    expires_in: 1800,
  };
  it("原生通道同时处理业务请求和令牌刷新", async () => {
    const client = await import("./client");
    client.saveTokens(tokens);
    desktopFetch.mockImplementation(async (path, init) => {
      if (path.endsWith("/refresh"))
        return Response.json({ ...tokens, access_token: "new" });
      return new Headers(init.headers).get("Authorization") === "Bearer new"
        ? Response.json({ id: "me" })
        : Response.json({}, { status: 401 });
    });
    expect(await client.api("/me")).toEqual({ id: "me" });
    expect(desktopFetch).toHaveBeenCalledWith(
      "/api/v1/auth/refresh",
      expect.objectContaining({ method: "POST" }),
    );
  });
  it("刷新遇到网络中断时保留会话且不重放业务写请求", async () => {
    const client = await import("./client");
    client.saveTokens(tokens);
    desktopFetch.mockImplementation(async (path) => {
      if (path.endsWith("/refresh")) throw new Error("服务器连接中断");
      return Response.json({}, { status: 401 });
    });
    await expect(
      client.api("/approvals/id/execute", "POST", {}),
    ).rejects.toThrow("服务器连接中断");
    expect(client.tokens()).toEqual(tokens);
    expect(
      desktopFetch.mock.calls.filter(([path]) => path.endsWith("/execute")),
    ).toHaveLength(1);
  });
});
