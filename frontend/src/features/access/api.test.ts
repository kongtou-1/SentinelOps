import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/api/client";
import { updatePolicies } from "./api";

vi.mock("@/api/client", async (original) => ({
  ...(await original<typeof import("@/api/client")>()),
  api: vi.fn(),
}));

beforeEach(() => {
  vi.resetAllMocks();
});

describe("资源限制批量保存", () => {
  const users = [
    {
      kind: "application",
      id: "app-1",
      userId: "user-1",
      name: "用户一",
      version: 2,
    },
    {
      kind: "application",
      id: "app-1",
      userId: "user-3",
      name: "用户三",
      version: 4,
    },
  ];

  it("仅写入传入的用户，保留各用户版本，不建立全员或角色规则", async () => {
    expect(await updatePolicies(users, true)).toEqual({
      succeeded: 2,
      failed: [],
    });
    expect(vi.mocked(api).mock.calls).toEqual([
      [
        "/access-policies/application/app-1?user_id=user-1",
        "PUT",
        { restricted: true, expected_version: 2 },
      ],
      [
        "/access-policies/application/app-1?user_id=user-3",
        "PUT",
        { restricted: true, expected_version: 4 },
      ],
    ]);
  });

  it("版本冲突不重试，继续保存其他选中用户并报告部分失败", async () => {
    vi.mocked(api)
      .mockRejectedValueOnce(new Error("资源策略已变化"))
      .mockResolvedValueOnce({});
    expect(await updatePolicies(users, false)).toEqual({
      succeeded: 1,
      failed: ["用户一：资源策略已变化"],
    });
    expect(api).toHaveBeenCalledTimes(2);
    expect(api).toHaveBeenLastCalledWith(
      "/access-policies/application/app-1?user_id=user-3",
      "PUT",
      { restricted: false, expected_version: 4 },
    );
  });

  it("保留全员资源策略的保存路径", async () => {
    await updatePolicies(
      [{ kind: "server", id: "server-1", name: "服务器", version: 0 }],
      true,
    );
    expect(api).toHaveBeenCalledWith(
      "/access-policies/server/server-1",
      "PUT",
      { restricted: true, expected_version: 0 },
    );
  });

  it("空选择不发出写请求", async () => {
    expect(await updatePolicies([], true)).toEqual({
      succeeded: 0,
      failed: [],
    });
    expect(api).not.toHaveBeenCalled();
  });
});
