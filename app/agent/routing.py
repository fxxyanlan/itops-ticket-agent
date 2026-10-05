"""条件边路由：决定 Agent 的下一步走向，以及「自动闭环 / 转人工」分支。

设计原则：分支判定用代码里的确定性规则，而不是让大模型自己拍板，
保证结果可解释、可测试。
"""

from app.config import settings
from app.constants import HIGH_RISK_TYPES, FAULT_TYPE_OTHER, URGENCY_CRITICAL
from app.agent.state import TicketState


def has_tool_calls(state: TicketState) -> bool:
    """最近一条 assistant 消息是否包含工具调用。"""
    messages = state.get("messages") or []
    if not messages:
        return False
    last = messages[-1]
    return last.get("role") == "assistant" and bool(last.get("tool_calls"))


def needs_evidence(state: TicketState) -> bool:
    """是否缺少关键证据（历史方案检索结果）。"""
    return state.get("matched_solutions") is None


def decide(state: TicketState) -> tuple[str, str]:
    """返回 (decision, reason)，decision ∈ {auto, human}。

    命中任一「转人工」硬规则即转人工，否则自动闭环。
    """
    urgency = state.get("urgency")
    fault_type = state.get("fault_type") or FAULT_TYPE_OTHER
    threshold = settings.match_threshold
    score = float(state.get("best_match_score") or 0.0)

    # 规则1：P0 灾难级
    if urgency == URGENCY_CRITICAL:
        return "human", "紧急等级 P0（业务中断级），必须人工立即处理"

    # 规则2：高危故障类型
    if fault_type in HIGH_RISK_TYPES:
        return "human", f"高危故障类型「{fault_type}」，必须人工处理"

    # 规则3：模型判定需人工
    if state.get("auto_resolvable") is False:
        reason = state.get("classify_reason") or "分类判定为需人工处理"
        return "human", f"分类判定需人工：{reason}"

    # 规则4：无成熟方案
    if score < threshold:
        return "human", f"未匹配到成熟方案（最高匹配度 {score:.2f} < 阈值 {threshold}）"

    return "auto", f"命中成熟方案（匹配度 {score:.2f} ≥ 阈值 {threshold}），可自动闭环"


def route_after_agent(state: TicketState) -> str:
    """条件边1：agent 节点执行后的路由。

    优先级：仍在工具循环 → 补齐证据 → 自动闭环 / 转人工。
    """
    if has_tool_calls(state):
        return "execute_tools"
    if needs_evidence(state):
        return "gather_evidence"
    return "auto_resolve" if decide(state)[0] == "auto" else "escalate_human"


def route_after_evidence(state: TicketState) -> str:
    """条件边2：证据补齐后的路由。"""
    return "auto_resolve" if decide(state)[0] == "auto" else "escalate_human"