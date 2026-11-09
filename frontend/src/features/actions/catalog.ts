export function actionName(code: string) {
  return code === "restart_container" ? "重启容器" : code;
}

export function riskName(risk: string) {
  return (
    ({ low: "低", medium: "中", high: "高" } as Record<string, string>)[risk] ||
    risk
  );
}

export function precheckName(code: string) {
  return (
    (
      {
        pinned_ssh: "核对已绑定的 SSH 主机公钥",
        container_identity: "核对容器身份与登记资产一致",
        not_paused_restarting_removing: "容器不处于暂停、重启或删除状态",
      } as Record<string, string>
    )[code] || code
  );
}
