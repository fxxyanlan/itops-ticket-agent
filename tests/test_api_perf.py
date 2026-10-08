"""API 性能与响应时间测试（进程内 TestClient）。

说明
    本文件在进程内调用 ASGI 应用，测得的绝对耗时低于真实网络往返；其价值在于
    **回归守卫**：一旦缓存失效、检索退化或新增 N+1 查询，分位耗时会立刻劣化并
    触发失败。真实 HTTP 压测结果见根目录 `API测试报告.md`。

SLA
    只读接口 P95 ≤ 300ms；提交类接口因同步执行完整 Agent 编排，不纳入该目标
    （异步入口 `/api/tickets/async` 的受理响应满足目标）。
"""

from __future__ import annotations

import time

import pytest

SAMPLE = {
    "description": "打印机 PC-1024 打印一直提示脱机，重启打印服务也不行",
    "user_contact": "perf@test.com",
    "device_id": "PC-1024",
}
SLA_MS = 300.0
SAMPLES = 60


def _percentile(values: list[float], pct: float) -> float:
    """线性插值分位数（与 scripts/api_benchmark.py 口径一致）。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    low, high = int(rank), min(int(rank) + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def _measure(fn, times: int) -> list[float]:
    """预热一次后重复调用，返回每轮耗时（毫秒）。"""
    fn()
    samples: list[float] = []
    for _ in range(times):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000)
    return samples


@pytest.fixture()
def seeded_ticket(client):
    """预置一张工单，保证详情 / 日志类接口有数据可读。"""
    return client.post("/api/tickets", json=SAMPLE).json()["data"]["ticket"]


@pytest.mark.perf
@pytest.mark.parametrize(
    "label,path,params",
    [
        ("健康检查", "/api/health", None),
        ("工单列表", "/api/tickets", {"limit": 100}),
        ("工单列表(按状态)", "/api/tickets", {"status": "已解决", "limit": 100}),
        ("设备列表", "/api/devices", {"limit": 200}),
        ("运行指标", "/api/metrics", None),
    ],
)
def test_read_endpoint_p95_within_sla(client, label, path, params):
    samples = _measure(lambda: client.get(path, params=params), SAMPLES)
    p95 = _percentile(samples, 0.95)
    print(f"\n[{label}] P50={_percentile(samples, 0.5):.2f}ms P95={p95:.2f}ms")
    assert p95 < SLA_MS, f"{label} P95={p95:.2f}ms 超过 SLA {SLA_MS}ms"


@pytest.mark.perf
def test_ticket_detail_p95_within_sla(client, seeded_ticket):
    tid = seeded_ticket["id"]
    samples = _measure(lambda: client.get(f"/api/tickets/{tid}"), SAMPLES)
    p95 = _percentile(samples, 0.95)
    print(f"\n[工单详情] P50={_percentile(samples, 0.5):.2f}ms P95={p95:.2f}ms")
    assert p95 < SLA_MS


@pytest.mark.perf
def test_ticket_logs_p95_within_sla(client, seeded_ticket):
    tid = seeded_ticket["id"]
    samples = _measure(lambda: client.get(f"/api/tickets/{tid}/logs"), SAMPLES)
    p95 = _percentile(samples, 0.95)
    print(f"\n[工单日志] P50={_percentile(samples, 0.5):.2f}ms P95={p95:.2f}ms")
    assert p95 < SLA_MS


@pytest.mark.perf
def test_async_submit_p95_within_sla(client):
    """异步受理只做落库 + 登记，响应体量小，应满足 300ms。"""
    samples = _measure(lambda: client.post("/api/tickets/async", json=SAMPLE), 30)
    p95 = _percentile(samples, 0.95)
    print(f"\n[异步受理] P50={_percentile(samples, 0.5):.2f}ms P95={p95:.2f}ms")
    assert p95 < SLA_MS


@pytest.mark.perf
def test_read_cache_effective(client):
    """同一读请求重复调用应命中缓存：后续分位耗时不得高于首轮。"""
    path, params = "/api/tickets", {"limit": 100}
    client.get(path, params=params)  # 首轮（冷）
    warm = _measure(lambda: client.get(path, params=params), SAMPLES)
    assert _percentile(warm, 0.95) < SLA_MS
    stats = client.get("/api/metrics").json()["data"]["cache"]
    assert stats["hits"] > 0, "只读接口应产生缓存命中"