"""异步任务登记表：支撑「提交即返回、后台跑 Agent」的任务状态查询。

为什么需要
    `POST /api/tickets` 同步执行完整 Agent（mock ~0.6s，真实 LLM 数秒级），
    无法满足提交接口 300ms 的响应目标。异步入口把 Agent 放到后台执行，立即
    返回 202，再由 `GET /api/tickets/{id}/status` 轮询终态。

边界
    进程内状态，进程重启即丢失（任务本身已落库，可通过重跑恢复）。仅在
    `POST /api/tickets/async` 与状态查询之间承担「进行中 / 失败」的过渡表达，
    终态以数据库中的工单状态为准。
"""

from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_tasks: dict[int, dict] = {}

STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_FAILED = "failed"


def start(ticket_id: int) -> None:
    """登记任务开始。"""
    with _lock:
        _tasks[int(ticket_id)] = {
            "ticket_id": int(ticket_id),
            "state": STATE_RUNNING,
            "started_at": time.time(),
            "finished_at": None,
            "elapsed_ms": None,
            "error": "",
        }


def finish(ticket_id: int, ok: bool, error: str = "") -> None:
    """登记任务结束（成功或失败）。"""
    with _lock:
        task = _tasks.get(int(ticket_id))
        if task is None:
            return
        now = time.time()
        task["state"] = STATE_DONE if ok else STATE_FAILED
        task["finished_at"] = now
        task["elapsed_ms"] = round((now - task["started_at"]) * 1000, 2)
        task["error"] = error


def get(ticket_id: int) -> dict | None:
    """查询任务状态；未登记返回 None（由调用方回退到数据库工单状态）。"""
    with _lock:
        task = _tasks.get(int(ticket_id))
        if task is None:
            return None
        snapshot = dict(task)
    if snapshot["state"] == STATE_RUNNING:
        snapshot["elapsed_ms"] = round((time.time() - snapshot["started_at"]) * 1000, 2)
    return snapshot


def stats() -> dict:
    """任务汇总（供 /api/metrics 观测）。"""
    with _lock:
        states = [t["state"] for t in _tasks.values()]
        return {
            "total": len(_tasks),
            "running": states.count(STATE_RUNNING),
            "done": states.count(STATE_DONE),
            "failed": states.count(STATE_FAILED),
        }


def clear() -> None:
    """清空登记表（测试用）。"""
    with _lock:
        _tasks.clear()