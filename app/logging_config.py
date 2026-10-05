"""日志系统：控制台 + 滚动文件双通道输出。

- 控制台：便于在 Trae 终端实时观察 Agent 思考与工具调用。
- 文件：logs/app.log，5MB 滚动、保留 3 份。
"""

import logging
import sys
from logging.handlers import RotatingFileHandler

from app.config import LOG_DIR, settings

_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-18s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_initialized = False


def setup_logging() -> None:
    """初始化根 logger（幂等，多次调用只生效一次）。"""
    global _initialized
    if _initialized:
        return

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, (settings.log_level or "INFO").upper(), logging.INFO)
    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    root = logging.getLogger()
    root.setLevel(level)
    # 清理已有 handler，避免 uvicorn --reload 下重复输出
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.setLevel(level)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        LOG_DIR / "app.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    root.addHandler(file_handler)

    # 降低第三方库噪音
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    _initialized = True


def get_logger(name: str = "itops") -> logging.Logger:
    """获取带统一格式的 logger。"""
    setup_logging()
    return logging.getLogger(name)