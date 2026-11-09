export function storageGroups(tables: { name: string; bytes: number }[]) {
  const groups = new Map<string, number>();
  for (const table of tables) {
    const name = /^(alert_|incident)/.test(table.name)
      ? "告警与事故"
      : /^(analysis_|agent_analyses|diagnosis_)/.test(table.name)
        ? "诊断记录"
        : /^prediction/.test(table.name)
          ? "预测记录"
          : /^inspection/.test(table.name)
            ? "巡检记录"
            : /^(postmortem_|knowledge_|improvement_)/.test(table.name)
              ? "复盘与知识"
              : /^(model_usage|usage_cursors)$/.test(table.name)
                ? "模型调用记录"
                : "其他平台数据";
    groups.set(name, (groups.get(name) || 0) + table.bytes);
  }
  return [...groups]
    .map(([name, bytes]) => ({ name, bytes }))
    .sort((a, b) => b.bytes - a.bytes);
}
