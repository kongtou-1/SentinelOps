import { describe, expect, it } from "vitest";
import type { Models } from "@/api/client";
import { databaseSource } from "./database";
import { emptySource } from "./ui";

const databases = [
  {
    id: "connection",
    allowed_tables: { "public.records": ["business_no", "amount"] },
  },
] as unknown as Models["DatabaseResponse"][];
const source = {
  ...emptySource(),
  agent_api_enabled: false,
  request_method: "GET" as const,
  path_template: "",
  response_format: "records" as const,
  items_path: "",
  object_id_path: "id",
  url: "",
  connection_id: "connection",
  table: "public.records",
  id_column: "business_no",
  version_column: "",
  time_column: "",
  decision_column: "",
  decision_pass_value: "pass",
  decision_fail_value: "fail",
  naive_time_is_utc: false,
  fact_columns: ["amount"],
};

describe("inspection database mapping", () => {
  it("accepts actual business keys without conventional metadata columns", () => {
    expect(databaseSource(source, databases)).toMatchObject({
      id_column: "business_no",
      version_column: "",
      time_column: "",
    });
  });
  it("requires an explicit existing object key and inspection data", () => {
    expect(() =>
      databaseSource({ ...source, id_column: "" }, databases),
    ).toThrow("唯一标识");
    expect(() =>
      databaseSource({ ...source, fact_columns: [] }, databases),
    ).toThrow("检查的数据");
  });
  it("rejects guessed and stale fields and connections", () => {
    expect(() =>
      databaseSource({ ...source, time_column: "updated_at" }, databases),
    ).toThrow("授权表结构");
    expect(() =>
      databaseSource({ ...source, table: "public.missing" }, databases),
    ).toThrow("数据表");
    expect(() => databaseSource(source, [])).toThrow("只读连接");
  });
});
