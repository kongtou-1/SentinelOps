import { describe, expect, it } from "vitest";
import { channelPriority } from "./agentConfig";

describe("Agent 通道显示", () => {
  it("按有序主备链标记优先级", () => {
    expect([0, 1, 2].map(channelPriority)).toEqual([
      "主用",
      "备用 1",
      "备用 2",
    ]);
  });
});
