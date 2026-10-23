"""解析 Agent（intake）：把自然语言工单描述结构化为工单字段。"""

from app.agent.agents.base import trace_entry, write_agent_log
from app.agent.state import TicketState
from app.constants import NODE_INTAKE
from app.llm.client import get_llm_client
from app.services import ticket_service
from app.services.safety_gate import assess_urgency, level_rank, more_severe

NAME = "intake"
LABEL = NODE_INTAKE
DESCRIPTION = "解析自然语言工单，抽取标题/故障类型/紧急等级/设备编号/联系方式"


def run(state: TicketState) -> dict:
    """调用 LLM 抽取字段并回写工单。"""
    ticket_id = int(state["ticket_id"])
    description = state.get("raw_description") or ""
    result = get_llm_client().extract_ticket(description, state.get("user_contact"))

    # 入口规则与模型各判一次：取更严重的一档（只升不降）
    assessment = assess_urgency(description)
    final_urgency = more_severe(assessment.level, result.urgency)
    if level_rank(assessment.level) < level_rank(result.urgency):
        urgency_source = "规则升级"
        parse_confidence = max(result.confidence, assessment.confidence)
    elif assessment.level == result.urgency:
        urgency_source = "模型+规则一致"
        parse_confidence = result.confidence
    else:
        urgency_source = "模型"
        parse_confidence = result.confidence
    parse_confidence = round(parse_confidence, 2)

    ticket_service.update_parsed_fields(
        ticket_id,
        title=result.title,
        fault_type=result.fault_type,
        urgency=final_urgency,
        device_id=result.device_id,
        user_contact=result.user_contact,
    )
    write_agent_log(
        ticket_id,
        LABEL,
        (
            f"解析完成：类型={result.fault_type}｜等级={final_urgency}（{urgency_source}）"
            f"｜置信度={parse_confidence:.2f}｜设备={result.device_id or '无'}"
        ),
        detail={
            **result.model_dump(),
            "final_urgency": final_urgency,
            "urgency_source": urgency_source,
            "parse_confidence": parse_confidence,
            "rule_urgency": assessment.level,
            "rule_confidence": assessment.confidence,
            "rule_matched_keywords": list(assessment.matched_keywords),
        },
    )
    return {
        "title": result.title,
        "fault_type": result.fault_type,
        "urgency": final_urgency,
        "urgency_source": urgency_source,
        "parse_confidence": parse_confidence,
        "rule_urgency": assessment.level,
        "rule_confidence": assessment.confidence,
        "rule_matched_keywords": list(assessment.matched_keywords),
        "device_id": result.device_id or state.get("device_id"),
        "user_contact": result.user_contact or state.get("user_contact"),
        "agent_trace": [
            trace_entry(
                LABEL,
                "结构化解析",
                fault_type=result.fault_type,
                urgency=final_urgency,
                confidence=parse_confidence,
            )
        ],
    }
