"""多 Agent 协作层的公共基础设施。

每个专职 Agent 都对外暴露统一的 `run(state) -> dict` 接口，只返回对共享状态的增量更新，
由 Supervisor（调度中枢）按确定性策略编排执行顺序，因此 Agent 之间不需要互相直接调用。

公共能力：
- `agent_context()`：构造喂给模型的工单上下文（mock 模式也从这里还原信息）；
- `write_agent_log()`：以 Agent 身份写一条可追溯日志（DB + 文件）；
- `trace_entry()`：生成一条多 Agent 协作轨迹，写入 `state["agent_trace"]`。
"""

from app.agent.state import TicketState
from app.constants import (
    NODE_CRITIC,
    NODE_FINALIZE,
    NODE_INTAKE,
    NODE_RESEARCH,
    NODE_RESOLVER,
    NODE_SUPERVISOR,
    NODE_TRIAGE,
)
from app.services.log_service import write_log

# 调度标识 → 日志/前端展示名
AGENT_LABELS: dict[str, str] = {
    "supervisor": NODE_SUPERVISOR,
    "intake": NODE_INTAKE,
    "triage": NODE_TRIAGE,
    "research": NODE_RESEARCH,
    "resolver": NODE_RESOLVER,
    "critic": NODE_CRITIC,
    "finalize": NODE_FINALIZE,
}


def agent_context(state: TicketState) -> dict:
    """给模型的工单上下文（mock 模式也从这里还原信息）。"""
    return {
        "ticket_id": state.get("ticket_id"),
        "title": state.get("title"),
        "fault_type": state.get("fault_type"),
        "urgency": state.get("urgency"),
        "device_id": state.get("device_id"),
        "description": state.get("raw_description"),
    }


def write_agent_log(
    ticket_id: int,
    label: str,
    message: str,
    level: str = "INFO",
    detail: dict | None = None,
) -> None:
    """以某个 Agent 的身份写一条处理日志。"""
    write_log(int(ticket_id), label, message, level=level, detail=detail)


def trace_entry(agent: str, action: str, **extra) -> dict:
    """构造一条多 Agent 协作轨迹记录（由 reducer 追加到 state['agent_trace']）。"""
    return {"agent": agent, "action": action, **extra}