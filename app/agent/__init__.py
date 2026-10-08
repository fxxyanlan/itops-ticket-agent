"""多 Agent 协作的 LangGraph 状态机：状态、专职 Agent、调度规则、图组装。

- `state.py`：多 Agent 共享状态（TicketState）
- `agents/`：Supervisor + 专职 Agent（intake / triage / research / resolver / critic / finalize）
- `routing.py`：确定性分支判定与 Agent 调度规则
- `graph.py`：Supervisor 中心化的图组装
"""