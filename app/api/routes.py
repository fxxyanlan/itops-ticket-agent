"""FastAPI 路由：工单提交/查询、Agent 日志、设备资产、健康检查。"""

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.db.base import init_db
from app.logging_config import get_logger
from app.schemas.ticket import ApiResponse, TicketCreate
from app.services import ticket_service
from app.services.agent_runner import run_agent

router = APIRouter(prefix="/api", tags=["itops"])
logger = get_logger("itops.api")


@router.get("/health", response_model=ApiResponse)
def health() -> ApiResponse:
    """健康检查：返回 LLM 模式与数据库连通性。"""
    db_status = "ok"
    try:
        init_db()
    except Exception as exc:  # noqa: BLE001
        db_status = f"error: {exc}"
    return ApiResponse(
        data={
            "status": "ok" if db_status == "ok" else "degraded",
            "llm_mode": settings.effective_llm_mode,
            "model": settings.deepseek_model,
            "database": db_status,
            "match_threshold": settings.match_threshold,
        }
    )


@router.post("/tickets", response_model=ApiResponse)
def create_ticket(payload: TicketCreate) -> ApiResponse:
    """提交工单：落库后立即同步跑完 Agent，返回终态结果。"""
    ticket = ticket_service.create_ticket(
        description=payload.description,
        user_contact=payload.user_contact,
        device_id=payload.device_id,
    )
    try:
        result = run_agent(ticket["id"])
    except Exception as exc:  # noqa: BLE001
        logger.exception("Agent 处理工单失败：%s", ticket["id"])
        raise HTTPException(status_code=500, detail=f"Agent 处理失败：{exc}") from exc

    return ApiResponse(message="处理完成", data=result)


@router.get("/tickets", response_model=ApiResponse)
def list_tickets(
    status: str | None = Query(None, description="按状态过滤：新建/处理中/已解决/转人工"),
    limit: int = Query(100, ge=1, le=500),
) -> ApiResponse:
    """工单列表。"""
    return ApiResponse(data=ticket_service.list_tickets(status=status, limit=limit))


@router.get("/tickets/{ticket_id}", response_model=ApiResponse)
def get_ticket(ticket_id: int) -> ApiResponse:
    """工单详情。"""
    ticket = ticket_service.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail=f"工单不存在：{ticket_id}")
    return ApiResponse(data=ticket)


@router.get("/tickets/{ticket_id}/logs", response_model=ApiResponse)
def get_ticket_logs(ticket_id: int) -> ApiResponse:
    """某工单的 Agent 处理日志（时间正序）。"""
    if ticket_service.get_ticket(ticket_id) is None:
        raise HTTPException(status_code=404, detail=f"工单不存在：{ticket_id}")
    return ApiResponse(data=ticket_service.list_logs(ticket_id))


@router.post("/tickets/{ticket_id}/rerun", response_model=ApiResponse)
def rerun_ticket(ticket_id: int) -> ApiResponse:
    """重新触发 Agent 处理（演示 / 重试用）。"""
    if ticket_service.get_ticket(ticket_id) is None:
        raise HTTPException(status_code=404, detail=f"工单不存在：{ticket_id}")
    try:
        result = run_agent(ticket_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("重跑 Agent 失败：%s", ticket_id)
        raise HTTPException(status_code=500, detail=f"Agent 处理失败：{exc}") from exc
    return ApiResponse(message="重新处理完成", data=result)


@router.get("/devices", response_model=ApiResponse)
def list_devices(limit: int = Query(200, ge=1, le=500)) -> ApiResponse:
    """设备资产列表（前端辅助查看）。"""
    return ApiResponse(data=ticket_service.list_devices(limit=limit))