"""LangGraph 节点实现。

节点清单：
- parse_ticket   工单解析（LLM 抽取字段）
- classify       意图 & 故障分类
- agent          Agent 决策中枢（原生 Function Calling）
- execute_tools  执行工具调用（ReAct 循环体）
- gather_evidence 证据补齐（LLM 漏调工具时的确定性兜底）
- auto_resolve   自动闭环分支
- escalate_human 转人工分支
- finalize       收尾落库
"""

import json

from app.agent.routing import decide
from app.agent.state import TicketState, public_snapshot
from app.config import settings
from app.constants import (
    AGENT_CONTEXT_MARKER,
    FAULT_TYPE_OTHER,
    MODE_AUTO,
    MODE_HUMAN,
    NODE_AGENT,
    NODE_AUTO,
    NODE_CLASSIFY,
    NODE_EVIDENCE,
    NODE_FINALIZE,
    NODE_HUMAN,
    NODE_PARSE,
    NODE_TOOLS,
    STATUS_HUMAN,
    STATUS_RESOLVED,
)
from app.llm.client import get_llm_client
from app.llm.schemas import TicketParseResult
from app.logging_config import get_logger
from app.services import ticket_service
from app.services.log_service import write_log
from app.tools.asset_query import run as query_asset
from app.tools.registry import TOOL_SPECS, dispatch
from app.tools.ticket_search import run as search_tickets

logger = get_logger("itops.agent.nodes")

AGENT_SYSTEM_PROMPT = """你是 IT 运维智能工单处理 Agent，负责闭环处理员工提交的故障工单。

工作流程（请严格按顺序）：
1. 调用 search_similar_tickets 检索历史同类型【已解决】工单，判断是否存在可复用的成熟方案；
2. 若工单上下文包含设备编号，调用 get_device_asset 获取设备资产信息；
3. 调用 update_ticket_status 把工单状态置为「处理中」；
4. 证据收集完成后，停止调用工具并简短总结，交由系统做分支判定。

注意：
- 必须调用 search_similar_tickets，它是分支判定的关键依据；
- 不要臆造不存在的参数，不要重复调用同一个工具；
- 只做工具调用与简短说明，不要直接输出最终解决方案。

""" + AGENT_CONTEXT_MARKER + """{context_json}"""


def _context(state: TicketState) -> dict:
    """给模型的工单上下文（mock 模式也从这里还原信息）。"""
    return {
        "ticket_id": state.get("ticket_id"),
        "title": state.get("title"),
        "fault_type": state.get("fault_type"),
        "urgency": state.get("urgency"),
        "device_id": state.get("device_id"),
        "description": state.get("raw_description"),
    }


# --------------------------------------------------------------------------- #
# 1) 工单解析
# --------------------------------------------------------------------------- #
def parse_ticket(state: TicketState) -> dict:
    """调用 LLM 抽取标题、故障类型、紧急等级、设备编号、联系方式，并回写工单。"""
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
    write_log(
        ticket_id,
        NODE_PARSE,
        f"解析完成：类型={result.fault_type}｜等级={result.urgency}｜设备={result.device_id or '无'}",
        detail=result.model_dump(),
    )
    return {
        "title": result.title,
        "fault_type": result.fault_type,
        "urgency": result.urgency,
        "device_id": result.device_id or state.get("device_id"),
        "user_contact": result.user_contact or state.get("user_contact"),
    }


# --------------------------------------------------------------------------- #
# 2) 意图 & 故障分类
# --------------------------------------------------------------------------- #
def classify(state: TicketState) -> dict:
    """判断工单属于「可自动解决」还是「必须人工处理」。"""
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

    write_log(
        ticket_id,
        NODE_CLASSIFY,
        f"分类完成：{'可自动解决' if result.auto_resolvable else '必须人工处理'}｜{result.reason}",
        detail=result.model_dump(),
    )
    return {"auto_resolvable": result.auto_resolvable, "classify_reason": result.reason}


# --------------------------------------------------------------------------- #
# 3) Agent 决策（原生 Function Calling）
# --------------------------------------------------------------------------- #
def agent_node(state: TicketState) -> dict:
    """携带工具声明调用模型，由模型自主决定调用哪些工具。"""
    ticket_id = int(state["ticket_id"])
    iterations = int(state.get("iterations") or 0)

    # 循环上限保护：达到上限后强制不再调用工具，直接进入路由判定
    if iterations >= settings.max_iterations:
        write_log(
            ticket_id,
            NODE_AGENT,
            f"已达最大工具调用轮数({settings.max_iterations})，强制结束工具循环",
            level="WARN",
        )
        return {
            "messages": [{"role": "assistant", "content": "已达最大工具调用轮数，停止调用工具。"}],
            "iterations": iterations + 1,
        }

    system_prompt = AGENT_SYSTEM_PROMPT.format(
        context_json=json.dumps(_context(state), ensure_ascii=False)
    )
    messages = [{"role": "system", "content": system_prompt}] + list(state.get("messages") or [])
    response = get_llm_client().chat_with_tools(messages, TOOL_SPECS)

    assistant_message: dict = {"role": "assistant", "content": response.content or ""}
    tool_calls_payload = [
        {
            "id": call.id,
            "type": "function",
            "function": {"name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False)},
        }
        for call in response.tool_calls
    ]
    if tool_calls_payload:
        assistant_message["tool_calls"] = tool_calls_payload

    if tool_calls_payload:
        names = "、".join(call["function"]["name"] for call in tool_calls_payload)
        write_log(
            ticket_id,
            NODE_AGENT,
            f"第 {iterations + 1} 轮决策：调用工具 [{names}]｜{response.content or ''}",
            detail={"tool_calls": tool_calls_payload, "content": response.content},
        )
    else:
        write_log(
            ticket_id,
            NODE_AGENT,
            f"第 {iterations + 1} 轮决策：不再调用工具｜{response.content or ''}",
            detail={"content": response.content},
        )

    return {"messages": [assistant_message], "iterations": iterations + 1}


# --------------------------------------------------------------------------- #
# 4) 工具执行
# --------------------------------------------------------------------------- #
def execute_tools(state: TicketState) -> dict:
    """执行最近一次 assistant 消息里的全部 tool_calls，并把结果回灌为 tool 消息。"""
    ticket_id = int(state["ticket_id"])
    messages = state.get("messages") or []
    last = messages[-1] if messages else {}
    tool_calls = last.get("tool_calls") or []

    update: dict = {}
    new_messages: list[dict] = []
    status_updates = list(state.get("status_updates") or [])

    for call in tool_calls:
        name = call.get("function", {}).get("name", "")
        raw_args = call.get("function", {}).get("arguments")
        result = dispatch(name, raw_args)

        summary = ""
        if name == "search_similar_tickets":
            matches = result.get("matches") or []
            update["matched_solutions"] = matches
            update["best_match_score"] = float(result.get("best_score") or 0.0)
            summary = f"检索到 {len(matches)} 条历史工单，最高匹配度 {update['best_match_score']:.2f}"
        elif name == "get_device_asset":
            update["device_info"] = result
            summary = (
                f"查到设备 {result.get('device_id')}（{result.get('device_type')}，状态 {result.get('health_status')}）"
                if result.get("found")
                else f"未查到设备：{result.get('device_id')}"
            )
        elif name == "update_ticket_status":
            status_updates.append(
                {"status": result.get("status"), "ok": result.get("ok"), "updated_at": result.get("updated_at")}
            )
            update["status_updates"] = status_updates
            summary = f"工单状态 → {result.get('status')}" if result.get("ok") else f"状态更新失败：{result.get('error')}"
        else:
            summary = str(result)

        write_log(
            ticket_id,
            f"tool:{name}",
            summary,
            level="INFO" if result.get("ok", True) else "WARN",
            detail={"arguments": raw_args, "result": result},
        )
        new_messages.append(
            {
                "role": "tool",
                "tool_call_id": call.get("id"),
                "name": name,
                "content": json.dumps(result, ensure_ascii=False, default=str),
            }
        )

    write_log(ticket_id, NODE_TOOLS, f"本轮共执行 {len(tool_calls)} 次工具调用")
    update["messages"] = new_messages
    return update


# --------------------------------------------------------------------------- #
# 5) 证据补齐（兜底）
# --------------------------------------------------------------------------- #
def gather_evidence(state: TicketState) -> dict:
    """模型未调用检索工具时，用确定性方式补齐历史方案证据。"""
    ticket_id = int(state["ticket_id"])
    update: dict = {}

    if state.get("matched_solutions") is None:
        result = search_tickets(
            fault_type=state.get("fault_type") or FAULT_TYPE_OTHER,
            keywords=state.get("raw_description") or "",
            top_k=3,
        )
        matches = result.get("matches") or []
        update["matched_solutions"] = matches
        update["best_match_score"] = float(result.get("best_score") or 0.0)
        write_log(
            ticket_id,
            NODE_EVIDENCE,
            f"模型未触发历史工单检索，已确定性补齐：命中 {len(matches)} 条，最高匹配度 {update['best_match_score']:.2f}",
            level="WARN",
            detail=result,
        )

    if state.get("device_id") and not state.get("device_info"):
        result = query_asset(state["device_id"])
        update["device_info"] = result
        write_log(ticket_id, NODE_EVIDENCE, f"补齐设备资产查询：{state['device_id']}", detail=result)

    return update


# --------------------------------------------------------------------------- #
# 6) 自动闭环分支
# --------------------------------------------------------------------------- #
def auto_resolve(state: TicketState) -> dict:
    """生成解决步骤并标记「已解决 / 自动闭环」。"""
    ticket_id = int(state["ticket_id"])
    _, reason = decide(state)
    matches = state.get("matched_solutions") or []
    device = state.get("device_info")
    resolution = get_llm_client().generate_resolution(_context(state), matches, device)

    ticket_service.update_status(
        ticket_id,
        STATUS_RESOLVED,
        resolution=resolution,
        resolution_mode=MODE_AUTO,
        decision_reason=reason,
    )
    write_log(
        ticket_id,
        NODE_AUTO,
        f"✅ 自动闭环：工单状态 → 已解决（{reason}）",
        detail={"decision_reason": reason, "matched_ticket": matches[0]["ticket_id"] if matches else None},
    )
    return {
        "decision": "auto",
        "decision_reason": reason,
        "resolution": resolution,
        "final_status": STATUS_RESOLVED,
    }


# --------------------------------------------------------------------------- #
# 7) 转人工分支
# --------------------------------------------------------------------------- #
def escalate_human(state: TicketState) -> dict:
    """分派工程师、生成通知消息并标记「转人工」。"""
    ticket_id = int(state["ticket_id"])
    _, reason = decide(state)
    assignee = ticket_service.pick_engineer(state.get("fault_type") or FAULT_TYPE_OTHER)
    notify_message = get_llm_client().generate_notification(_context(state), assignee or {}, reason)

    ticket_service.update_status(
        ticket_id,
        STATUS_HUMAN,
        resolution_mode=MODE_HUMAN,
        assignee=(assignee or {}).get("name"),
        notify_message=notify_message,
        decision_reason=reason,
    )
    write_log(
        ticket_id,
        NODE_HUMAN,
        f"⚠️ 转人工：工单状态 → 转人工｜原因：{reason}｜分派：{(assignee or {}).get('name', '待定')}",
        level="WARN",
        detail={"decision_reason": reason, "assignee": assignee},
    )
    return {
        "decision": "human",
        "decision_reason": reason,
        "assignee": assignee,
        "notify_message": notify_message,
        "final_status": STATUS_HUMAN,
    }


# --------------------------------------------------------------------------- #
# 8) 收尾
# --------------------------------------------------------------------------- #
def finalize(state: TicketState) -> dict:
    """汇总本次处理结果并落一条收尾日志。"""
    ticket_id = int(state["ticket_id"])
    snapshot = public_snapshot(state)
    write_log(
        ticket_id,
        NODE_FINALIZE,
        f"处理结束：状态={state.get('final_status')}｜模式={'自动闭环' if state.get('decision') == 'auto' else '转人工'}"
        f"｜工具轮数={state.get('iterations')}",
        detail=snapshot,
    )
    return {}