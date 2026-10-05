"""Agent 执行封装：把工单交给 LangGraph 状态机跑完整闭环。"""

from app.agent.graph import get_graph
from app.agent.state import initial_state, public_snapshot
from app.db.base import init_db
from app.services import ticket_service
from app.services.log_service import write_log


def run_agent(ticket_id: int) -> dict:
    """执行一次完整的 Agent 处理流程，返回最新工单与状态快照。

    同步执行：调用方（API / 脚本）在返回时即可拿到终态结果。
    """
    ticket = ticket_service.get_ticket(ticket_id)
    if ticket is None:
        raise ValueError(f"工单不存在：{ticket_id}")

    init_db()

    state = initial_state(ticket)
    write_log(
        ticket_id,
        "启动",
        "Agent 开始处理工单（LangGraph 状态机启动）",
        detail={"description": ticket.get("description")},
    )

    # recursion_limit 需要容纳 ReAct 循环 + 分支节点，设为较大值
    result = get_graph().invoke(state, config={"recursion_limit": 60})

    refreshed = ticket_service.get_ticket(ticket_id) or ticket
    return {
        "ticket": refreshed,
        "state": public_snapshot(result),
        "resolution": refreshed.get("resolution"),
        "notify_message": refreshed.get("notify_message"),
    }