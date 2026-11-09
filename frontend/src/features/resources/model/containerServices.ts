export type Slice = { name: string; value: number };

export function resourceSlices(
  total: number | null | undefined,
  used: number | null | undefined,
  services: { name: string; value: number | null | undefined }[],
): Slice[] | null {
  if (
    total == null ||
    used == null ||
    !Number.isFinite(total) ||
    !Number.isFinite(used) ||
    total <= 0 ||
    used < 0 ||
    used > total
  )
    return null;
  const measured = services.filter(
    (s): s is Slice =>
      s.value != null && Number.isFinite(s.value) && s.value >= 0,
  );
  const sum = measured.reduce((n, s) => n + s.value, 0);
  // Independent scrapes can disagree; never silently scale service usage down.
  if (sum > used + total * 0.01 || sum > total) return null;
  return [
    ...measured.map((s) => ({ ...s })),
    {
      name:
        services.length === measured.length
          ? "系统及其他占用"
          : "其他占用 / 未采样服务",
      value: Math.max(0, used - sum),
    },
    { name: "剩余资源", value: Math.max(0, total - Math.max(used, sum)) },
  ];
}

export function serviceTitle(c: {
  service_name?: string;
  name: string;
  identity?: Record<string, unknown>;
}) {
  return (
    c.service_name ||
    (typeof c.identity?.compose_service === "string"
      ? c.identity.compose_service
      : "") ||
    c.name
  );
}
