# 默认只读诊断 Skill

2026-10-15 检查本机平台，已有并启用 `disk_full`、`memory_oom`、`container_unavailable`、`llm_errors` 四个专用诊断 Skill，版本均为 2。Agent 已支持通过 `list_skills` 查看目录、通过 `read_skill` 按 ID 和版本加载正文。

本次新增 `sre_diagnosis`（SRE 综合只读诊断），作为通用诊断起点，保留现有专用 Skill。上游选用 [Jeffallan/claude-skills 的 sre-engineer](https://github.com/Jeffallan/claude-skills/tree/958e60fb998e008507c978b7943211f337def6a6/skills/sre-engineer)，适合 Prometheus 信号分析、事故影响评估及恢复核验。GitHub 官方 API 于当日返回仓库 **11,881 Star**、MIT 许可；Star 是整个仓库的数量，不是单个 Skill 的数量。

固定上游提交为 `958e60fb998e008507c978b7943211f337def6a6`。正文位于 `backend/app/skills/sre-diagnosis/SKILL.md`，同目录保留原 MIT 许可。正文中文化并映射为本平台实际工具，补充 Docker 状态、LLM 探测、预测新鲜度与冻结源码版本的判断规则。只保留只读诊断；移除上游的自动化脚本、自动修复、配置生成、混沌实验及外部通知流程。上游示例与资源不在运行时下载或执行。

## 行为与配置

Agent 每轮先查看启用目录；存在 `sre_diagnosis` 时，按目录 ID、版本加载它，再按需加载专用 Skill。默认 Skill 仅允许诊断和后续只读检查，不包含代码、配置、凭据、资源、服务状态的修改步骤，不调用 `propose_action_skill`，诊断的 `proposal` 固定为 `null`。专用 Skill 中的变更指引不能扩大默认 Skill 的范围。

2026-10-23 修正：新运行由后端冻结 `diagnosis_mode=read_only`，工具清单移除动作提案，结果校验拒绝非空提案；停用默认 Skill 也不会增加运行权限。历史审批按原授权继续校验，不回写旧诊断。会话证据、采用和回放的组合修复见[诊断链路记录](../project/Agent诊断链路修复与验收.md)。

默认 Skill 进入现有 `knowledge_skills`，通过 `/api/v1/skills` 管理，启用后可被诊断工具加载。它没有进入资源中心的展示市场或 Action Skill 执行注册表，没有新增工具、审批、执行权限或数据库表。系统原有执行与审批模块保持原有流程。

安装沿用幂等脚本：

```powershell
docker compose exec -T backend /app/.venv/bin/python -m scripts.seed_knowledge
```

重复安装不覆盖管理员编辑，不重启用已停用的默认 Skill，不恢复软删除记录。管理员可在诊断 Skill 管理页编辑、停用或删除；停用后 Agent 沿用目录中剩余可用 Skill。修改 `SKILL.md` 后不会自动覆盖数据库正文；已安装版本需由管理员通过现有 Skill 管理接口显式更新。

`harness_adapter.SYSTEM` 的默认选择规则也被持续对话使用；已有运行保持原有上下文，新的运行使用更新后的规则。默认 Skill 正文来自数据库，加载证据保留本轮使用的 ID、版本与正文，不把 Skill 本身作为故障证据。

## 验证范围

测试覆盖幂等安装、保留人工编辑/停用/软删除、真实 Harness SDK/MCP 加载默认正文、单次诊断与工作台候选/采用，以及无动作提案。模型响应由本地模拟 Provider 提供，验证接入与结构约束，不代表真实 DeepSeek 的诊断质量或指令遵循评测。

本次在独立 `ops_sre_skill_20261015_test` 执行 `test_ai.py`、`test_conversations.py`、`test_harness_adapter.py`：首轮 75 项通过，1 项新增测试误将现有采用接口的 HTTP 200 写为 201；按现有 API 契约修正断言后，该项复验通过。未改变接口行为。Skill 格式校验、Ruff 检查和格式检查均通过，测试库已清理。

本机安装后，启用目录含 5 个 Skill，新增默认 Skill 为版本 1，原 4 个仍为版本 2。通过实际 `list_skills` / `read_skill` 分发核对数据库正文与镜像内文件一致，并核对单次诊断及持续对话均包含默认加载规则。API 与 AI worker 更新后均健康。验收记录：`.local/sre-skill-acceptance.json`；测试记录：`.local/sre-skill-tests.xml`、`.local/sre-skill-recheck-tests.xml`。
