import type { Models } from "@/api/client";

export function databaseSource(
  source: Models["InspectionSource"],
  databases: Models["DatabaseResponse"][],
) {
  const connection = databases.find((d) => d.id === source.connection_id);
  if (!connection) throw new Error("请选择当前服务和环境下的只读连接");
  const columns = connection.allowed_tables[source.table];
  if (!columns) throw new Error("请选择已授权的数据表");
  if (!source.id_column)
    throw new Error("请选择能唯一标识记录的已有字段，如业务编号或主键");
  if (!source.fact_columns?.length) throw new Error("请选择需要检查的数据字段");
  const selected = [
    source.id_column,
    source.version_column,
    source.time_column,
    source.decision_column,
    ...source.fact_columns,
  ].filter(Boolean);
  if (selected.some((c) => !columns.includes(c)))
    throw new Error("所选字段已不在授权表结构中，请重新选择");
  return { ...source, url: "", query_params: [] };
}
