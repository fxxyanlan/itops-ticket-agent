"""审核 Agent（critic）：对自动闭环方案做交叉审核（自检）。

通过 → 交回调度中枢进入收尾；
未通过 → 清空 decision / resolution，退回处置 Agent 重新生成（最多 settings.max_review_rounds 次）。
审核轮次写入 state['review_rounds']，用于限制重试上限、避免死循环。
"""

from app.agent.agents.base import agent_context, trace_entry, write_agent_log
from app.agent.state import TicketState
from app.config import settings
from app.constants import NODE_CRITIC
from app.llm.client import get_llm_client

NAME = "critic"
LABEL = NODE_CRITIC
DESCRIPTION = "对自动闭环方案做上线前自检，未通过则退回处置 Agent 重做"


def run(state: TicketState) -> dict:
    """审核当前自动闭环方案。"""
    ticket_id = int(state["ticket_id"])
    matches = state.get("matched_solutions") or []
    device = state.get("device_info")
    resolution = state.get("resolution") or ""
    rounds = int(state.get("review_rounds") or 0) + 1

    result = get_llm_client().review_resolution(agent_context(state), resolution, matches, device)

    # 审核置信度不足时，即使模型说 pass 也按未通过处理，避免低把握方案自动上线
    threshold = settings.confidence_threshold
    confidence = round(result.confidence, 2)
    verdict = result.verdict
    feedback = result.feedback
    if verdict == "pass" and confidence < threshold:
        verdict = "reject"
        feedback = (
            f"审核置信度 {confidence:.2f} 低于阈值 {threshold:.2f}，按未通过处理；"
            f"{result.feedback}"
        )

    detail = {
        **result.model_dump(),
        "final_verdict": verdict,
        "review_confidence": confidence,
        "confidence_threshold": threshold,
    }
    if verdict == "pass":
        write_agent_log(
            ticket_id,
            LABEL,
            f"✅ 审核通过（第 {rounds} 轮）：{feedback}",
            detail=detail,
        )
        return {
            "critic_verdict": "pass",
            "critic_feedback": feedback,
            "review_confidence": confidence,
            "review_rounds": rounds,
            "agent_trace": [trace_entry(LABEL, "审核通过", round=rounds, confidence=confidence)],
        }

    write_agent_log(
        ticket_id,
        LABEL,
        f"⚠️ 审核未通过（第 {rounds} 轮）：{feedback}，退回处置 Agent 重新生成",
        level="WARN",
        detail=detail,
    )
    return {
        "critic_verdict": "reject",
        "critic_feedback": feedback,
        "review_confidence": confidence,
        "review_rounds": rounds,
        # 清空决策与方案，触发处置 Agent 重新生成
        "decision": None,
        "resolution": None,
        "agent_trace": [
            trace_entry(
                LABEL,
                "审核未通过并退回重做",
                round=rounds,
                feedback=feedback,
                confidence=confidence,
            )
        ],
    }