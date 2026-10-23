"""路由与调度规则：决定「自动闭环 / 转人工」分支，以及多 Agent 的执行顺序。

设计原则：分支判定与 Agent 调度都用代码里的确定性规则，而不是让大模型自己拍板，
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


def _is_low_confidence(value: object) -> bool:
    """置信度低于阈值时返回 True；None 表示尚未判断，非法值按低置信度处理。"""
    if value is None:
        return False
    try:
        return float(value) < settings.confidence_threshold
    except (TypeError, ValueError):
        return True


def decide(state: TicketState) -> tuple[str, str]:
    """返回 (decision, reason)，decision ∈ {auto, human}。

    命中任一「转人工」硬规则即转人工，否则自动闭环。
    """
    urgency = state.get("urgency")
    fault_type = state.get("fault_type") or FAULT_TYPE_OTHER
    threshold = settings.match_threshold
    confidence_threshold = settings.confidence_threshold
    score = float(state.get("best_match_score") or 0.0)

    # 规则1：P0 灾难级
    if urgency == URGENCY_CRITICAL:
        return "human", "紧急等级 P0（灾难级），必须人工立即处理"

    # 规则2：高危故障类型
    if fault_type in HIGH_RISK_TYPES:
        return "human", f"高危故障类型「{fault_type}」，必须人工处理"

    # 规则3：解析置信度不足（防止把高危误判成低危）
    parse_confidence = state.get("parse_confidence")
    if parse_confidence is None or _is_low_confidence(parse_confidence):
        shown = "未提供" if parse_confidence is None else f"{float(parse_confidence):.2f}"
        return "human", f"解析置信度 {shown} 低于阈值 {confidence_threshold:.2f}，保守转人工"

    # 规则4：分类置信度不足
    classify_confidence = state.get("classify_confidence")
    if classify_confidence is not None and _is_low_confidence(classify_confidence):
        return "human", (
            f"分类置信度 {float(classify_confidence):.2f} 低于阈值 "
            f"{confidence_threshold:.2f}，保守转人工"
        )

    # 规则5：模型判定需人工
    if state.get("auto_resolvable") is False:
        reason = state.get("classify_reason") or "分类判定为需人工处理"
        return "human", f"分类判定需人工：{reason}"

    # 规则6：审核未通过
    if state.get("critic_verdict") == "reject":
        feedback = state.get("critic_feedback") or "方案存在未解决的风险"
        return "human", f"审核未通过：{feedback}"

    # 规则7：无成熟方案
    if score < threshold:
        return "human", f"未匹配到成熟方案（最高匹配度 {score:.2f} < 阈值 {threshold}）"

    return "auto", f"命中成熟方案（匹配度 {score:.2f} ≥ 阈值 {threshold}），可自动闭环"


# --------------------------------------------------------------------------- #
# 多 Agent 调度：Supervisor 的确定性编排策略
# --------------------------------------------------------------------------- #
def plan_next(state: TicketState) -> tuple[str, str]:
    """返回 (next_agent, reason)，next_agent ∈ {intake, triage, research, resolver, critic, finalize}。

    按「解析 → 分类 → 检索 → 处置 → 审核 → 收尾」的顺序推进，
    每个分支都以共享状态里的字段是否就绪作为推进条件，天然幂等、不会绕圈。
    """
    # 1) 结构化字段缺失 → 解析 Agent
    if not state.get("fault_type") or not state.get("title"):
        return "intake", "缺少结构化工单字段，先由解析 Agent 抽取"

    # 1.1) 已判定转人工 → 收尾（否则低置信度规则会反复进入处置 Agent）
    if state.get("decision") == "human":
        return "finalize", "已判定转人工，进入收尾归档"

    # 1.2) 解析置信度不足 → 跳过分类和检索，直接进入处置转人工
    if state.get("decision") is None and _is_low_confidence(state.get("parse_confidence")):
        return "resolver", "解析置信度低于阈值，跳过检索直接转人工"

    # 2) 未判定可自动解决性 → 分类 Agent
    if state.get("auto_resolvable") is None:
        return "triage", "尚未判定可自动解决性，交由分类 Agent 判定"

    # 2.1) 分类置信度不足 → 跳过检索，直接进入处置转人工
    if (
        state.get("decision") is None
        and state.get("auto_resolvable") is False
        and _is_low_confidence(state.get("classify_confidence"))
    ):
        return "resolver", "分类置信度低于阈值，跳过检索直接转人工"

    # 3) 缺少历史方案证据 → 检索 Agent
    if needs_evidence(state):
        return "research", "缺少历史方案证据，交由检索 Agent 收集"

    # 4) 尚未产出处置结果 → 处置 Agent
    if state.get("decision") is None:
        return "resolver", "证据就绪，交由处置 Agent 生成处置结果"

    # 5) 自动闭环方案需交叉审核（未通过则退回重做，受重试上限保护）
    if (
        state.get("decision") == "auto"
        and state.get("critic_verdict") != "pass"
        and int(state.get("review_rounds") or 0) <= settings.max_review_rounds
    ):
        return "critic", "自动闭环方案需上线前自检，交由审核 Agent 审核"

    # 6) 全部就绪 → 收尾归档
    return "finalize", "各 Agent 已完成，进入收尾归档"


def route_supervisor(state: TicketState) -> str:
    """条件边：调度中枢决定下一个要执行的 Agent 节点。"""
    return state.get("next_agent") or "finalize"


def route_after_research(state: TicketState) -> str:
    """条件边：检索 Agent 之后 —— 仍在工具循环则执行工具，否则交回调度中枢。"""
    return "execute_tools" if has_tool_calls(state) else "supervisor"