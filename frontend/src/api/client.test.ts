import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";

const old = {
  access_token: "old",
  refresh_token: "refresh-old",
  token_type: "bearer",
  expires_in: 1800,
};
const next = { ...old, access_token: "new", refresh_token: "refresh-new" };
beforeEach(() => {
  vi.resetModules();
  const data = new Map<string, string>();
  vi.stubGlobal("sessionStorage", {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => data.set(k, v),
    removeItem: (k: string) => data.delete(k),
  });
  vi.stubGlobal("window", new EventTarget());
});
afterEach(() => vi.unstubAllGlobals());
describe("认证与请求", () => {
  it("完整导出读取全部分页，并在每一页保留筛选和认证", async () => {
    const client = await import("@/api/client");
    client.saveTokens(old);
    const fetcher = vi.fn(async (path: string, init: RequestInit) => {
      expect(path).toContain("status=failed");
      expect(path).toContain("start=2026-10-01");
      expect(new Headers(init.headers).get("Authorization")).toBe("Bearer old");
      const offset = Number(
        new URL(path, "http://local").searchParams.get("offset"),
      );
      return Response.json(
        Array.from({ length: offset === 0 ? 100 : 3 }, (_, i) => ({
          id: offset + i,
        })),
        {
          headers: { "X-Total-Count": "103" },
        },
      );
    });
    vi.stubGlobal("fetch", fetcher);
    const rows = await client.all<{ id: number }>(
      "/executions?status=failed&start=2026-10-01",
    );
    expect(rows).toHaveLength(103);
    expect(rows[102]!.id).toBe(102);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it("后续分页失败时拒绝整个导出，不返回部分记录", async () => {
    const client = await import("@/api/client");
    client.saveTokens(old);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) =>
        path.includes("offset=0")
          ? Response.json(Array.from({ length: 100 }, (_, id) => ({ id })))
          : Response.json({ detail: "export page failed" }, { status: 500 }),
      ),
    );
    await expect(client.all("/alerts?status=firing")).rejects.toMatchObject({
      status: 500,
    });
  });
  it("并发401只轮换一次，所有请求使用新令牌重试", async () => {
    const client = await import("@/api/client");
    client.saveTokens(old);
    let refreshes = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string, init: RequestInit) => {
        if (path.endsWith("/refresh")) {
          refreshes++;
          await new Promise((r) => setTimeout(r, 10));
          return Response.json(next);
        }
        return new Headers(init.headers).get("Authorization") === "Bearer new"
          ? Response.json({ ok: true })
          : Response.json({ detail: "unauthorized" }, { status: 401 });
      }),
    );
    const results = await Promise.all([
      client.api("/servers"),
      client.api("/incidents"),
      client.api("/approvals"),
    ]);
    expect(refreshes).toBe(1);
    expect(results).toEqual([{ ok: true }, { ok: true }, { ok: true }]);
    expect(client.tokens()?.refresh_token).toBe("refresh-new");
  });
  it("晚到的401复用已轮换令牌，不再次消费旧刷新令牌", async () => {
    const client = await import("@/api/client");
    client.saveTokens(old);
    let refreshes = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string, init: RequestInit) => {
        if (path.endsWith("/refresh")) {
          refreshes++;
          return Response.json(next);
        }
        if (new Headers(init.headers).get("Authorization") === "Bearer new")
          return Response.json({ ok: true });
        if (path.endsWith("/late")) await new Promise((r) => setTimeout(r, 30));
        return Response.json({}, { status: 401 });
      }),
    );
    await Promise.all([client.api("/early"), client.api("/late")]);
    expect(refreshes).toBe(1);
  });
  it("刷新失败清除会话并发出过期事件", async () => {
    const client = await import("@/api/client");
    client.saveTokens(old);
    const expired = vi.fn();
    window.addEventListener("session-expired", expired);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({}, { status: 401 })),
    );
    await expect(client.api("/me")).rejects.toMatchObject({ status: 401 });
    expect(client.tokens()).toBeNull();
    expect(expired).toHaveBeenCalledOnce();
  });
  it("下载在刷新后仍被拒绝时，清理会话并返回登录", async () => {
    const client = await import("@/api/client");
    client.saveTokens(old);
    const expired = vi.fn();
    window.addEventListener("session-expired", expired);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) =>
        path.endsWith("/refresh")
          ? Response.json(next)
          : Response.json({}, { status: 401 }),
      ),
    );
    await expect(
      client.download("/reports/id/export?format=pdf"),
    ).rejects.toMatchObject({ status: 401 });
    expect(client.tokens()).toBeNull();
    expect(expired).toHaveBeenCalledOnce();
  });
  it("退出期间完成的刷新不能恢复旧会话", async () => {
    const client = await import("@/api/client");
    client.saveTokens(old);
    let finish: (v: Response) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) =>
        path.endsWith("/refresh")
          ? await new Promise<Response>((r) => {
              finish = r;
            })
          : Response.json({}, { status: 401 }),
      ),
    );
    const result = client.api("/me");
    const failed = expect(result).rejects.toMatchObject({ status: 401 });
    await new Promise((r) => setTimeout(r, 10));
    client.saveTokens(null);
    finish(Response.json(next));
    await failed;
    expect(client.tokens()).toBeNull();
  });
  it("状态与版本冲突保留会话，写请求不自动重试", async () => {
    const client = await import("@/api/client");
    client.saveTokens(old);
    const fetch = vi.fn(async () =>
      Response.json({ detail: "Revision conflict" }, { status: 409 }),
    );
    vi.stubGlobal("fetch", fetch);
    await expect(
      client.api("/reports/id", "PUT", { expected_revision: 1 }),
    ).rejects.toMatchObject({ status: 409, message: "Revision conflict" });
    expect(fetch).toHaveBeenCalledOnce();
    expect(client.tokens()?.access_token).toBe("old");
  });
  it("网络异常不重放执行或邮件请求", async () => {
    const client = await import("@/api/client");
    client.saveTokens(old);
    const fetch = vi.fn(async () => {
      throw new TypeError("Network failed");
    });
    vi.stubGlobal("fetch", fetch);
    await expect(
      client.api("/approvals/id/execute", "POST", {}),
    ).rejects.toThrow("Network failed");
    expect(fetch).toHaveBeenCalledOnce();
  });
  it("保持数组响应并读取过滤后的分页总数", async () => {
    const client = await import("@/api/client");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json([{ id: "1" }], { headers: { "X-Total-Count": "23" } }),
      ),
    );
    const r = await client.request("/servers?offset=20&limit=20");
    expect(r.data).toEqual([{ id: "1" }]);
    expect(r.total).toBe(23);
  });
});
