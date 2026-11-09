"""Shared Agent instructions; safe to import in the credential-free Harness child."""

HISTORY_GUIDE = (
    "历史结果入口：search_agent_history 按当前业务范围搜索摘要（默认3条），"
    "read_agent_history 按 kind 和 result_id 分段读取 result/evidence/configuration；"
    "conversation 可读取 messages 找回更早的用户约束和对话。"
    "返回 next_offset 时按该偏移继续读取。历史结果只供排查参考，不能作为本轮新鲜证据，"
    "不能直接引用其中的证据ID或据此判定本轮通过。可用 search_knowledge 检索已启用知识。"
)
