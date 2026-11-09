export const canEditReport = (status: string, manage: boolean) =>
  manage && status === "draft";
export const canExportReport = (status: string) => status === "approved";
export function revisionRequest<T>(revision: number, content: T) {
  return { expected_revision: revision, content };
}
export const editableIncident = (state: string) =>
  !["closed", "merged"].includes(state);

type IncidentCloseState = {
  status: string;
  alert_count: number;
  firing_alert_count: number;
  recovery_pending_alert_count: number;
  recovery_verified_alert_count: number;
  recovery_terminated_alert_count: number;
};
export function canNormallyCloseIncident(row?: IncidentCloseState) {
  if (
    !row ||
    !editableIncident(row.status) ||
    row.firing_alert_count ||
    row.recovery_pending_alert_count
  )
    return false;
  return (
    row.status === "recovered" ||
    (row.alert_count > 0 &&
      row.recovery_terminated_alert_count > 0 &&
      row.recovery_verified_alert_count +
        row.recovery_terminated_alert_count ===
        row.alert_count)
  );
}
export function incidentCloseReason(row?: IncidentCloseState) {
  if (!row) return "正在读取事故状态";
  if (canNormallyCloseIncident(row)) return "";
  if (row.firing_alert_count)
    return `仍有 ${row.firing_alert_count} 条告警触发中，正常关闭需等待告警解除并通过恢复核验。`;
  if (row.recovery_pending_alert_count)
    return `仍有 ${row.recovery_pending_alert_count} 条告警恢复待核验。`;
  return "事故尚未通过恢复核验，暂不能正常关闭。";
}
