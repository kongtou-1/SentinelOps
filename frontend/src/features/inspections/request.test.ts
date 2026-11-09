import { describe, expect, it } from "vitest";
import { apiSource, newRequestDraft, requestHeaders } from "./request";
import { emptySource } from "./ui";

const sourceDefaults = {
  ...emptySource(),
  agent_api_enabled: false,
  request_method: "GET" as const,
  path_template: "",
  response_format: "records" as const,
  items_path: "",
  object_id_path: "id",
  table: "",
  id_column: "id",
  version_column: "updated_at",
  time_column: "updated_at",
  decision_column: "",
  decision_pass_value: "pass",
  decision_fail_value: "fail",
  naive_time_is_utc: false,
};

describe("inspection GET configuration", () => {
  it("extracts pasted URL parameters, retaining repeated keys and empty values", () => {
    const source = apiSource({
      ...sourceDefaults,
      url: "https://example.com/records?status=已完成&tag=a&tag=b&empty=",
      query_params: [{ name: "region", value: "CN" }],
    });
    expect(source.url).toBe("https://example.com/records");
    expect(source.query_params).toEqual([
      { name: "region", value: "CN" },
      { name: "status", value: "已完成" },
      { name: "tag", value: "a" },
      { name: "tag", value: "b" },
      { name: "empty", value: "" },
    ]);
    expect(apiSource(source)).toEqual(source);
  });

  it.each([
    "limit",
    "cursor",
    "updated_after",
    "updated_before",
    "match_field",
    "match_value",
    "LIMIT",
  ])("protects automatic parameter %s", (name) => {
    expect(() =>
      apiSource({
        ...sourceDefaults,
        url: `https://example.com/records?${name}=x`,
      }),
    ).toThrow("自动填写");
  });

  it.each(["api_key", "access_token", "password"])(
    "keeps credential %s out of source URLs",
    (name) => {
      expect(() =>
        apiSource({
          ...sourceDefaults,
          url: `https://example.com/records?${name}=private`,
        }),
      ).toThrow("认证");
    },
  );

  it.each([
    "ftp://example.com",
    "https://user:pass@example.com",
    "https://example.com#secret",
  ])("rejects invalid source %s", (url) => {
    expect(() => apiSource({ ...sourceDefaults, url })).toThrow();
  });

  it("distinguishes preserving stored headers from clearing them", () => {
    expect(requestHeaders(newRequestDraft(true))).toBeUndefined();
    expect(requestHeaders(newRequestDraft())).toEqual({});
  });

  it("builds Bearer auth and additional headers", () => {
    const draft = newRequestDraft();
    draft.auth = "bearer";
    draft.token = "fixture-token";
    draft.headers.push({ name: "X-Tenant", value: "demo" });
    expect(requestHeaders(draft)).toEqual({
      Authorization: "Bearer fixture-token",
      "X-Tenant": "demo",
    });
  });

  it("encodes Basic auth using UTF-8 and builds an API Key header", () => {
    const draft = newRequestDraft();
    draft.auth = "basic";
    draft.username = "用户";
    draft.password = "密码";
    expect(requestHeaders(draft)).toEqual({
      Authorization: "Basic 55So5oi3OuWvhueggQ==",
    });
    draft.auth = "api_key";
    draft.keyValue = "fixture-key";
    expect(requestHeaders(draft)).toEqual({ "X-API-Key": "fixture-key" });
  });

  it("rejects incomplete credentials and conflicting Authorization headers", () => {
    const draft = newRequestDraft();
    draft.auth = "bearer";
    expect(() => requestHeaders(draft)).toThrow("Token");
    draft.token = "fixture-token";
    draft.headers.push({ name: "authorization", value: "other" });
    expect(() => requestHeaders(draft)).toThrow("重复");
  });

  it("rejects invalid and reserved HTTP headers", () => {
    for (const row of [
      { name: "Host", value: "example.com" },
      { name: "bad name", value: "value" },
      { name: "X-Key", value: "one\r\ntwo" },
    ]) {
      const draft = newRequestDraft();
      draft.headers = [row];
      expect(() => requestHeaders(draft)).toThrow();
    }
  });
});
