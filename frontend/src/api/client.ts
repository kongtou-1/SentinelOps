import type { components } from "@/api/schema";
import { desktopFetch, isDesktop, saveBlob } from "@/platform/desktop";
const transport = (path: string, init: RequestInit) =>
  isDesktop ? desktopFetch(path, init) : fetch(path, init);
export type Models = components["schemas"];
type Tokens = Models["TokenResponse"];
const key = "sentinelops.session";
let refreshPromise: Promise<void> | undefined;
let generation = 0;
export function tokens(): Tokens | null {
  try {
    return JSON.parse(sessionStorage.getItem(key) || "null");
  } catch {
    return null;
  }
}
export function saveTokens(value: Tokens | null) {
  generation++;
  if (value) sessionStorage.setItem(key, JSON.stringify(value));
  else sessionStorage.removeItem(key);
}
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public detail?: unknown,
  ) {
    super(message);
  }
}
export function query(values: Record<string, unknown> = {}) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(values))
    if (v !== "" && v !== null && v !== undefined) q.set(k, String(v));
  return q.size ? "?" + q : "";
}
async function error(response: Response): Promise<never> {
  const data = await response.json().catch(() => ({}));
  const detail = data.detail;
  const message = Array.isArray(detail)
    ? detail
        .map(
          (x: { loc?: string[]; msg?: string }) =>
            `${x.loc?.slice(1).join(".")}: ${x.msg}`,
        )
        .join("；")
    : detail;
  const defaults: Record<number, string> = {
    401: "登录已失效，请重新登录",
    403: "没有执行此操作的权限",
    404: "记录不存在",
    409: "记录状态或版本已变化，请刷新后重试",
    422: "请检查填写的内容",
    502: "服务连接暂时失败，请稍后重试",
    503: "依赖服务或配置不可用",
  };
  throw new ApiError(
    response.status,
    typeof message === "string"
      ? message
      : defaults[response.status] || `请求失败（${response.status}）`,
    detail,
  );
}
async function refresh() {
  if (!refreshPromise) {
    const before = generation;
    refreshPromise = (async () => {
      const current = tokens();
      if (!current) throw new ApiError(401, "请登录");
      const response = await transport("/api/v1/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: current.refresh_token }),
      });
      if (!response.ok) await error(response);
      const next = await response.json();
      if (before !== generation) throw new ApiError(401, "认证会话已变化");
      saveTokens(next);
    })()
      .catch((e) => {
        if (
          before === generation &&
          (!isDesktop ||
            (e instanceof ApiError && [401, 403].includes(e.status)))
        ) {
          saveTokens(null);
          window.dispatchEvent(new Event("session-expired"));
        }
        throw e;
      })
      .finally(() => {
        refreshPromise = undefined;
      });
  }
  return refreshPromise;
}
export async function authorizedFetch(
  path: string,
  init: RequestInit = {},
  retry = true,
): Promise<Response> {
  const current = tokens();
  const headers = new Headers(init.headers);
  if (current) headers.set("Authorization", `Bearer ${current.access_token}`);
  if (init.body && !(init.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const response = await transport(
    path.startsWith("/health/") ? path : "/api/v1" + path,
    { ...init, headers },
  );
  if (
    response.status === 401 &&
    current &&
    retry &&
    !path.startsWith("/auth/")
  ) {
    // A second 401 may arrive after another request already rotated the token.
    if (tokens()?.access_token === current.access_token) await refresh();
    return authorizedFetch(path, init, false);
  }
  if (!response.ok) {
    if (response.status === 403)
      window.dispatchEvent(new Event("permissions-changed"));
    if (response.status === 401 && !path.startsWith("/auth/")) {
      saveTokens(null);
      window.dispatchEvent(new Event("session-expired"));
    }
    await error(response);
  }
  return response;
}
export async function request<T>(
  path: string,
  init: RequestInit = {},
  retry = true,
): Promise<{ data: T; total: number | null; response: Response }> {
  const response = await authorizedFetch(path, init, retry);
  const totalHeader = response.headers.get("X-Total-Count");
  const data = response.status === 204 ? undefined : await response.json();
  return {
    data,
    total: totalHeader === null ? null : Number(totalHeader),
    response,
  };
}
export async function api<T = unknown>(
  path: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  return (
    await request<T>(path, {
      method,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    })
  ).data;
}
export async function all<T>(path: string, signal?: AbortSignal): Promise<T[]> {
  const result: T[] = [];
  for (let offset = 0; ; offset += 100) {
    const r = await request<T[]>(
      path + (path.includes("?") ? "&" : "?") + `offset=${offset}&limit=100`,
      { signal },
    );
    result.push(...r.data);
    if (r.data.length < 100 || (r.total !== null && result.length >= r.total))
      return result;
  }
}
export async function download(path: string) {
  // Obtain an authorized blob without putting bearer tokens in URLs.
  const r = await authorizedFetch(path);
  const disposition = r.headers.get("Content-Disposition") || "";
  let filename = disposition.match(/filename="([^"]+)"/)?.[1] || "download";
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (encoded) {
    try {
      filename = decodeURIComponent(encoded);
    } catch {
      /* Use plain filename. */
    }
  }
  await saveBlob(await r.blob(), filename);
}
