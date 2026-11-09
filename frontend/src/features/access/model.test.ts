import { describe, expect, it } from "vitest";
import {
  bundleState,
  missingPermissionItems,
  ownsBundle,
  toggleBundle,
} from "./model";
const bundle = {
  id: "diagnosis.run",
  module: "diagnosis",
  name: "诊断",
  permissions: ["analyses:read", "analyses:run"],
};
describe("权限组", () => {
  it("申请判断要求权限组的全部权限，不能只看主要操作", () => {
    const operation = { ...bundle, primary_permissions: ["analyses:run"] };
    expect(ownsBundle(undefined, operation)).toBe(false);
    expect(ownsBundle(["analyses:run"], operation)).toBe(false);
    expect(ownsBundle(bundle.permissions, operation)).toBe(true);
  });
  it("批量申请按资源排除已拥有的权限", () => {
    expect(
      missingPermissionItems("application", ["a", "b"], [bundle.id], [bundle], {
        a: bundle.permissions,
        b: ["analyses:read"],
      }),
    ).toEqual([
      {
        bundle_ids: [bundle.id],
        resource_kind: "application",
        resource_id: "b",
        include_descendants: true,
      },
    ]);
    expect(
      missingPermissionItems("application", ["a"], [bundle.id], [bundle], {
        a: bundle.permissions,
      }),
    ).toEqual([]);
  });
  it("基础查看不会显示为部分拥有操作权限", () => {
    const operation = { ...bundle, primary_permissions: ["analyses:run"] };
    expect(bundleState(["analyses:read"], operation)).toEqual({
      checked: false,
      partial: false,
    });
    expect(
      toggleBundle(["analyses:read", "analyses:run"], operation, false),
    ).toEqual(["analyses:read"]);
  });
  it("兼容迁移后的部分权限组合", () => {
    expect(bundleState(["analyses:read"], bundle)).toEqual({
      checked: false,
      partial: true,
    });
  });
  it("批量选择不丢失其他模块权限", () => {
    const permissions = toggleBundle(["reports:read"], bundle, true);
    expect(bundleState(permissions, bundle).checked).toBe(true);
    expect(toggleBundle(permissions, bundle, false)).toEqual(["reports:read"]);
  });
});
