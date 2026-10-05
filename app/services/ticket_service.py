"""工单服务：工单 CRUD、状态流转、工程师分派。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import STATUS_NEW
from app.db.base import session_scope
from app.db.models import Engineer, Ticket


def _to_dict(ticket: Ticket) -> dict:
    """把 ORM 对象转为纯字典，避免会话关闭后属性失效。"""
    return {
        "id": ticket.id,
        "title": ticket.title,
        "description": ticket.description,
        "fault_type": ticket.fault_type,
        "urgency": ticket.urgency,
        "device_id": ticket.device_id,
        "user_contact": ticket.user_contact,
        "status": ticket.status,
        "resolution_mode": ticket.resolution_mode,
        "resolution": ticket.resolution,
        "assignee": ticket.assignee,
        "notify_message": ticket.notify_message,
        "decision_reason": ticket.decision_reason,
        "created_at": ticket.created_at.isoformat(),
        "updated_at": ticket.updated_at.isoformat(),
    }


def create_ticket(description: str, user_contact: str | None = None, device_id: str | None = None) -> dict:
    """创建工单，初始状态「新建」。"""
    with session_scope() as session:
        ticket = Ticket(
            description=description,
            user_contact=user_contact,
            device_id=device_id,
            status=STATUS_NEW,
            title=description.strip()[:24],
        )
        session.add(ticket)
        session.flush()
        return _to_dict(ticket)


def get_ticket(ticket_id: int) -> dict | None:
    """查询单个工单。"""
    with session_scope() as session:
        ticket = session.get(Ticket, int(ticket_id))
        return _to_dict(ticket) if ticket else None


def list_tickets(status: str | None = None, limit: int = 100) -> list[dict]:
    """按状态过滤查询工单列表（按创建时间倒序）。"""
    with session_scope() as session:
        stmt = select(Ticket).order_by(Ticket.id.desc()).limit(max(1, int(limit)))
        if status:
            stmt = (
                select(Ticket)
                .where(Ticket.status == status)
                .order_by(Ticket.id.desc())
                .limit(max(1, int(limit)))
            )
        return [_to_dict(row) for row in session.scalars(stmt).all()]


def update_parsed_fields(ticket_id: int, **fields) -> dict | None:
    """把解析结果（标题/类型/等级/设备号/联系方式）回写工单。"""
    allowed = {"title", "fault_type", "urgency", "device_id", "user_contact"}
    with session_scope() as session:
        ticket = session.get(Ticket, int(ticket_id))
        if ticket is None:
            return None
        for key, value in fields.items():
            if key in allowed and value is not None:
                setattr(ticket, key, value)
        session.add(ticket)
        session.flush()
        return _to_dict(ticket)


def update_status(ticket_id: int, status: str, **extra) -> dict | None:
    """更新状态，并可选写入 resolution / assignee / notify_message / resolution_mode 等。"""
    allowed_extra = {
        "resolution",
        "resolution_mode",
        "assignee",
        "notify_message",
        "decision_reason",
    }
    with session_scope() as session:
        ticket = session.get(Ticket, int(ticket_id))
        if ticket is None:
            return None
        ticket.status = status
        for key, value in extra.items():
            if key in allowed_extra and value is not None:
                setattr(ticket, key, value)
        session.add(ticket)
        session.flush()
        return _to_dict(ticket)


def pick_engineer(fault_type: str) -> dict | None:
    """按故障类型专长分派工程师；无匹配专长时回退到当前负载最低者。"""
    with session_scope() as session:
        engineer = session.scalar(
            select(Engineer)
            .where(Engineer.specialty == fault_type)
            .order_by(Engineer.active_tickets.asc())
        )
        if engineer is None:
            engineer = session.scalar(select(Engineer).order_by(Engineer.active_tickets.asc()))
        if engineer is None:
            return None
        return {
            "name": engineer.name,
            "specialty": engineer.specialty,
            "level": engineer.level,
            "contact": engineer.contact,
            "avg_hours": engineer.avg_hours,
        }


def list_devices(limit: int = 200) -> list[dict]:
    """设备资产列表（前端辅助查看）。"""
    from app.db.models import DeviceAsset

    with session_scope() as session:
        rows = session.scalars(select(DeviceAsset).limit(limit)).all()
        return [
            {
                "device_id": r.device_id,
                "hostname": r.hostname,
                "device_type": r.device_type,
                "os": r.os,
                "ip": r.ip,
                "location": r.location,
                "owner": r.owner,
                "warranty_until": r.warranty_until,
                "health_status": r.health_status,
            }
            for r in rows
        ]


def list_logs(ticket_id: int) -> list[dict]:
    """查询某工单的 Agent 处理日志（按时间正序）。"""
    from app.db.models import AgentLog

    with session_scope() as session:
        rows = session.scalars(
            select(AgentLog).where(AgentLog.ticket_id == int(ticket_id)).order_by(AgentLog.id.asc())
        ).all()
        return [
            {
                "id": r.id,
                "ticket_id": r.ticket_id,
                "node": r.node,
                "level": r.level,
                "message": r.message,
                "detail": r.detail,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]