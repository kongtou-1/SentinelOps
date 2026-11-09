import { date, label } from "@/utils/display";
import { metricLabels } from "@/config/metrics";
import type { Row } from "@/types/records";
export const options = (values: string[]) =>
  values.map((v) => ({ label: label(v), value: v }));
export const title = (key: string, label: string, width = 200) => ({
  key,
  label,
  width,
});
export const state = { key: "status", label: "状态", status: true, width: 135 };
export const updated = {
  key: "updated_at",
  label: "更新时间",
  width: 175,
  format: (r: Row) => date(r.updated_at),
};
export const created = {
  key: "created_at",
  label: "创建时间",
  width: 175,
  format: (r: Row) => date(r.created_at),
};
export const metricOptions = (items: string[]) =>
  items.map((value) => ({ value, label: metricLabels[value] || value }));
