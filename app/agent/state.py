"""LangGraph 状态定义（多 Agent 共享状态）。

messages 使用 OpenAI 原生消息格式（dict 列表）而不是 LangChain 消息对象，
这样可以直接喂给 DeepSeek 的 `tools` 接口，体现「原生 Function Calling」。

agent_trace 记录各专职 Agent 的协作轨迹，供前端与日志观测多 Agent 协作过程。
"""

import operator
from typing import Annotated, Any, TypedDict


class TicketState(TypedDict, total=False):
    """工单处理全流程状态。"""

    # --- 输入 ---
    ticket_id: int
    raw_description: str
    user_contact: str | None

    # --- 工单解析结果 ---
    title: str | None
    fault_type: str | None
    urgency: str | None
    device_id: str | None
    parse_confidence: float | None  # 解析判断的置信度（0~1）
    urgency_source: str | None  # 最终等级来源：模型 / 规则升级 / 模型+规则一致

    # --- 分类结果 ---
    auto_resolvable: bool | None
    classify_reason: str | None
    classify_confidence: float | None  # 分类判断的置信度（0~1）

    # --- 入口规则判级 ---
    rule_urgency: str | None
    rule_confidence: float | None
    rule_matched_keywords: list[str] | None

    # --- 工具证据 ---
    matched_solutions: list[dict] | None  # 工具1：历史相似工单（None=未调用，[]=调用但无命中）
    best_match_score: float
    device_info: dict | None  # 工具2：设备资产
    status_updates: list[dict]  # 工具3：状态变更记录

    # --- 决策与产出 ---
    decision: str | None  # auto | human
    decision_reason: str | None
    resolution: str | None
    assignee: dict | None
    notify_message: str | None
    final_status: str | None

    # --- 多 Agent 协作（Supervisor 编排）---
    next_agent: str | None  # 调度中枢决定的下一步 agent
    agent_trace: Annotated[list[dict], operator.add]  # 各 Agent 的协作轨迹
    critic_verdict: str | None  # 审核结论：pass | reject
    critic_feedback: str | None
    review_confidence: float | None  # 审核判断的置信度（0~1）
    review_rounds: int  # 审核轮次（用于限制重试上限）

    # --- ReAct 循环控制 ---
    messages: Annotated[list[dict], operator.add]
    iterations: int
    errors: list[str]


def initial_state(ticket: dict) -> TicketState:
    """由工单记录构造初始状态。"""
    return TicketState(
        ticket_id=ticket["id"],
        raw_description=ticket["description"],
        user_contact=ticket.get("user_contact"),
        device_id=ticket.get("device_id"),
        matched_solutions=None,
        best_match_score=0.0,
        device_info=None,
        status_updates=[],
        next_agent=None,
        agent_trace=[],
        critic_verdict=None,
        critic_feedback=None,
        review_confidence=None,
        review_rounds=0,
        messages=[],
        iterations=0,
        errors=[],
    )


def public_snapshot(state: dict) -> dict[str, Any]:
    """裁剪出可序列化的状态快照，便于打印 / 存日志（剔除 messages 原文）。"""
    keys = (
        "ticket_id",
        "title",
        "fault_type",
        "urgency",
        "urgency_source",
        "device_id",
        "rule_urgency",
        "rule_confidence",
        "rule_matched_keywords",
        "parse_confidence",
        "auto_resolvable",
        "classify_reason",
        "classify_confidence",
        "review_confidence",
        "best_match_score",
        "decision",
        "decision_reason",
        "final_status",
        "critic_verdict",
        "review_rounds",
        "iterations",
    )
    snapshot = {key: state.get(key) for key in keys}
    snapshot["matched_count"] = len(state.get("matched_solutions") or [])
    snapshot["device_found"] = bool((state.get("device_info") or {}).get("found"))
    snapshot["agent_trace"] = state.get("agent_trace") or []
    return snapshot