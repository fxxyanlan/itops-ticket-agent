"""进程内 TTL 缓存：为只读接口消除重复查询开销。

设计取舍
- 单进程自持的字典缓存，不引入 Redis 等外部依赖（本项目为单机 SQLite 架构）；
- **失效集中化**：所有工单写路径统一调用 `invalidate("tickets")`，不依赖定时过期
  来保证正确性，因此「读到过期数据」的窗口仅由 TTL 且由写操作主动打断；
- 仅缓存可安全复用的读结果，缓存对象视为只读，调用方不得就地修改。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, TypeVar

T = TypeVar("T")

_lock = threading.Lock()
_store: dict[str, tuple[float, Any]] = {}
_hits = 0
_misses = 0


def get(key: str) -> Any | None:
    """取缓存；不存在或已过期时返回 None。"""
    global _hits, _misses
    now = time.monotonic()
    with _lock:
        entry = _store.get(key)
        if entry is None:
            _misses += 1
            return None
        expire_at, value = entry
        if expire_at <= now:
            _store.pop(key, None)
            _misses += 1
            return None
        _hits += 1
        return value


def set(key: str, value: Any, ttl: float) -> None:  # noqa: A001
    """写入缓存；ttl <= 0 表示不缓存。"""
    if ttl <= 0:
        return
    with _lock:
        _store[key] = (time.monotonic() + ttl, value)


def get_or_load(key: str, ttl: float, loader: Callable[[], T]) -> T:
    """缓存未命中时执行 loader 并回填。"""
    cached = get(key)
    if cached is not None:
        return cached
    value = loader()
    set(key, value, ttl)
    return value


def invalidate(prefix: str = "") -> int:
    """按前缀批量失效（默认清空全部），返回清除条数。"""
    with _lock:
        if not prefix:
            count = len(_store)
            _store.clear()
            return count
        doomed = [k for k in _store if k.startswith(prefix)]
        for key in doomed:
            _store.pop(key, None)
        return len(doomed)


def stats() -> dict:
    """缓存运行统计（供 /api/metrics 观测）。"""
    with _lock:
        total = _hits + _misses
        return {
            "entries": len(_store),
            "hits": _hits,
            "misses": _misses,
            "hit_rate": round(_hits / total, 4) if total else 0.0,
        }


def clear() -> None:
    """清空缓存与统计（测试用）。"""
    global _hits, _misses
    with _lock:
        _store.clear()
        _hits = 0
        _misses = 0