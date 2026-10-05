"""工具3：更新工单状态（新建 / 处理中 / 已解决 / 转人工）。

这是 Agent 会主动调用的写操作工具，用于体现「工单状态真实流转」。
"""

from app.constants import ALL_STATUSES
from app.db.base import session_scope
from app.db.models import Ticket
from app.services.log_service import write_log

SPEC = {
    "type": "function",
    "function": {
        "name": "update_ticket_status",
        "description": "更新工单状态并追加处理备注。状态流转：新建 → 处理中 → 已解决 / 转人工",
        "parameters": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "integer", "description": "工单编号"},
                "status": {
                    "type": "string",
                    "enum": ALL_STATUSES,
                    "description": "目标状态：新建/处理中/已解决/转人工",
                },
                "note": {"type": "string", "description": "状态变更备注"},
            },
            "required": ["ticket_id", "status"],
        },
    },
}


def run(ticket_id: int, status: str, note: str | None = None) -> dict:
    """工具入口：校验状态合法性并落库。"""
    if status not in ALL_STATUSES:
        return {"ok": False, "error": f"非法状态：{status}，可选 {ALL_STATUSES}"}
    if ticket_id is None:
        return {"ok": False, "error": "缺少 ticket_id"}

    with session_scope() as session:
        ticket = session.get(Ticket, int(ticket_id))
        if ticket is None:
            return {"ok": False, "error": f"工单不存在：{ticket_id}"}

        ticket.status = status
        # 触发 onupdate 更新 updated_at
        session.add(ticket)
        session.flush()

        write_log(
            ticket_id=ticket.id,
            node="tool:update_ticket_status",
            message=f"工单状态更新为「{status}」" + (f"（{note}）" if note else ""),
            detail={"status": status, "note": note},
            session=session,
        )
        return {
            "ok": True,
            "ticket_id": ticket.id,
            "status": ticket.status,
            "updated_at": ticket.updated_at.isoformat(),
        }