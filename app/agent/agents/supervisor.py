"""调度中枢（supervisor）：多 Agent 协作的编排者。

设计原则：调度决策全部由 `app.agent.routing.plan_next` 的确定性代码规则给出，
而不是让大模型自由路由 —— 保证多 Agent 协作过程可解释、可复现、可测试，
也让离线 mock 模式下的执行顺序完全确定。
"""

from app.agent.agents.base import AGENT_LABELS, trace_entry, write_agent_log
from app.agent.routing import plan_next
from app.agent.state import TicketState
from app.constants import NODE_SUPERVISOR

NAME = "supervisor"
LABEL = NODE_SUPERVISOR
DESCRIPTION = "按确定性策略编排各专职 Agent 的执行顺序"


def run(state: TicketState) -> dict:
    """计算下一个应执行的 Agent，并记录调度决策。"""
    ticket_id = int(state["ticket_id"])
    next_agent, reason = plan_next(state)
    write_agent_log(
        ticket_id,
        LABEL,
        f"调度决策 → {AGENT_LABELS.get(next_agent, next_agent)}｜{reason}",
        detail={"next_agent": next_agent, "reason": reason},
    )
    return {
        "next_agent": next_agent,
        "agent_trace": [trace_entry(LABEL, f"调度 → {AGENT_LABELS.get(next_agent, next_agent)}", reason=reason)],
    }