import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { invoke } = vi.hoisted(() => ({ invoke: vi.fn() }));
vi.mock("@/platform/native", () => ({ invoke }));
beforeEach(() => {
  vi.resetModules();
  invoke.mockReset();
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("桌面服务器与流式请求", () => {
  it("规范化根地址并拒绝不支持的协议、凭据和路径", async () => {
    const { normalizeServer } = await import("./desktop");
    expect(normalizeServer(" https://OPS.example.com:443/ ")).toBe(
      "https://ops.example.com",
    );
    expect(normalizeServer("http://192.168.1.5:18088")).toBe(
      "http://192.168.1.5:18088",
    );
    for (const value of [
      "file:///a",
      "https://u:p@host",
      "http://host/api",
      "http://host?x=1",
      "http://host#x",
      "localhost:8000",
    ]) {
      expect(() => normalizeServer(value)).toThrow();
    }
  });

  it("流式响应先返回第一块，读取结束后关闭", async () => {
    const { desktopFetch } = await import("./desktop");
    let finish: (v: number[] | null) => void = () => {};
    let reads = 0;
    invoke.mockImplementation(async (command) => {
      if (command === "request_send")
        return {
          status: 200,
          headers: [["content-type", "text/event-stream"]],
        };
      if (command === "request_read") {
        reads++;
        if (reads === 1) return Array.from(new TextEncoder().encode("第一段"));
        return new Promise((resolve) => {
          finish = resolve;
        });
      }
    });
    const response = await desktopFetch("/api/v1/test");
    const reader = response.body!.getReader();
    const chunk = await reader.read();
    expect(new TextDecoder().decode(chunk.value)).toBe("第一段");
    const end = reader.read();
    await vi.waitFor(() => expect(reads).toBe(2));
    finish(null);
    expect((await end).done).toBe(true);
    expect(invoke).toHaveBeenCalledWith(
      "request_open",
      expect.objectContaining({ epoch: 0, path: "/api/v1/test" }),
    );
  });

  it("等待响应头时可立即取消请求", async () => {
    const { desktopFetch } = await import("./desktop");
    invoke.mockImplementation((command) =>
      command === "request_send" ? new Promise(() => {}) : Promise.resolve(),
    );
    const controller = new AbortController();
    const request = desktopFetch("/api/v1/test", { signal: controller.signal });
    const rejected = expect(request).rejects.toMatchObject({
      name: "AbortError",
    });
    await vi.waitFor(() =>
      expect(invoke).toHaveBeenCalledWith("request_send", expect.anything()),
    );
    controller.abort();
    await rejected;
    expect(invoke).toHaveBeenCalledWith("request_cancel", expect.anything());
  });

  it("切换后台后拒绝旧流的晚到数据", async () => {
    const { desktopFetch, server } = await import("./desktop");
    let finish: (v: number[]) => void = () => {};
    invoke.mockImplementation(async (command) => {
      if (command === "request_send") return { status: 200, headers: [] };
      if (command === "request_read")
        return new Promise((resolve) => {
          finish = resolve;
        });
    });
    const response = await desktopFetch("/api/v1/test");
    const body = response.text();
    const rejected = expect(body).rejects.toMatchObject({ name: "AbortError" });
    await vi.waitFor(() =>
      expect(invoke).toHaveBeenCalledWith("request_read", expect.anything()),
    );
    server.value = { url: "https://new.example.com", epoch: 1 };
    finish([65]);
    await rejected;
    expect(invoke).toHaveBeenCalledWith("request_cancel", expect.anything());
  });

  it("204 响应不创建流，文件保存交给原生选择器", async () => {
    vi.stubEnv("MODE", "desktop");
    const { desktopFetch, saveBlob } = await import("./desktop");
    invoke.mockImplementation(async (command) => {
      if (command === "request_send") return { status: 204, headers: [] };
      if (command === "save_file") return false;
    });
    expect((await desktopFetch("/api/v1/test")).body).toBeNull();
    expect(await saveBlob(new Blob(["export"]), "证据.csv")).toBe(false);
    expect(invoke).toHaveBeenCalledWith("save_file", {
      name: "证据.csv",
      data: Array.from(new TextEncoder().encode("export")),
    });
  });
});
