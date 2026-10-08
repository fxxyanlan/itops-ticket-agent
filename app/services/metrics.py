"""请求指标采集：进程内滚动统计，供 `GET /api/metrics` 观测。

用途
    压测与在线观测共用同一套指标口径，避免「报告用一套脚本、线上看另一套数字」。
    每个路径保留最近 N 个耗时样本（环形），据此计算 P50/P95/P99 与错误率。

说明
    不引入 Prometheus 等外部依赖，保持单机架构自洽；如需接入监控体系，
    可在 `record()` 中追加一个 exporter 调用，不影响现有契约。
"""

from __future__ import annotations

import math
import threading
from collections import defaultdict, deque

_MAX_SAMPLES = 2000  # 每路径保留的最大样本数

_lock = threading.Lock()
_samples: dict[str, deque] = defaultdict(lambda: deque(maxlen=_MAX_SAMPLES))
_counts: dict[str, int] = defaultdict(int)
_errors: dict[str, int] = defaultdict(int)


def record(path: str, status_code: int, elapsed_ms: float) -> None:
    """记录一次请求。5xx 与 4xx 计入错误（4xx 反映客户端/契约问题，也应可见）。"""
    key = path
    with _lock:
        _samples[key].append(float(elapsed_ms))
        _counts[key] += 1
        if status_code >= 400:
            _errors[key] += 1


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    k = (len(ordered) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return ordered[int(k)]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def snapshot(top_paths: int = 20) -> dict:
    """输出各路径指标快照，按请求量倒序。"""
    with _lock:
        counts = dict(_counts)
        errors = dict(_errors)
        samples = {k: list(v) for k, v in _samples.items()}

    rows: list[dict] = []
    for path, count in counts.items():
        values = samples.get(path, [])
        rows.append(
            {
                "path": path,
                "requests": count,
                "errors": errors.get(path, 0),
                "error_rate": round(errors.get(path, 0) / count, 6) if count else 0.0,
                "p50_ms": round(_percentile(values, 0.50), 2),
                "p95_ms": round(_percentile(values, 0.95), 2),
                "p99_ms": round(_percentile(values, 0.99), 2),
                "max_ms": round(max(values), 2) if values else 0.0,
                "samples": len(values),
            }
        )
    rows.sort(key=lambda item: item["requests"], reverse=True)

    total = sum(counts.values())
    total_errors = sum(errors.values())
    return {
        "total_requests": total,
        "total_errors": total_errors,
        "overall_error_rate": round(total_errors / total, 6) if total else 0.0,
        "sample_window_per_path": _MAX_SAMPLES,
        "paths": rows[:top_paths],
    }


def clear() -> None:
    """清空指标（测试用）。"""
    with _lock:
        _samples.clear()
        _counts.clear()
        _errors.clear()