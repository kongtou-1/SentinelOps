# Admin 强制关闭事故验收

日期：2026-10-14。依据 [实施计划](Admin强制关闭事故实施计划.md) 完成代码修改、隔离验收和本机部署。

## 最终行为

- 普通关闭维持恢复核验条件，页面显示禁用原因。Admin 对尚未恢复的可写事故可点“强制关闭”，填写 1–2000 字原因；Operator/Viewer 无此权限，直接请求也返回 403。
- 关闭快照保存操作人、时间、原因和各关联告警的关闭时状态，时间线、审计同步追加。告警仍按真实监控更新，不静默，不补写恢复时间。重复关闭不覆盖首次决定，同一次告警的重复/晚到通知不重开事故，新 occurrence 正常建事故。
- pending/approved 执行授权与关闭在同一事务失效。未派发任务被 worker 阻止；已派发动作继续记录真实执行及验证结果，不能撤回，也不能用关闭替代修复。
- 事故详情、列表、复盘及 PDF/Word 标记强制关闭；未核验时明确提示关闭不代表修复成功。已审核历史报告的冻结快照不变。

## 验证结果

| 验证 | 结果 |
| --- | --- |
| 后端事故、恢复、执行、复盘及相关回归 | 两轮分别 160、99 项通过，去重共 241 项；无失败或跳过 |
| 前端单元测试 | 30 项通过 |
| 类型检查与生产构建 | Docker 前后端镜像构建通过，包含 vue-tsc 和 Vite 构建 |
| 代码检查 | 本次后端变更 Ruff 检查通过 |
| 迁移 | 升降级、旧记录默认空快照、UTC 字段及更新时间触发器通过；业务库 Alembic check 无差异 |
| 隔离浏览器 | 三种角色权限、原因必填、关闭后触发中告警保留、刷新及 API 重启持久化通过，无页面脚本错误 |
| 复盘和导出 | 冻结提示、审核、实际 PDF/Word 导出通过；解析文件文字确认管理员决定和未核验说明均保留 |
| 现用 8080 页面 | 代理健康、Admin 新按钮及普通关闭禁用原因通过；总览未出现监控服务不可用提示 |

执行边界测试使用受控 SSH 测试替身；隔离报告内容为人工验收样本，没有调用真实 LLM 生成，也没有发送邮件或改变远程服务器。

## 本机部署与数据保留

部署前停止 API 和相关 worker，创建完整 PostgreSQL 自定义格式备份，并实际恢复到一次性数据库，核对版本和业务表数量后删除恢复验证库。

- 备份：`.local/backups/force-close-20261014T141140Z/ops_platform.dump`，目录中的 manifest 保存大小、SHA-256 和实际恢复结果。解密主密钥继续独立保留。
- 迁移：`0015_repair_handoffs` → `0016_incident_closure`，仅增加 incidents.closure。
- 更新：backend、frontend、prediction-worker、ai-worker、execution-worker、report-worker、source-worker 均 healthy；源码 worker 原只读目录挂载保留。
- 原事故 `2d73b0dc-e0cb-44c2-8854-d3c0d41f582a` 保持 in_progress，5 条 firing 告警、已采用诊断结果及各实测资源 ID 均核对一致，没有替用户关闭事故。
- 两个本次独立测试库与本机 API/Vite 验收进程已清理，保留 XML、截图、导出和验收 JSON。本次两个 pytest 临时目录受 Windows 访问限制未删除，没有接管目录权限；不被现用服务挂载。

证据位于 `.local/testlab/force-close-tests.xml`、`force-close-extra-tests.xml`、`force-close-browser.json`、`force-close-browser-report.json`、`force-close-live-browser.json`、`force-close-deployment.json` 和 `force-close-cleanup.json`，截图及导出文件同目录。
