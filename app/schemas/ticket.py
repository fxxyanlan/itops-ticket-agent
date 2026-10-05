"""Pydantic 模型：API 请求 / 响应结构。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TicketCreate(BaseModel):
    """提交工单请求体。"""

    description: str = Field(..., min_length=2, description="自然语言故障描述")
    user_contact: str | None = Field(None, description="用户联系方式")
    device_id: str | None = Field(None, description="设备编号（可选，模型也会尝试自动抽取）")


class TicketOut(BaseModel):
    """工单输出结构。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str
    fault_type: str
    urgency: str
    device_id: str | None = None
    user_contact: str | None = None
    status: str
    resolution_mode: str | None = None
    resolution: str | None = None
    assignee: str | None = None
    notify_message: str | None = None
    decision_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class AgentLogOut(BaseModel):
    """Agent 日志输出结构。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    ticket_id: int
    node: str
    level: str
    message: str
    detail: str | None = None
    created_at: datetime


class ApiResponse(BaseModel):
    """统一响应体。"""

    code: int = 0
    message: str = "ok"
    data: object | None = None


class HealthOut(BaseModel):
    """健康检查输出。"""

    status: str
    llm_mode: str
    database: str
    model: str