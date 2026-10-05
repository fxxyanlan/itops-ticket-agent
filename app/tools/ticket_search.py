"""工具1：检索历史同类型工单的解决方案。

实现要点：
- 只检索 `status='已解决'` 的历史工单；
- 先按故障类型过滤，再用 difflib 计算文本相似度作为匹配分；
- 返回 top-k 与最高分 best_score，供 LangGraph 条件边判定是否有成熟方案。
"""

from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import STATUS_RESOLVED
from app.db.base import session_scope
from app.db.models import Ticket

# 大模型原生 Function Calling 的工具声明
SPEC = {
    "type": "function",
    "function": {
        "name": "search_similar_tickets",
        "description": "按故障类型与问题描述检索历史【已解决】工单，返回相似解决方案与匹配度分数，用于判断是否存在成熟方案",
        "parameters": {
            "type": "object",
            "properties": {
                "fault_type": {"type": "string", "description": "故障类型，如 打印机/账号权限/网络/硬件/软件/服务器/邮箱/数据库/安全事件/其他"},
                "keywords": {"type": "string", "description": "用于相似度匹配的问题描述或关键词，建议直接传入用户的原始描述"},
                "top_k": {"type": "integer", "description": "返回条数，默认 3"},
            },
            "required": ["fault_type", "keywords"],
        },
    },
}

# 归一化时剔除的噪声字符
_NOISE_CHARS = set(" \t\r\n，。！？、；：,.!?;:\"'“”‘’()（）[]【】<>《》-—_/\\|~`@#$%^&*+=*")


def normalize(text: str) -> str:
    """文本归一化：小写、去空白与标点，便于相似度比较。"""
    if not text:
        return ""
    return "".join(ch.lower() for ch in text if ch not in _NOISE_CHARS)


def similarity(query: str, candidate: str) -> float:
    """基于 difflib 的相似度，返回 [0,1]。"""
    q, c = normalize(query), normalize(candidate)
    if not q or not c:
        return 0.0
    return SequenceMatcher(None, q, c).ratio()


def search_in_session(
    session: Session, fault_type: str, keywords: str, top_k: int = 3
) -> list[dict]:
    """在给定会话中执行检索，返回按匹配分倒序的候选列表。"""
    rows = session.scalars(
        select(Ticket).where(Ticket.status == STATUS_RESOLVED, Ticket.fault_type == fault_type)
    ).all()

    scored: list[dict] = []
    for row in rows:
        # 标题与描述分别比较取较高分，避免长描述稀释相似度
        score = max(
            similarity(keywords, row.title or ""),
            similarity(keywords, row.description or ""),
        )
        scored.append(
            {
                "ticket_id": row.id,
                "title": row.title,
                "fault_type": row.fault_type,
                "resolution": row.resolution or "",
                "score": round(score, 4),
            }
        )

    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[: max(1, int(top_k or 3))]


def run(fault_type: str, keywords: str, top_k: int = 3) -> dict:
    """工具入口：返回匹配结果与最高匹配分。"""
    with session_scope() as session:
        matches = search_in_session(session, fault_type, keywords, top_k)

    best = matches[0]["score"] if matches else 0.0
    return {
        "fault_type": fault_type,
        "matches": matches,
        "best_score": best,
        "candidate_count": len(matches),
        "hint": "best_score 越高说明越可能复用历史方案" if matches else "该故障类型暂无历史已解决工单",
    }