import { ref } from "vue";
import { invoke } from "@/platform/native";

export const isDesktop = import.meta.env.MODE === "desktop";
export const server = ref({ url: "", epoch: 0 });
export const connectionError = ref("");
type Server = typeof server.value;

export function normalizeServer(value: string) {
  let url: URL;
  try {
    url = new URL(value.trim());
  } catch {
    throw new Error("请输入完整的 HTTP 或 HTTPS 服务器地址");
  }
  if (
    !["http:", "https:"].includes(url.protocol) ||
    !url.hostname ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    url.pathname !== "/"
  )
    throw new Error("服务器地址只包含协议、域名或 IP 和端口");
  return url.origin;
}

export async function initializeDesktop() {
  server.value = await invoke<Server>("load_server");
}

export async function testServer(url: string) {
  return invoke<string>("test_server", { url: normalizeServer(url) });
}

export async function saveServer(url: string) {
  server.value = await invoke<Server>("save_server", {
    url: normalizeServer(url),
  });
}

function aborted() {
  return new DOMException("请求已取消", "AbortError");
}

// Pull each body chunk over IPC, preserving streaming and backpressure.
export async function desktopFetch(path: string, init: RequestInit = {}) {
  const epoch = server.value.epoch;
  const id = crypto.randomUUID();
  const signal = init.signal;
  if (signal?.aborted) throw aborted();
  const check = () => {
    if (signal?.aborted || epoch !== server.value.epoch) throw aborted();
  };
  const cancel = () => {
    void invoke("request_cancel", { id }).catch(() => {});
  };
  let body: number[] | null = null;
  if (init.body != null) {
    if (typeof init.body !== "string") throw new Error("不支持的桌面请求内容");
    body = Array.from(new TextEncoder().encode(init.body));
  }
  const headers = Array.from(new Headers(init.headers).entries());
  await invoke("request_open", {
    id,
    epoch,
    path,
    method: init.method || "GET",
    headers,
    body,
  });
  let rejectAbort: (reason: unknown) => void = () => {};
  const interrupted = new Promise<never>((_, reject) => {
    rejectAbort = reject;
  });
  // Keep a rejection handler even when the response has already been returned.
  void interrupted.catch(() => {});
  const onAbort = () => {
    cancel();
    rejectAbort(aborted());
  };
  signal?.addEventListener("abort", onAbort, { once: true });
  const cleanup = () => signal?.removeEventListener("abort", onAbort);
  try {
    check();
    const head = await Promise.race([
      invoke<{ status: number; headers: [string, string][] }>("request_send", {
        id,
      }),
      interrupted,
    ]);
    check();
    if ([204, 205, 304].includes(head.status) || init.method === "HEAD") {
      cancel();
      cleanup();
      return new Response(null, { status: head.status, headers: head.headers });
    }
    const stream = new ReadableStream<Uint8Array>({
      async pull(controller) {
        try {
          check();
          const bytes = await Promise.race([
            invoke<number[] | null>("request_read", { id }),
            interrupted,
          ]);
          check();
          if (bytes === null) {
            cleanup();
            controller.close();
          } else controller.enqueue(new Uint8Array(bytes));
        } catch (error) {
          cancel();
          cleanup();
          controller.error(
            error instanceof Error ? error : new Error(String(error)),
          );
        }
      },
      cancel() {
        cancel();
        cleanup();
      },
    });
    return new Response(stream, { status: head.status, headers: head.headers });
  } catch (error) {
    cancel();
    cleanup();
    if (error instanceof Error) throw error;
    throw new Error(String(error));
  }
}

export async function saveBlob(blob: Blob, name: string) {
  if (isDesktop) {
    return invoke<boolean>("save_file", {
      name,
      data: Array.from(new Uint8Array(await blob.arrayBuffer())),
    });
  }
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return true;
}

export async function openExternal(url: string) {
  await invoke("open_external", { url });
}
