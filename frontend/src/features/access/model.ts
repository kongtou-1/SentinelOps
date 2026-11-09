export type Permission = {
  code: string;
  name: string;
  module: string;
  requestable: boolean;
};
export type Bundle = {
  id: string;
  module: string;
  name: string;
  permissions: string[];
  primary_permissions?: string[];
  scopes?: string[];
};
export type Catalogue = {
  modules: Record<string, string>;
  permissions: Permission[];
  bundles: Bundle[];
  dashboards: Record<string, string>;
};
export const resourceKinds = [
  { value: "application", label: "应用" },
  { value: "server", label: "服务器" },
  { value: "dashboard", label: "看板" },
  { value: "container", label: "容器" },
  { value: "llm_endpoint", label: "LLM服务" },
  { value: "service_monitor", label: "服务监测" },
  { value: "service_database", label: "数据库连接" },
  { value: "global", label: "全部普通资源" },
];
export const durations = [
  { value: 1, label: "1天" },
  { value: 7, label: "7天" },
  { value: 30, label: "30天" },
  { value: 0, label: "长期" },
];
export function bundleState(permissions: string[], bundle: Bundle) {
  const primary = bundle.primary_permissions || bundle.permissions;
  const count = primary.filter((code) => permissions.includes(code)).length;
  return {
    checked: count === primary.length,
    partial: count > 0 && count < primary.length,
  };
}
export function ownsBundle(permissions: string[] | undefined, bundle: Bundle) {
  return (
    !!permissions &&
    bundle.permissions.every((code) => permissions.includes(code))
  );
}
export function missingPermissionItems(
  kind: string,
  targets: string[],
  selected: string[],
  bundles: Bundle[],
  permissions: Record<string, string[]>,
) {
  return targets
    .map((id) => ({
      bundle_ids: selected.filter((key) => {
        const bundle = bundles.find((item) => item.id === key);
        return !!bundle && !ownsBundle(permissions[id], bundle);
      }),
      resource_kind: kind,
      resource_id: id,
      include_descendants: true,
    }))
    .filter((item) => item.bundle_ids.length);
}
export function toggleBundle(
  permissions: string[],
  bundle: Bundle,
  checked: boolean,
) {
  const next = new Set(permissions);
  for (const code of checked
    ? bundle.permissions
    : bundle.primary_permissions || bundle.permissions)
    checked ? next.add(code) : next.delete(code);
  return [...next];
}
