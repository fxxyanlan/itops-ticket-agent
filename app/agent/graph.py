"""LangGraph 图组装：Supervisor 中心化的多 Agent 协作拓扑。

拓扑结构：
    START → supervisor（调度中枢）
    supervisor --(条件边1)--> intake | triage | research | resolver | critic | finalize
    intake / triage / resolver / critic → supervisor          # 专职 Agent 回报调度中枢
    research --(条件边2)--> execute_tools → research           # 检索 Agent 的 ReAct 子循环
    research --(条件边2)--> supervisor
    finalize → END
"""

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from app.agent import routing
from app.agent.agents import critic, finalize, intake, research, resolver, supervisor, triage
from app.agent.state import TicketState

# 条件边1：调度中枢 → 专职 Agent
SUPERVISOR_ROUTE_MAP = {
    "intake": "intake",
    "triage": "triage",
    "research": "research",
    "resolver": "resolver",
    "critic": "critic",
    "finalize": "finalize",
}

# 条件边2：检索 Agent → 工具执行 / 回报调度中枢
RESEARCH_ROUTE_MAP = {
    "execute_tools": "execute_tools",
    "supervisor": "supervisor",
}


def build_graph():
    """构建并编译 LangGraph 状态机。"""
    builder = StateGraph(TicketState)

    # 调度中枢
    builder.add_node("supervisor", supervisor.run)
    # 专职 Agent
    builder.add_node("intake", intake.run)
    builder.add_node("triage", triage.run)
    builder.add_node("research", research.run)
    builder.add_node("execute_tools", research.execute_tools)
    builder.add_node("resolver", resolver.run)
    builder.add_node("critic", critic.run)
    builder.add_node("finalize", finalize.run)

    # 入口：先由调度中枢编排
    builder.add_edge(START, "supervisor")

    # 条件边1：调度中枢分派下一个 Agent
    builder.add_conditional_edges("supervisor", routing.route_supervisor, SUPERVISOR_ROUTE_MAP)

    # 专职 Agent 执行完统一回报调度中枢
    for agent_node in ("intake", "triage", "resolver", "critic"):
        builder.add_edge(agent_node, "supervisor")

    # 条件边2：检索 Agent 内部的 ReAct 子循环
    builder.add_conditional_edges("research", routing.route_after_research, RESEARCH_ROUTE_MAP)
    builder.add_edge("execute_tools", "research")

    # 收尾
    builder.add_edge("finalize", END)

    return builder.compile()


@lru_cache(maxsize=1)
def get_graph():
    """编译后的图（单例，避免重复编译）。"""
    return build_graph()