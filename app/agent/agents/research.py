"""检索 Agent（research）：原生 Function Calling 收集证据 + 确定性证据补齐。

本 Agent 内部是一个 ReAct 子循环：
    research --(模型要求调用工具)--> execute_tools --> research
    research --(模型不再调用工具)--> Supervisor
循环上限由 settings.max_iterations 保护。

兜底设计：模型如果漏调检索工具，本 Agent 会在循环结束时用确定性方式补齐
历史方案与设备资产证据，保证「自动闭环 / 转人工」的判定始终有依据。
"""

import json

from app.agent.agents.base import agent_context, trace_entry, write_agent_log
from app.agent.state import TicketState
from app.config import settings
from app.constants import AGENT_CONTEXT_MARKER, FAULT_TYPE_OTHER, NODE_RESEARCH, NODE_TOOLS
from app.llm.client import get_llm_client
from app.tools.asset_query import run as query_asset
from app.tools.registry import TOOL_SPECS, dispatch
from app.tools.ticket_search import run as search_tickets

NAME = "research"
LABEL = NODE_RESEARCH
DESCRIPTION = "检索历史同类型工单与设备资产，为分支判定收集证据"

RESEARCH_SYSTEM_PROMPT = """你是 IT 运维多 Agent 系统中的「检索 Agent」，只负责为后续处置收集证据。

工作流程（请严格按顺序）：
1. 调用 search_similar_tickets 检索历史同类型【已解决】工单，判断是否存在可复用的成熟方案；
2. 若工单上下文包含设备编号，调用 get_device_asset 获取设备资产信息；
3. 调用 update_ticket_status 把工单状态置为「处理中」；
4. 证据收集完成后，停止调用工具并简短总结，交由调度中枢做下一步编排。

注意：
- 必须调用 search_similar_tickets，它是分支判定的关键依据；
- 不要臆造不存在的参数，不要重复调用同一个工具；
- 只做工具调用与简短说明，不要直接输出最终解决方案。

""" + AGENT_CONTEXT_MARKER + """{context_json}"""


def _backfill_evidence(state: TicketState, ticket_id: int) -> dict:
    """模型未主动调用检索工具时的确定性兜底。"""
    update: dict = {}

    if state.get("matched_solutions") is None:
        result = search_tickets(
            fault_type=state.get("fault_type") or FAULT_TYPE_OTHER,
            keywords=state.get("raw_description") or "",
            top_k=3,
        )
        matches = result.get("matches") or []
        score = float(result.get("best_score") or 0.0)
        update["matched_solutions"] = matches
        update["best_match_score"] = score
        write_agent_log(
            ticket_id,
            LABEL,
            f"模型未触发历史工单检索，已确定性补齐：命中 {len(matches)} 条，最高匹配度 {score:.2f}",
            level="WARN",
            detail=result,
        )

    if state.get("device_id") and not state.get("device_info"):
        result = query_asset(state["device_id"])
        update["device_info"] = result
        write_agent_log(ticket_id, LABEL, f"补齐设备资产查询：{state['device_id']}", detail=result)

    return update


def run(state: TicketState) -> dict:
    """携带工具声明调用模型，由模型自主决定调用哪些工具。"""
    ticket_id = int(state["ticket_id"])
    iterations = int(state.get("iterations") or 0)

    # 循环上限保护：达到上限后强制不再调用工具，直接补齐证据并交回调度中枢
    if iterations >= settings.max_iterations:
        write_agent_log(
            ticket_id,
            LABEL,
            f"已达最大工具调用轮数({settings.max_iterations})，强制结束工具循环",
            level="WARN",
        )
        update = {
            "messages": [{"role": "assistant", "content": "已达最大工具调用轮数，停止调用工具。"}],
            "iterations": iterations + 1,
            "agent_trace": [trace_entry(LABEL, "达到工具轮数上限，结束检索")],
        }
        update.update(_backfill_evidence(state, ticket_id))
        return update

    system_prompt = RESEARCH_SYSTEM_PROMPT.format(
        context_json=json.dumps(agent_context(state), ensure_ascii=False)
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
        names = "、".join(call["function"]["name"] for call in tool_calls_payload)
        write_agent_log(
            ticket_id,
            LABEL,
            f"第 {iterations + 1} 轮决策：调用工具 [{names}]｜{response.content or ''}",
            detail={"tool_calls": tool_calls_payload, "content": response.content},
        )
        return {
            "messages": [assistant_message],
            "iterations": iterations + 1,
            "agent_trace": [
                trace_entry(LABEL, "请求调用工具", round=iterations + 1, tools=[
                    call["function"]["name"] for call in tool_calls_payload
                ])
            ],
        }

    write_agent_log(
        ticket_id,
        LABEL,
        f"第 {iterations + 1} 轮决策：不再调用工具｜{response.content or ''}",
        detail={"content": response.content},
    )
    update = {
        "messages": [assistant_message],
        "iterations": iterations + 1,
        "agent_trace": [trace_entry(LABEL, "检索完成", round=iterations + 1)],
    }
    update.update(_backfill_evidence(state, ticket_id))
    return update


# --------------------------------------------------------------------------- #
# ReAct 子循环体：工具执行（由条件边 route_after_research 触发）
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

        write_agent_log(
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

    write_agent_log(ticket_id, NODE_TOOLS, f"本轮共执行 {len(tool_calls)} 次工具调用")
    update["messages"] = new_messages
    return update