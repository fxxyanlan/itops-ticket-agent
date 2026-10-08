"""FastAPI 路由：工单提交/查询、Agent 日志、设备资产、健康检查、运行指标。

性能与契约要点
- 读接口带 TTL 缓存，写路径集中失效（见 app/services/ticket_service.py）；
- 健康检查用轻量 `SELECT 1` 探活并缓存结果，不再每请求执行 create_all；
- 列表类接口统一支持 limit/offset 分页，并有服务端上限；
- 新增异步提交入口，避免长耗时 Agent 阻塞提交接口的响应时间目标。
"""

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from app.config import settings
from app.constants import STATUS_HUMAN, STATUS_RESOLVED
from app.db.base import probe_db
from app.logging_config import get_logger
from app.schemas.ticket import ApiResponse, TicketCreate, TicketStatusFilter, TicketTaskStatus
from app.services import cache, metrics, task_registry, ticket_service
from app.services.agent_runner import run_agent

router = APIRouter(prefix="/api", tags=["itops"])
logger = get_logger("itops.api")

# 工单终态（用于异步任务状态是否终态的判定）
_TERMINAL_STATUSES = {STATUS_RESOLVED, STATUS_HUMAN}
# 健康检查数据库探活结果的缓存键
_HEALTH_CACHE_KEY = "health:db"


@router.get("/health", response_model=ApiResponse)
def health() -> ApiResponse:
    """健康检查：返回 LLM 模式与数据库连通性（探活结果按 TTL 缓存）。"""
    db_status = cache.get(_HEALTH_CACHE_KEY)
    if db_status is None:
        try:
            probe_db()
            db_status = "ok"
        except Exception as exc:  # noqa: BLE001
            # 只回传异常类型，不回显内部细节（原始堆栈仅进日志）
            logger.exception("数据库探活失败")
            db_status = f"error: {type(exc).__name__}"
        cache.set(_HEALTH_CACHE_KEY, db_status, settings.health_cache_ttl_seconds)

    return ApiResponse(
        data={
            "status": "ok" if db_status == "ok" else "degraded",
            "llm_mode": settings.effective_llm_mode,
            "model": settings.deepseek_model,
            "database": db_status,
            "match_threshold": settings.match_threshold,
        }
    )


@router.get("/metrics", response_model=ApiResponse)
def get_metrics() -> ApiResponse:
    """运行指标：逐路径耗时分位、错误率、缓存命中率与异步任务概况。"""
    return ApiResponse(
        data={
            "runtime": metrics.snapshot(),
            "cache": cache.stats(),
            "tasks": task_registry.stats(),
        }
    )


@router.post("/tickets", response_model=ApiResponse)
def create_ticket(payload: TicketCreate) -> ApiResponse:
    """提交工单：落库后立即同步跑完 Agent，返回终态结果。

    该接口同步执行完整 Agent 闭环（mock 约 0.6s、真实模型数秒级），响应时间
    受 Agent 编排主导，不适用 300ms 目标；如需快速受理请使用 `/api/tickets/async`。
    """
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


def _run_agent_background(ticket_id: int) -> None:
    """后台执行 Agent 并登记任务结果（异常不外抛，只登记为 failed）。"""
    try:
        run_agent(ticket_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("后台 Agent 处理失败：%s", ticket_id)
        task_registry.finish(ticket_id, ok=False, error=f"{type(exc).__name__}: {exc}")
        return
    task_registry.finish(ticket_id, ok=True)


@router.post("/tickets/async", response_model=ApiResponse, status_code=202)
def create_ticket_async(payload: TicketCreate, background: BackgroundTasks) -> ApiResponse:
    """异步提交工单：立即返回 202，Agent 转后台执行，由状态接口轮询终态。"""
    ticket = ticket_service.create_ticket(
        description=payload.description,
        user_contact=payload.user_contact,
        device_id=payload.device_id,
    )
    ticket_id = ticket["id"]
    task_registry.start(ticket_id)
    background.add_task(_run_agent_background, ticket_id)

    logger.info("已受理异步工单：#%s", ticket_id)
    return ApiResponse(
        message="已受理，Agent 正在后台处理",
        data={
            "ticket_id": ticket_id,
            "status": ticket["status"],
            "poll_url": f"/api/tickets/{ticket_id}/status",
        },
    )


@router.get("/tickets", response_model=ApiResponse)
def list_tickets(
    status: TicketStatusFilter | None = Query(None, description="按状态过滤：新建/处理中/已解决/转人工"),
    limit: int = Query(100, ge=1, le=settings.max_page_size),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """工单列表（按 id 倒序，支持分页）。"""
    return ApiResponse(data=ticket_service.list_tickets(status=status, limit=limit, offset=offset))


@router.get("/tickets/{ticket_id}", response_model=ApiResponse)
def get_ticket(ticket_id: int) -> ApiResponse:
    """工单详情。"""
    ticket = ticket_service.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail=f"工单不存在：{ticket_id}")
    return ApiResponse(data=ticket)


@router.get("/tickets/{ticket_id}/status", response_model=ApiResponse)
def get_ticket_status(ticket_id: int) -> ApiResponse:
    """异步任务状态：供 `/api/tickets/async` 轮询，直到 `terminal=true`。"""
    ticket = ticket_service.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail=f"工单不存在：{ticket_id}")

    task = task_registry.get(ticket_id)
    if task is None:
        # 未登记过（如进程重启或同步接口创建）：以数据库状态为准
        state = "unknown"
        terminal = ticket["status"] in _TERMINAL_STATUSES
        elapsed_ms = None
        error = ""
    else:
        state = task["state"]
        terminal = state != task_registry.STATE_RUNNING
        elapsed_ms = task["elapsed_ms"]
        error = task["error"]

    payload = TicketTaskStatus(
        ticket_id=ticket_id,
        state=state,
        terminal=terminal,
        ticket_status=ticket["status"],
        resolution_mode=ticket.get("resolution_mode"),
        elapsed_ms=elapsed_ms,
        error=error,
    )
    return ApiResponse(data=payload.model_dump())


@router.get("/tickets/{ticket_id}/logs", response_model=ApiResponse)
def get_ticket_logs(
    ticket_id: int,
    limit: int = Query(settings.default_log_limit, ge=1, le=2000, description="返回条数上限"),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """某工单的 Agent 处理日志（时间正序，支持分页）。"""
    if ticket_service.get_ticket(ticket_id) is None:
        raise HTTPException(status_code=404, detail=f"工单不存在：{ticket_id}")
    return ApiResponse(data=ticket_service.list_logs(ticket_id, limit=limit, offset=offset))


@router.post("/tickets/{ticket_id}/rerun", response_model=ApiResponse)
def rerun_ticket(ticket_id: int) -> ApiResponse:
    """重新触发 Agent 处理（演示 / 重试用）。

    与 `POST /api/tickets` 同为同步执行，不适用 300ms 目标。
    """
    if ticket_service.get_ticket(ticket_id) is None:
        raise HTTPException(status_code=404, detail=f"工单不存在：{ticket_id}")
    try:
        result = run_agent(ticket_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("重跑 Agent 失败：%s", ticket_id)
        raise HTTPException(status_code=500, detail=f"Agent 处理失败：{exc}") from exc
    return ApiResponse(message="重新处理完成", data=result)


@router.get("/devices", response_model=ApiResponse)
def list_devices(limit: int = Query(200, ge=1, le=settings.max_page_size)) -> ApiResponse:
    """设备资产列表（前端辅助查看）。"""
    return ApiResponse(data=ticket_service.list_devices(limit=limit))