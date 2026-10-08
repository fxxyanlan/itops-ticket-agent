"""收尾 Agent（finalize）：汇总本次处理结果并落一条归档日志。"""

from app.agent.agents.base import trace_entry, write_agent_log
from app.agent.state import TicketState, public_snapshot
from app.constants import NODE_FINALIZE

NAME = "finalize"
LABEL = NODE_FINALIZE
DESCRIPTION = "汇总处理结果并归档（写收尾日志与状态快照）"


def run(state: TicketState) -> dict:
    """写收尾日志，记录最终状态、处理模式与多 Agent 协作轨迹。"""
    ticket_id = int(state["ticket_id"])
    snapshot = public_snapshot(state)
    write_agent_log(
        ticket_id,
        LABEL,
        f"处理结束：状态={state.get('final_status')}"
        f"｜模式={'自动闭环' if state.get('decision') == 'auto' else '转人工'}"
        f"｜工具轮数={state.get('iterations')}"
        f"｜协作 Agent 数={len(state.get('agent_trace') or [])}",
        detail=snapshot,
    )
    return {
        "agent_trace": [trace_entry(LABEL, "归档处理结果", final_status=state.get("final_status"))]
    }