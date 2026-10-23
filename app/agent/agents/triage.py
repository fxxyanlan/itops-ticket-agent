"""分类 Agent（triage）：判定工单属于「可自动解决」还是「必须人工处理」。"""

from app.agent.agents.base import trace_entry, write_agent_log
from app.agent.state import TicketState
from app.config import settings
from app.constants import FAULT_TYPE_OTHER, NODE_TRIAGE
from app.llm.client import get_llm_client
from app.llm.schemas import TicketParseResult
from app.services.safety_gate import sanitize_for_llm

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
    result = get_llm_client().classify(
        parsed, sanitize_for_llm(state.get("raw_description") or "")
    )

    # 置信度不足时直接按「必须人工」处理，不给自动闭环留机会
    threshold = settings.confidence_threshold
    confidence = round(result.confidence, 2)
    auto_resolvable = result.auto_resolvable
    reason = result.reason
    if confidence < threshold:
        auto_resolvable = False
        reason = (
            f"分类置信度 {confidence:.2f} 低于阈值 {threshold:.2f}，保守转人工；"
            f"模型原判断：{result.reason}"
        )

    write_agent_log(
        ticket_id,
        LABEL,
        f"分类完成：{'可自动解决' if auto_resolvable else '必须人工处理'}"
        f"｜置信度={confidence:.2f}｜{reason}",
        detail={
            **result.model_dump(),
            "final_auto_resolvable": auto_resolvable,
            "classify_confidence": confidence,
            "confidence_threshold": threshold,
        },
    )
    return {
        "auto_resolvable": auto_resolvable,
        "classify_reason": reason,
        "classify_confidence": confidence,
        "agent_trace": [
            trace_entry(
                LABEL,
                "可自动解决性判定",
                auto_resolvable=auto_resolvable,
                confidence=confidence,
            )
        ],
    }