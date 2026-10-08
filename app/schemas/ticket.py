"""Pydantic 模型：API 请求 / 响应结构。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TicketCreate(BaseModel):
    """提交工单请求体。

    长度上限与数据库列宽对齐（`user_contact` 128 / `device_id` 64），避免超长值
    在不同数据库后端出现截断或写入失败；`description` 上限 2000 字符，既覆盖
    真实故障描述，也防止超大报文拖垮解析与向量化开销。
    """

    description: str = Field(..., min_length=2, max_length=2000, description="自然语言故障描述")
    user_contact: str | None = Field(None, max_length=128, description="用户联系方式")
    device_id: str | None = Field(None, max_length=64, description="设备编号（可选，模型也会尝试自动抽取）")

    @field_validator("description", mode="before")
    @classmethod
    def _strip_description(cls, value: object) -> object:
        """先去空白，再交给长度约束判定，避免「全空白」绕过最小长度校验。"""
        return value.strip() if isinstance(value, str) else value

    @field_validator("user_contact", "device_id", mode="before")
    @classmethod
    def _strip_optional(cls, value: object) -> object:
        """可选字段：去空白，空串归一为 None。"""
        if not isinstance(value, str):
            return value
        text = value.strip()
        return text or None


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


# 与 app.constants.ALL_STATUSES 保持一致；用 Literal 是为了让 OpenAPI 自动展示枚举
TicketStatusFilter = Literal["新建", "处理中", "已解决", "转人工"]


class TicketTaskStatus(BaseModel):
    """异步提交任务的状态输出结构。"""

    ticket_id: int
    state: str = Field(..., description="任务态：running / done / failed；未登记过为 unknown")
    terminal: bool = Field(..., description="是否已到达终态（无论成功或失败）")
    ticket_status: str = Field(..., description="数据库中工单的当前状态")
    resolution_mode: str | None = Field(None, description="自动闭环 / 转人工")
    elapsed_ms: float | None = Field(None, description="已耗时（毫秒）")
    error: str = Field("", description="失败原因（state=failed 时非空）")