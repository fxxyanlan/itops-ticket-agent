"""解析 Agent（intake）：把自然语言工单描述结构化为工单字段。"""

from app.agent.agents.base import trace_entry, write_agent_log
from app.agent.state import TicketState
from app.constants import NODE_INTAKE
from app.llm.client import get_llm_client
from app.services import ticket_service

NAME = "intake"
LABEL = NODE_INTAKE
DESCRIPTION = "解析自然语言工单，抽取标题/故障类型/紧急等级/设备编号/联系方式"


def run(state: TicketState) -> dict:
    """调用 LLM 抽取字段并回写工单。"""
    ticket_id = int(state["ticket_id"])
    description = state.get("raw_description") or ""
    result = get_llm_client().extract_ticket(description, state.get("user_contact"))

    ticket_service.update_parsed_fields(
        ticket_id,
        title=result.title,
        fault_type=result.fault_type,
        urgency=result.urgency,
        device_id=result.device_id,
        user_contact=result.user_contact,
    )
    write_agent_log(
        ticket_id,
        LABEL,
        f"解析完成：类型={result.fault_type}｜等级={result.urgency}｜设备={result.device_id or '无'}",
        detail=result.model_dump(),
    )
    return {
        "title": result.title,
        "fault_type": result.fault_type,
        "urgency": result.urgency,
        "device_id": result.device_id or state.get("device_id"),
        "user_contact": result.user_contact or state.get("user_contact"),
        "agent_trace": [
            trace_entry(LABEL, "结构化解析", fault_type=result.fault_type, urgency=result.urgency)
        ],
    }