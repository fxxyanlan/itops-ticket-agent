"""处置 Agent（resolver）：按确定性规则产出最终结果。

- 自动闭环：依据检索到的历史方案 + 设备信息生成解决步骤，工单置为「已解决」；
- 转人工：分派工程师、生成通知消息，工单置为「转人工」。

分支判定复用 `app.agent.routing.decide`（代码硬规则），保证结果可解释、可复现。
"""

from app.agent.agents.base import agent_context, trace_entry, write_agent_log
from app.agent.routing import decide
from app.agent.state import TicketState
from app.constants import (
    FAULT_TYPE_OTHER,
    MODE_AUTO,
    MODE_HUMAN,
    NODE_RESOLVER,
    STATUS_HUMAN,
    STATUS_RESOLVED,
)
from app.llm.client import get_llm_client
from app.services import ticket_service

NAME = "resolver"
LABEL = NODE_RESOLVER
DESCRIPTION = "根据确定性判定生成最终处置结果：自动闭环方案 或 转人工派单通知"


def run(state: TicketState) -> dict:
    """执行处置：自动闭环或转人工。"""
    decision, reason = decide(state)
    if decision == "auto":
        return _auto_resolve(state, reason)
    return _escalate_human(state, reason)


def _auto_resolve(state: TicketState, reason: str) -> dict:
    """生成解决步骤并标记「已解决 / 自动闭环」。"""
    ticket_id = int(state["ticket_id"])
    matches = state.get("matched_solutions") or []
    device = state.get("device_info")
    resolution = get_llm_client().generate_resolution(agent_context(state), matches, device)

    ticket_service.update_status(
        ticket_id,
        STATUS_RESOLVED,
        resolution=resolution,
        resolution_mode=MODE_AUTO,
        decision_reason=reason,
    )
    write_agent_log(
        ticket_id,
        LABEL,
        f"✅ 自动闭环：工单状态 → 已解决（{reason}）",
        detail={
            "decision_reason": reason,
            "matched_ticket": matches[0]["ticket_id"] if matches else None,
            "parse_confidence": state.get("parse_confidence"),
            "classify_confidence": state.get("classify_confidence"),
            "review_confidence": state.get("review_confidence"),
        },
    )
    return {
        "decision": "auto",
        "decision_reason": reason,
        "resolution": resolution,
        "final_status": STATUS_RESOLVED,
        "agent_trace": [trace_entry(LABEL, "生成自动闭环方案", matched=len(matches))],
    }


def _escalate_human(state: TicketState, reason: str) -> dict:
    """分派工程师、生成通知消息并标记「转人工」。"""
    ticket_id = int(state["ticket_id"])
    assignee = ticket_service.pick_engineer(state.get("fault_type") or FAULT_TYPE_OTHER)
    notify_message = get_llm_client().generate_notification(agent_context(state), assignee or {}, reason)

    ticket_service.update_status(
        ticket_id,
        STATUS_HUMAN,
        resolution_mode=MODE_HUMAN,
        assignee=(assignee or {}).get("name"),
        notify_message=notify_message,
        decision_reason=reason,
    )
    write_agent_log(
        ticket_id,
        LABEL,
        f"⚠️ 转人工：工单状态 → 转人工｜原因：{reason}｜分派：{(assignee or {}).get('name', '待定')}",
        level="WARN",
        detail={
            "decision_reason": reason,
            "assignee": assignee,
            "parse_confidence": state.get("parse_confidence"),
            "classify_confidence": state.get("classify_confidence"),
        },
    )
    return {
        "decision": "human",
        "decision_reason": reason,
        "assignee": assignee,
        "notify_message": notify_message,
        "final_status": STATUS_HUMAN,
        "agent_trace": [trace_entry(LABEL, "转人工派单", assignee=(assignee or {}).get("name"))],
    }