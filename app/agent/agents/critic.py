"""审核 Agent（critic）：对自动闭环方案做交叉审核（自检）。

通过 → 交回调度中枢进入收尾；
未通过 → 清空 decision / resolution，退回处置 Agent 重新生成（最多 settings.max_review_rounds 次）。
审核轮次写入 state['review_rounds']，用于限制重试上限、避免死循环。
"""

from app.agent.agents.base import agent_context, trace_entry, write_agent_log
from app.agent.state import TicketState
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

    if result.verdict == "pass":
        write_agent_log(
            ticket_id,
            LABEL,
            f"✅ 审核通过（第 {rounds} 轮）：{result.feedback}",
            detail=result.model_dump(),
        )
        return {
            "critic_verdict": "pass",
            "critic_feedback": result.feedback,
            "review_rounds": rounds,
            "agent_trace": [trace_entry(LABEL, "审核通过", round=rounds)],
        }

    write_agent_log(
        ticket_id,
        LABEL,
        f"⚠️ 审核未通过（第 {rounds} 轮）：{result.feedback}，退回处置 Agent 重新生成",
        level="WARN",
        detail=result.model_dump(),
    )
    return {
        "critic_verdict": "reject",
        "critic_feedback": result.feedback,
        "review_rounds": rounds,
        # 清空决策与方案，触发处置 Agent 重新生成
        "decision": None,
        "resolution": None,
        "agent_trace": [
            trace_entry(LABEL, "审核未通过并退回重做", round=rounds, feedback=result.feedback)
        ],
    }