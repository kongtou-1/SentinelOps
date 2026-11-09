import { describe, expect, it } from "vitest";
import type { Models } from "@/api/client";
import {
  activeRun,
  mergeEvents,
  stage,
} from "@/features/diagnosis/model/diagnosis";
function event(seq: number, kind: string, data = {}): Models["EventResponse"] {
  return {
    id: String(seq),
    conversation_id: "c",
    run_id: "r",
    seq,
    kind,
    data,
    created_at: "2026-10-05T00:00:00Z",
    updated_at: "2026-10-05T00:00:00Z",
  };
}
describe("diagnosis runtime", () => {
  it("deduplicates reconnect events and preserves sequence order", () => {
    expect(
      mergeEvents(
        [event(1, "run_started"), event(2, "model_started")],
        [event(3, "tool_started"), event(2, "model_started")],
      ).map((e) => e.seq),
    ).toEqual([1, 2, 3]);
  });
  it("derives truthful phases from persisted runtime events", () => {
    expect(
      stage([event(1, "tool_started", { tool: "get_recent_logs" })], "running"),
    ).toBe("读取容器日志");
    expect(stage([], "queued")).toBe("等待诊断 worker");
    expect(stage([], "cancelling")).toBe("正在停止本轮");
    expect(activeRun("completed")).toBe(false);
  });
});
