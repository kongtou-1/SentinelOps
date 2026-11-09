import { describe, expect, it } from "vitest";
import {
  resourceSlices,
  serviceTitle,
} from "@/features/resources/model/containerServices";

describe("host resource breakdown", () => {
  it("retains host capacity, other usage and free capacity", () => {
    const slices = resourceSlices(100, 60, [
      { name: "API", value: 20 },
      { name: "DB", value: 10 },
    ])!;
    expect(slices.map((s) => s.value)).toEqual([20, 10, 30, 40]);
    expect(slices.reduce((n, s) => n + s.value, 0)).toBe(100);
  });
  it("distinguishes missing samples from zero usage", () => {
    expect(
      resourceSlices(100, 60, [{ name: "DB", value: null }])![0]!.name,
    ).toContain("未采样");
    expect(
      resourceSlices(100, 0, [{ name: "DB", value: 0 }])!.at(-1)!.value,
    ).toBe(100);
    expect(resourceSlices(null, 20, [])).toBeNull();
    expect(resourceSlices(100, 10, [{ name: "bad", value: 40 }])).toBeNull();
    expect(resourceSlices(100, NaN, [])).toBeNull();
  });
  it("uses human names before compose and container names", () => {
    expect(
      serviceTitle({
        name: "api-1",
        service_name: "推理入口",
        identity: { compose_service: "api" },
      }),
    ).toBe("推理入口");
  });
});
