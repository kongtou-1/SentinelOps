import { describe, expect, it } from "vitest";
import {
  activeTasks,
  capacity,
  duration,
  healthState,
  workerState,
} from "./healthDisplay";

describe("系统状态展示", () => {
  it("空闲任务可收起，运行中和只有历史异常的任务仍保留", () => {
    const idle = {
      name: "prediction",
      queued: 0,
      running: 0,
      failed_24h: 0,
      oldest_wait_seconds: null,
    };
    const rows = [
      idle,
      { ...idle, name: "report", queued: 2, oldest_wait_seconds: 125 },
      { ...idle, name: "diagnosis", running: 1 },
      { ...idle, name: "execution", failed_24h: 3 },
    ];
    expect(activeTasks(rows).map((row) => row.name)).toEqual([
      "report",
      "diagnosis",
      "execution",
    ]);
    expect(rows).toHaveLength(4);
  });

  it("缺失检查结果不显示正常；停用和应启用但没有心跳的服务分开显示", () => {
    expect(healthState().label).toBe("未确认");
    expect(healthState("not_ready").tone).not.toBe("success");
    expect(workerState({ expected: false, status: "not_observed" }).label).toBe(
      "未启用",
    );
    expect(workerState({ expected: true, status: "not_observed" }).tone).toBe(
      "warning",
    );
    expect(workerState({ expected: false, status: "unhealthy" }).tone).toBe(
      "danger",
    );
  });

  it("容量与等待时间保留零值，缺失值保持未知", () => {
    expect(capacity()).toBe("—");
    expect(capacity(0)).toBe("0.0 KB");
    expect(capacity(30 * 1024 ** 2)).toBe("30.0 MB");
    expect(duration(null)).toBe("—");
    expect(duration(0)).toBe("0 秒");
    expect(duration(125)).toBe("2 分 5 秒");
    expect(duration(7200)).toBe("2 小时 0 分");
  });
});
