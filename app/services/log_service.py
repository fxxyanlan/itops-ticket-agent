"""Agent 日志服务：把每一步思考与工具调用同时写入 DB 和日志文件。"""

import json
from typing import Any

from sqlalchemy.orm import Session

from app.db.base import session_scope
from app.db.models import AgentLog
from app.logging_config import get_logger

logger = get_logger("itops.agent")


def _dumps(detail: Any) -> str | None:
    """把结构化细节序列化为 JSON 文本（中文不转义，不可序列化对象转字符串）。"""
    if detail is None:
        return None
    try:
        return json.dumps(detail, ensure_ascii=False, default=str)
    except TypeError:
        return json.dumps(str(detail), ensure_ascii=False)


def write_log(
    ticket_id: int,
    node: str,
    message: str,
    level: str = "INFO",
    detail: Any = None,
    session: Session | None = None,
) -> None:
    """写一条 Agent 处理日志（DB + 文件）。"""
    text = f"[ticket#{ticket_id}] [{node}] {message}"
    if level.upper() == "ERROR":
        logger.error(text)
    elif level.upper() == "WARN":
        logger.warning(text)
    else:
        logger.info(text)

    payload = dict(ticket_id=ticket_id, node=node, level=level.upper(), message=message, detail=_dumps(detail))
    if session is not None:
        session.add(AgentLog(**payload))
        return
    with session_scope() as own_session:
        own_session.add(AgentLog(**payload))