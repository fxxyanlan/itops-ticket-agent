"""FastAPI 应用入口。

启动：
    py -m uvicorn app.main:app --reload --port 8000

契约约定
    所有响应（含错误）统一为 `{code, message, detail, data}`。其中 `detail` 为
    兼容字段：前端错误处理读取的是它，保留可避免既有调用方改动。
"""

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api.routes import router
from app.config import settings
from app.db.base import init_db
from app.logging_config import get_logger, setup_logging
from app.services import metrics


setup_logging()
logger = get_logger("itops.main")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """启动时初始化数据库表并打印运行模式。"""
    init_db()
    logger.info(
        "服务启动完成｜LLM 模式=%s｜模型=%s｜相似度阈值=%s",
        settings.effective_llm_mode,
        settings.deepseek_model,
        settings.match_threshold,
    )
    yield
    logger.info("服务已停止")


app = FastAPI(
    title="IT 运维智能工单自动处理 Agent",
    description="LangGraph 状态机 + DeepSeek 原生 Function Calling",
    version=__version__,
    lifespan=lifespan,
)

# 允许 Streamlit 前端（8501）跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    """记录每个请求的耗时与状态码，并回写 X-Response-Time-Ms 响应头。"""
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = (time.perf_counter() - started) * 1000
        metrics.record(request.url.path, 500, elapsed_ms)
        raise
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.2f}"
    metrics.record(request.url.path, response.status_code, elapsed_ms)
    return response


app.include_router(router)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """统一 HTTP 异常信封（404 / 500 等），并保留 `detail` 兼容字段。"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.status_code,
            "message": str(exc.detail),
            "detail": str(exc.detail),
            "data": None,
        },
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """统一参数校验失败信封：给出可定位的字段与原因。"""
    errors = exc.errors()
    first = errors[0] if errors else {}
    field = ".".join(str(part) for part in first.get("loc", [])[1:]) or "body"
    reason = first.get("msg", "请求参数校验失败")
    return JSONResponse(
        status_code=422,
        content={
            "code": 422,
            "message": f"请求参数校验失败：{field} {reason}",
            "detail": f"{field}: {reason}",
            "errors": jsonable_encoder(errors),
            "data": None,
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """兜底异常处理：统一返回 {code,message,detail,data}，避免裸栈与内部信息泄漏。"""
    logger.exception("未处理异常：%s %s", request.method, request.url.path)
    message = "服务内部异常，请稍后重试"
    return JSONResponse(
        status_code=500,
        content={
            "code": 500,
            "message": message,
            # 仅在显式开启调试时回显异常细节，生产默认不回传内部信息
            "detail": f"{type(exc).__name__}: {exc}" if settings.expose_error_detail else message,
            "data": None,
        },
    )


@app.get("/", include_in_schema=False)
def index() -> dict:
    """根路径：简单导航信息。"""
    return {
        "name": "IT 运维智能工单自动处理 Agent",
        "version": __version__,
        "docs": "/docs",
        "health": "/api/health",
    }