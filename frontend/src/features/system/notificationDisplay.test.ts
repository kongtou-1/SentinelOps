import { describe, expect, it } from "vitest";
import {
  operationActor,
  operationName,
  operationTarget,
  windowState,
  type OperationRecord,
} from "./notificationDisplay";
import { storageGroups } from "./storageDisplay";

const record: OperationRecord = {
  id: "record-id",
  user_id: "internal-user-id",
  actor_username: "operator",
  action: "inspection_enqueued",
  resource_kind: "inspection_run",
  resource_id: "internal-run-id",
  resource_name: "订单检查",
  detail: {},
  created_at: "2026-10-28T08:00:00Z",
};

describe("操作记录展示", () => {
  it("显示中文操作和具体对象，区分操作人、系统任务与失效账号", () => {
    expect(operationName(record)).toBe("发起巡检");
    expect(operationTarget(record)).toBe("订单检查");
    expect(operationActor(record)).toBe("operator");
    expect(
      operationActor({ ...record, user_id: null, actor_username: null }),
    ).toBe("系统任务");
    expect(operationActor({ ...record, actor_username: null })).toBe(
      "账号不可用",
    );
  });
  it("通知策略按当次记录显示，缺失历史字段时不推断设置", () => {
    const policy = { ...record, resource_kind: "notification_policy" };
    expect(operationTarget(policy)).toBe("通知策略");
    expect(
      operationTarget({
        ...policy,
        detail: { managed: true, email_enabled: false },
      }),
    ).toBe("平台通知 · 邮件已关闭");
    expect(operationTarget({ ...policy, detail: { managed: false } })).toBe(
      "Alertmanager 邮件",
    );
  });
  it("按开始和结束时间区分维护状态", () => {
    const row = {
      starts_at: "2026-10-28T08:00:00Z",
      ends_at: "2026-10-28T09:00:00Z",
    };
    expect(windowState(row, Date.parse(row.starts_at) - 1)).toBe("未开始");
    expect(windowState(row, Date.parse(row.starts_at))).toBe("生效中");
    expect(windowState(row, Date.parse(row.ends_at))).toBe("已结束");
  });
});

it("存储按业务分组，保留未知表的占用且不重复累计", () => {
  const tables = [
    { name: "alert_events", bytes: 100 },
    { name: "incident_timeline", bytes: 200 },
    { name: "analysis_messages", bytes: 70 },
    { name: "model_usage", bytes: 40 },
    { name: "new_internal_table", bytes: 10 },
  ];
  const result = storageGroups(tables);
  expect(result.find((row) => row.name === "告警与事故")?.bytes).toBe(300);
  expect(result.find((row) => row.name === "其他平台数据")?.bytes).toBe(10);
  expect(result.reduce((sum, row) => sum + row.bytes, 0)).toBe(420);
});
