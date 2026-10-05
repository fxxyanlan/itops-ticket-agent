"""ORM 模型：工单、Agent 日志、设备资产、运维工程师。"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.constants import STATUS_NEW
from app.db.base import Base


def _now() -> datetime:
    return datetime.now()


class Ticket(Base):
    """工单表：同时承载「线上工单」与「历史已解决工单」（种子数据）。"""

    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    fault_type: Mapped[str] = mapped_column(String(32), default="其他")
    urgency: Mapped[str] = mapped_column(String(8), default="P2")
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_contact: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # 状态流转：新建 → 处理中 → 已解决 / 转人工
    status: Mapped[str] = mapped_column(String(16), default=STATUS_NEW, index=True)
    # 终态标记：自动闭环 / 转人工
    resolution_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    assignee: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notify_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class AgentLog(Base):
    """Agent 处理日志表：每个节点 / 每次工具调用都会落一行。"""

    __tablename__ = "agent_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    node: Mapped[str] = mapped_column(String(32), default="")  # 节点名或 tool:工具名
    level: Mapped[str] = mapped_column(String(8), default="INFO")
    message: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON 字符串
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class DeviceAsset(Base):
    """设备资产表（工具2 查询的数据源）。"""

    __tablename__ = "device_assets"

    device_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(128), default="")
    device_type: Mapped[str] = mapped_column(String(32), default="")
    os: Mapped[str] = mapped_column(String(64), default="")
    ip: Mapped[str] = mapped_column(String(32), default="")
    location: Mapped[str] = mapped_column(String(64), default="")
    owner: Mapped[str] = mapped_column(String(64), default="")
    purchase_date: Mapped[str] = mapped_column(String(16), default="")
    warranty_until: Mapped[str] = mapped_column(String(16), default="")
    health_status: Mapped[str] = mapped_column(String(16), default="正常")


class Engineer(Base):
    """运维工程师表（转人工时按故障类型专长 + 负载分派）。"""

    __tablename__ = "engineers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(32), default="")
    specialty: Mapped[str] = mapped_column(String(32), default="")  # 对应 fault_type
    level: Mapped[str] = mapped_column(String(8), default="L1")
    contact: Mapped[str] = mapped_column(String(64), default="")
    active_tickets: Mapped[int] = mapped_column(Integer, default=0)
    # 平均处理时长（小时），用于通知中展示预计响应
    avg_hours: Mapped[float] = mapped_column(Float, default=2.0)