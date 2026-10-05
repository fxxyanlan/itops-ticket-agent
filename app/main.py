"""FastAPI 应用入口。

启动：
    py -m uvicorn app.main:app --reload --port 8000
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.routes import router
from app.config import settings
from app.db.base import init_db
from app.logging_config import get_logger, setup_logging

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

app.include_router(router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """兜底异常处理：统一返回 {code,message,data}，避免裸栈。"""
    logger.exception("未处理异常：%s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"code": 500, "message": f"服务异常：{exc}", "data": None})


@app.get("/", include_in_schema=False)
def index() -> dict:
    """根路径：简单导航信息。"""
    return {
        "name": "IT 运维智能工单自动处理 Agent",
        "version": __version__,
        "docs": "/docs",
        "health": "/api/health",
    }