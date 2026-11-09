import type { Models } from "@/api/client";

export type HeaderRow = { name: string; value: string };
export type RequestDraft = {
  savedUrl: string;
  auth: "keep" | "none" | "bearer" | "basic" | "api_key";
  token: string;
  username: string;
  password: string;
  keyName: string;
  keyValue: string;
  headers: HeaderRow[];
};

export const newRequestDraft = (
  saved = false,
  savedUrl = "",
): RequestDraft => ({
  savedUrl,
  auth: saved ? "keep" : "none",
  token: "",
  username: "",
  password: "",
  keyName: "X-API-Key",
  keyValue: "",
  headers: [],
});

export const automaticParameters = [
  { name: "updated_after", value: "本轮开始时间（UTC）" },
  { name: "updated_before", value: "本轮结束时间（UTC）" },
  { name: "limit", value: "本页记录上限" },
  { name: "cursor", value: "接口返回的下一页游标（首轮不传）" },
];
const reserved = new Set([
  ...automaticParameters.map((p) => p.name),
  "match_field",
  "match_value",
]);
const sensitive =
  /password|passphrase|secret|api.?key|authorization|token|private.?key/i;

export function apiSource(config: Models["InspectionSource"]) {
  if (config.http_connection_id)
    return { ...config, url: "", connection_id: null };
  let parsed: URL;
  try {
    parsed = new URL(config.url.trim());
  } catch {
    throw new Error("请输入完整的 HTTP 或 HTTPS 接口地址");
  }
  if (
    !["http:", "https:"].includes(parsed.protocol) ||
    parsed.username ||
    parsed.password ||
    parsed.hash
  ) {
    throw new Error(
      "接口地址须使用 HTTP(S)，认证请在认证表单填写，地址不能包含锚点",
    );
  }
  const params = [
    ...(config.query_params || []),
    ...Array.from(parsed.searchParams, ([name, value]) => ({ name, value })),
  ]
    .filter((p) => p.name.trim() || p.value)
    .map((p) => ({ name: p.name.trim(), value: p.value }));
  for (const p of params) {
    if (!p.name) throw new Error("请填写查询参数名");
    if (reserved.has(p.name.toLowerCase()))
      throw new Error(`${p.name} 由巡检自动填写，请移除该自定义参数`);
    if (sensitive.test(p.name))
      throw new Error(`${p.name} 属于认证参数，请改在认证或请求头中填写`);
  }
  return {
    ...config,
    url: config.url.trim().split("?")[0]!,
    query_params: params,
    connection_id: null,
  };
}

export function requestHeaders(
  draft: RequestDraft,
): Record<string, string> | undefined {
  if (draft.auth === "keep") return undefined;
  const rows = [...draft.headers];
  if (draft.auth === "bearer") {
    if (!draft.token.trim()) throw new Error("请填写 Bearer Token");
    rows.push({ name: "Authorization", value: `Bearer ${draft.token.trim()}` });
  } else if (draft.auth === "basic") {
    if (!draft.username || !draft.password)
      throw new Error("请填写 Basic Auth 用户名和密码");
    if (draft.username.includes(":"))
      throw new Error("Basic Auth 用户名不能包含冒号");
    const bytes = new TextEncoder().encode(
      `${draft.username}:${draft.password}`,
    );
    rows.push({
      name: "Authorization",
      value: `Basic ${btoa(String.fromCharCode(...bytes))}`,
    });
  } else if (draft.auth === "api_key") {
    if (!draft.keyName.trim() || !draft.keyValue.trim())
      throw new Error("请填写 API Key 请求头名称和密钥");
    rows.push({ name: draft.keyName, value: draft.keyValue });
  }
  const headers: Record<string, string> = {};
  const names = new Set<string>();
  for (const row of rows) {
    if (!row.name.trim() && !row.value) continue;
    const name = row.name.trim();
    if (!/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(name) || /[\r\n]/.test(row.value))
      throw new Error("请求头名称或值格式不正确");
    if (
      ["host", "content-length", "transfer-encoding", "connection"].includes(
        name.toLowerCase(),
      )
    )
      throw new Error(`不支持自定义 ${name} 请求头`);
    if (names.has(name.toLowerCase()))
      throw new Error(`${name} 请求头重复，请保留一项`);
    names.add(name.toLowerCase());
    headers[name] = row.value;
  }
  return headers;
}
