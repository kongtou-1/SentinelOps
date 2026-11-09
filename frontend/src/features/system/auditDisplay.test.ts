import { describe, expect, it } from "vitest";
import {
  auditActionName,
  auditActorName,
  auditIncidentName,
  auditReason,
} from "./auditDisplay";
import { systemLists } from "./list.config";

describe("审计日志可读信息", () => {
  it("主列表使用用户名、事故标题和中文操作，移除追踪编号", () => {
    const row = {
      action: "execution_succeeded",
      user_id: "user-uuid",
      actor_username: "运维小王",
      incident_id: "incident-uuid",
      incident_title: "订单接口不可用",
      request_id: "request-uuid",
    };
    const columns = systemLists["audit-logs"]!.columns;
    const values = columns.map(
      (column) => column.format?.(row) || row[column.key as keyof typeof row],
    );
    expect(values).toContain("运维小王");
    expect(values).toContain("订单接口不可用");
    expect(values).toContain("运维动作执行成功");
    expect(values).not.toContain("user-uuid");
    expect(values).not.toContain("incident-uuid");
    expect(columns.some((column) => column.key === "request_id")).toBe(false);
  });
  it("缺失名称时区分系统任务和不可用账号，不回退到内部 ID", () => {
    expect(auditActorName({ user_id: null, actor_username: null })).toBe(
      "系统任务",
    );
    expect(auditActorName({ user_id: "user-uuid", actor_username: null })).toBe(
      "账号不可用",
    );
    expect(auditIncidentName({ incident_title: null })).toBe("事故名称未保留");
    expect(auditActionName("future_action")).toBe("其他操作");
  });
  it("拒绝原因显示业务含义", () => {
    expect(auditReason({ detail: { reason: "not_approved" } })).toBe(
      "尚未获得有效审批",
    );
    expect(auditReason({ detail: { error_code: "new_internal_error" } })).toBe(
      "详细原因见排查信息",
    );
  });
});
