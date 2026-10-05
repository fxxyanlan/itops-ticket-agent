"""LangGraph 图组装：把节点与条件边连成完整的状态机。"""

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from app.agent import nodes
from app.agent.routing import route_after_agent, route_after_evidence
from app.agent.state import TicketState

# 条件边1：agent 之后的分支映射
ROUTE_MAP = {
    "execute_tools": "execute_tools",
    "gather_evidence": "gather_evidence",
    "auto_resolve": "auto_resolve",
    "escalate_human": "escalate_human",
}

# 条件边2：证据补齐之后的分支映射
EVIDENCE_ROUTE_MAP = {
    "auto_resolve": "auto_resolve",
    "escalate_human": "escalate_human",
}


def build_graph():
    """构建并编译 LangGraph 状态机。

    图结构：
        START → parse_ticket → classify → agent
        agent --(有 tool_calls)--> execute_tools --> agent      # ReAct 循环
        agent --(条件边1)--> gather_evidence | auto_resolve | escalate_human
        gather_evidence --(条件边2)--> auto_resolve | escalate_human
        auto_resolve / escalate_human → finalize → END
    """
    builder = StateGraph(TicketState)

    # 注册节点
    builder.add_node("parse_ticket", nodes.parse_ticket)
    builder.add_node("classify", nodes.classify)
    builder.add_node("agent", nodes.agent_node)
    builder.add_node("execute_tools", nodes.execute_tools)
    builder.add_node("gather_evidence", nodes.gather_evidence)
    builder.add_node("auto_resolve", nodes.auto_resolve)
    builder.add_node("escalate_human", nodes.escalate_human)
    builder.add_node("finalize", nodes.finalize)

    # 线性边
    builder.add_edge(START, "parse_ticket")
    builder.add_edge("parse_ticket", "classify")
    builder.add_edge("classify", "agent")

    # 条件边1：工具循环 / 补证据 / 分支路由
    builder.add_conditional_edges("agent", route_after_agent, ROUTE_MAP)
    # ReAct 循环：工具执行后回到 agent 继续决策
    builder.add_edge("execute_tools", "agent")

    # 条件边2：补完证据后二选一
    builder.add_conditional_edges("gather_evidence", route_after_evidence, EVIDENCE_ROUTE_MAP)

    # 两条终态分支统一收尾
    builder.add_edge("auto_resolve", "finalize")
    builder.add_edge("escalate_human", "finalize")
    builder.add_edge("finalize", END)

    return builder.compile()


@lru_cache(maxsize=1)
def get_graph():
    """编译后的图（单例，避免重复编译）。"""
    return build_graph()