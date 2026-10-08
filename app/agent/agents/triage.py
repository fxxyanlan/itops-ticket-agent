"""分类 Agent（triage）：判定工单属于「可自动解决」还是「必须人工处理」。"""

from app.agent.agents.base import trace_entry, write_agent_log
from app.agent.state import TicketState
from app.constants import FAULT_TYPE_OTHER, NODE_TRIAGE
from app.llm.client import get_llm_client
from app.llm.schemas import TicketParseResult

NAME = "triage"
LABEL = NODE_TRIAGE
DESCRIPTION = "判定工单可自动解决性，作为后续自动闭环 / 转人工的输入"


def run(state: TicketState) -> dict:
    """调用 LLM 做可自动解决性判定，并写回判定理由。"""
    ticket_id = int(state["ticket_id"])
    parsed = TicketParseResult(
        title=state.get("title") or "",
        fault_type=state.get("fault_type") or FAULT_TYPE_OTHER,
        urgency=state.get("urgency") or "P2",
        device_id=state.get("device_id"),
        user_contact=state.get("user_contact"),
        summary=(state.get("raw_description") or "")[:60],
    )
    result = get_llm_client().classify(parsed, state.get("raw_description") or "")

    write_agent_log(
        ticket_id,
        LABEL,
        f"分类完成：{'可自动解决' if result.auto_resolvable else '必须人工处理'}｜{result.reason}",
        detail=result.model_dump(),
    )
    return {
        "auto_resolvable": result.auto_resolvable,
        "classify_reason": result.reason,
        "agent_trace": [
            trace_entry(LABEL, "可自动解决性判定", auto_resolvable=result.auto_resolvable)
        ],
    }