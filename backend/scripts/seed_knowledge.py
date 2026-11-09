"""Idempotent installation of agent Skills and separate reference documents."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import KnowledgeDocument, KnowledgeSkill

SOPS = [
    (
        "disk_full",
        "磁盘空间不足诊断",
        "检查目标挂载点、磁盘剩余空间和 inode，区分实际耗尽与预测风险。"
        "结合历史增长和预测质量；不把不同挂载点相加。不自动删除文件，清理前确认保留策略。",
    ),
    (
        "memory_oom",
        "内存增长与 OOM 诊断",
        "比较宿主机可用内存、容器内存增长和同机其他容器。"
        "读取容器 State 中的 OOMKilled 及退出码。没有 OOM 证据不得认定 OOM；"
        "内存持续增长提示泄漏或缓存，应结合应用日志确认。",
    ),
    (
        "container_unavailable",
        "Docker 容器停止与不可用诊断",
        "检查 container_up 告警、容器状态、最新采集时间和应用可用性；读取脱敏日志和退出状态。"
        "区分真实进程停止与采集器失联。可提议 restart_container，风险 medium，必须人工审批；"
        "提案不能自动执行。恢复后重新核对指标，不把旧告警当作当前故障。",
    ),
    (
        "llm_errors",
        "LLM 服务错误分类诊断",
        "401 为认证失败，402 为余额不足，429 为限流，500/503 为服务异常。"
        "结合当前探测时间、成功率和延迟，区分网络超时。"
        "禁止将 API Key 放进模型上下文；修改凭据或额度由人工处理。",
    ),
]

SKILL_STEPS = {
    "disk_full": (
        "磁盘使用率、剩余空间或 inode 告警，以及磁盘容量预测风险。",
        "1. 用 get_alert_context 确认服务器、挂载点、告警时间与恢复核验状态。\n"
        "2. 按需用 get_server_metrics 读取 host_disk_percent、host_disk_available_bytes、"
        "host_inode_percent；按挂载点分别比较。\n"
        "3. 用 get_predictions 检查趋势和 fresh；缺少当前数据时保留缺口。\n"
        "4. 用 search_knowledge 查找对应 SOP，给出人工排查或清理建议。",
    ),
    "memory_oom": (
        "宿主机或容器内存升高、持续增长、OOM 或异常退出。",
        "1. 用 get_alert_context 确认目标服务器和容器。\n"
        "2. 用 get_server_metrics 读取 host_memory_available_bytes 或 host_memory_percent，"
        "用 get_container_metrics 读取 container_memory_bytes。\n"
        "3. 用 get_container_status 检查 OOMKilled 和退出码，按需用 get_recent_logs 查退出原因。\n"
        "4. 用 get_predictions 与 search_knowledge 辅助判断增长趋势和排查方向。",
    ),
    "container_unavailable": (
        "容器停止、不可用、健康检查失败或疑似采集器失联。",
        "1. 用 get_alert_context 确认容器、关联告警和最新观测时间。\n"
        "2. 用 get_container_status 及 get_container_metrics 的 container_up "
        "区分进程停止、健康检查失败和采集失联。\n"
        "3. 用 get_recent_logs 检查退出原因，按需读取容器资源指标并用 search_knowledge 查 SOP。\n"
        "4. 有恢复依据且确需重启时，仅用 propose_action_skill 校验 restart_container 提案，"
        "交由人工审批与发起执行。",
    ),
    "llm_errors": (
        "LLM 探测失败、成功率下降、延迟升高或认证、余额、限流告警。",
        "1. 用 get_alert_context 核对 Endpoint、探测时间和错误分类。\n"
        "2. 用 get_llm_metrics 按需读取 llm_success_rate、llm_latency_avg_seconds "
        "或 llm_latency_p95_seconds。\n"
        "3. 用 search_knowledge 查找错误分类 SOP，区分认证、余额、限流、服务异常和网络超时。\n"
        "4. 明确证据支持的分类与缺口，凭据、额度或配置变更交由人工处理。",
    ),
}


def main():
    with SessionLocal() as db:
        for code, title, content in SOPS:
            when, steps = SKILL_STEPS[code]
            instructions = (
                f"# {title}\n\n## 何时使用\n{when}\n\n## 诊断步骤\n{steps}\n\n"
                f"## 判断要求\n{content}\n\n## 输出要求\n"
                "说明已核验事实、原因假设、影响、下一步和数据缺口，引用实际工具证据 ID。"
                "遵循当前会话的诊断输出格式；Skill 本身不是故障证据。"
                "仅使用当前开放且在事故范围内的工具，不执行 Shell 或运维变更。"
            )
            skill = db.scalar(select(KnowledgeSkill).where(KnowledgeSkill.code == code))
            if skill is None:
                db.add(KnowledgeSkill(code=code, title=title, content=instructions, tags=[code]))
            elif skill.deleted_at is None and skill.content == content:
                # Upgrade only the original built-in prose; preserve human edits and enabled state.
                skill.content = instructions
                skill.version += 1
            if not db.scalar(select(KnowledgeDocument.id).where(KnowledgeDocument.title == title)):
                db.add(
                    KnowledgeDocument(
                        title=title, content=content, source="Reviewed built-in SOP v1", tags=[code]
                    )
                )
        if (
            db.scalar(select(KnowledgeSkill.id).where(KnowledgeSkill.code == "sre_diagnosis"))
            is None
        ):
            db.add(
                KnowledgeSkill(
                    code="sre_diagnosis",
                    title="SRE 综合只读诊断（默认）",
                    content=(
                        Path(__file__).resolve().parents[1] / "app/skills/sre-diagnosis/SKILL.md"
                    ).read_text(encoding="utf-8"),
                    tags=["sre", "docker", "llm", "只读诊断"],
                )
            )
        db.commit()
    print("Built-in agent Skills installed; reference documents queued for indexing")


if __name__ == "__main__":
    main()
