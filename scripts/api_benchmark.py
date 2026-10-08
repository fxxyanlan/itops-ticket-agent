r"""API 性能压测脚本：真实数据、真实 HTTP、可复现，产出报告所需的性能指标。

用途
    对运行中的后端做端到端并发压测，逐接口产出 P50/P95/P99、错误率与吞吐，
    用于「优化前 vs 优化后」对照。所有请求走真实 HTTP，不做任何桩替换。

前置
    1) 生成基准数据：scripts\gen_benchmark_data.py
    2) 以基准库启动后端（务必覆盖 DATABASE_URL，避免污染 demo 库）：
         $env:DATABASE_URL="sqlite:///C:/.../data/benchmark.db"
         $env:LLM_MODE="mock"
         .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8011

用法
    .\.venv\Scripts\python.exe scripts\api_benchmark.py --label baseline
    .\.venv\Scripts\python.exe scripts\api_benchmark.py --label optimized --out api_benchmark_result.json

产物
    api_benchmark_result.json（含 label、逐接口指标、环境信息）
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUT = BASE_DIR / "api_benchmark_result.json"


@dataclass
class Scenario:
    """一个压测场景。"""

    name: str
    method: str
    path: str
    count: int
    kind: str = "read"  # read | write_agent
    expected: tuple[int, ...] = (200,)
    note: str = ""


@dataclass
class Result:
    """单个场景的聚合结果。"""

    name: str
    method: str
    path: str
    kind: str
    count: int
    errors: int = 0
    latencies: list[float] = field(default_factory=list)
    wall_seconds: float = 0.0
    status_codes: dict[str, int] = field(default_factory=dict)
    sample_error: str = ""

    def _pct(self, p: float) -> float:
        values = sorted(self.latencies)
        if not values:
            return 0.0
        if len(values) == 1:
            return values[0]
        k = (len(values) - 1) * p
        lo, hi = math.floor(k), math.ceil(k)
        if lo == hi:
            return values[int(k)]
        return values[lo] + (values[hi] - values[lo]) * (k - lo)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "method": self.method,
            "path": self.path,
            "kind": self.kind,
            "count": self.count,
            "completed": len(self.latencies),
            "errors": self.errors,
            "error_rate": round(self.errors / self.count, 6) if self.count else 0.0,
            "status_codes": self.status_codes,
            "p50_ms": round(self._pct(0.50), 2),
            "p95_ms": round(self._pct(0.95), 2),
            "p99_ms": round(self._pct(0.99), 2),
            "mean_ms": round(statistics.fmean(self.latencies), 2) if self.latencies else 0.0,
            "min_ms": round(min(self.latencies), 2) if self.latencies else 0.0,
            "max_ms": round(max(self.latencies), 2) if self.latencies else 0.0,
            "rps": round(len(self.latencies) / self.wall_seconds, 2) if self.wall_seconds > 0 else 0.0,
            "sample_error": self.sample_error,
        }


_local = threading.local()


def _session() -> requests.Session:
    """每线程一个 Session，复用连接（更贴近真实客户端行为）。"""
    if not hasattr(_local, "sess"):
        sess = requests.Session()
        sess.headers.update({"Content-Type": "application/json"})
        _local.sess = sess
    return _local.sess


def _run_one(scenario: Scenario, base_url: str, payload: dict | None, timeout: int) -> tuple[float, int, str]:
    """发起单次请求，返回（耗时秒, 状态码, 错误文本）。"""
    url = base_url + scenario.path
    started = time.perf_counter()
    try:
        if scenario.method == "GET":
            resp = _session().get(url, timeout=timeout)
        else:
            resp = _session().post(url, json=payload, timeout=timeout)
        elapsed = time.perf_counter() - started
        return elapsed, resp.status_code, "" if resp.status_code in scenario.expected else resp.text[:200]
    except Exception as exc:  # noqa: BLE001
        return time.perf_counter() - started, 0, f"{type(exc).__name__}: {exc}"


def run_scenario(scenario: Scenario, base_url: str, concurrency: int, payload: dict | None, timeout: int) -> Result:
    """并发跑完一个场景并聚合。"""
    result = Result(
        name=scenario.name,
        method=scenario.method,
        path=scenario.path,
        kind=scenario.kind,
        count=scenario.count,
    )
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [
            pool.submit(_run_one, scenario, base_url, payload, timeout) for _ in range(scenario.count)
        ]
        for future in as_completed(futures):
            elapsed, status, err = future.result()
            result.latencies.append(elapsed * 1000.0)
            key = str(status)
            result.status_codes[key] = result.status_codes.get(key, 0) + 1
            if status not in scenario.expected:
                result.errors += 1
                if err and not result.sample_error:
                    result.sample_error = err
    result.wall_seconds = time.perf_counter() - started
    return result


def warm_up(base_url: str, timeout: int) -> None:
    """预热：建立连接、触发首次懒加载，避免首请求抖动污染指标。"""
    for path in ("/api/health", "/api/tickets?limit=1", "/api/devices?limit=1"):
        try:
            requests.get(base_url + path, timeout=timeout)
        except Exception:  # noqa: BLE001
            pass


def build_scenarios(
    total_read: int, total_write: int, ticket_id: int | None, include_async: bool
) -> list[Scenario]:
    """构造场景列表；依赖具体工单号的场景在拿不到 id 时自动跳过。"""
    scenarios: list[Scenario] = [
        Scenario("健康检查", "GET", "/api/health", total_read, note="高频探活接口"),
        Scenario("工单列表(默认)", "GET", "/api/tickets?limit=100", total_read),
        Scenario("工单列表(按状态)", "GET", "/api/tickets?status=已解决&limit=100", total_read),
        Scenario("设备列表", "GET", "/api/devices?limit=200", total_read, note="静态数据，缓存候选"),
    ]
    if ticket_id is not None:
        scenarios += [
            Scenario("工单详情", "GET", f"/api/tickets/{ticket_id}", total_read, expected=(200, 404)),
            Scenario("工单日志", "GET", f"/api/tickets/{ticket_id}/logs", total_read, expected=(200, 404)),
        ]
    scenarios.append(
        Scenario(
            "提交工单(同步跑Agent)",
            "POST",
            "/api/tickets",
            total_write,
            kind="write_agent",
            expected=(200,),
            note="同步执行完整 Agent，自有 SLA，不受 300ms 约束",
        )
    )
    if include_async:
        scenarios.append(
            Scenario(
                "提交工单(异步)",
                "POST",
                "/api/tickets/async",
                total_write,
                kind="write_agent",
                expected=(200, 202),
                note="立即返回 202，满足提交快路径 SLA",
            )
        )
    return scenarios


def main() -> int:
    parser = argparse.ArgumentParser(description="API 性能压测")
    parser.add_argument("--base-url", default="http://127.0.0.1:8011", help="后端地址")
    parser.add_argument("--label", default="run", help="本次压测标签（如 baseline / optimized）")
    parser.add_argument("--read-requests", type=int, default=200, help="每个读接口的请求数")
    parser.add_argument("--write-requests", type=int, default=20, help="每个写接口的请求数")
    parser.add_argument("--concurrency", type=int, default=8, help="并发线程数")
    parser.add_argument("--timeout", type=int, default=120, help="单请求超时（秒）")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="结果 JSON 输出路径")
    parser.add_argument("--no-async", action="store_true", help="跳过异步接口场景")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")

    # 探活并取一个真实工单号
    try:
        health = requests.get(base_url + "/api/health", timeout=args.timeout)
        health.raise_for_status()
        env_info = health.json()
    except Exception as exc:  # noqa: BLE001
        print(f"无法连接后端 {base_url}：{exc}")
        print("请先启动后端，例如：")
        print('  $env:DATABASE_URL="sqlite:///C:/.../data/benchmark.db"; $env:LLM_MODE="mock"')
        print("  .\\.venv\\Scripts\\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8011")
        return 1

    ticket_id = None
    try:
        listing = requests.get(base_url + "/api/tickets?limit=1", timeout=args.timeout).json()
        rows = listing.get("data") or []
        if rows:
            ticket_id = rows[0]["id"]
    except Exception:  # noqa: BLE001
        pass

    warm_up(base_url, args.timeout)

    scenarios = build_scenarios(
        args.read_requests, args.write_requests, ticket_id, include_async=not args.no_async
    )

    print(f"压测目标：{base_url}｜标签：{args.label}｜并发：{args.concurrency}")
    print(f"环境：llm_mode={env_info.get('data', {}).get('llm_mode')}｜工单样本 id={ticket_id}")
    print("-" * 100)

    results: list[Result] = []
    for scenario in scenarios:
        payload = (
            {"description": f"压测工单：打印机脱机无法打印，设备 dev_{int(time.time()) % 2000 + 1:04d}"}
            if scenario.method == "POST"
            else None
        )
        res = run_scenario(scenario, base_url, args.concurrency, payload, args.timeout)
        results.append(res)
        flag = "OK " if res.errors == 0 else "ERR"
        print(
            f"[{flag}] {res.name:<24} n={res.count:<4} P50={res._pct(0.50):>8.2f}ms "
            f"P95={res._pct(0.95):>8.2f}ms P99={res._pct(0.99):>8.2f}ms "
            f"错误={res.errors:<3} RPS={res.to_dict()['rps']:>7.2f}"
        )
        if res.sample_error:
            print(f"       └ 错误样例：{res.sample_error[:160]}")

    # 汇总：仅读接口纳入 300ms SLA 统计
    read_results = [r for r in results if r.kind == "read"]
    sla_target = 300.0
    read_ok = [r for r in read_results if r._pct(0.95) <= sla_target]
    total_requests = sum(r.count for r in results)
    total_errors = sum(r.errors for r in results)

    report = {
        "label": args.label,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "base_url": base_url,
        "concurrency": args.concurrency,
        "environment": env_info.get("data", {}),
        "ticket_sample_id": ticket_id,
        "summary": {
            "total_requests": total_requests,
            "total_errors": total_errors,
            "overall_error_rate": round(total_errors / total_requests, 6) if total_requests else 0.0,
            "read_endpoints": len(read_results),
            "read_endpoints_within_300ms_p95": len(read_ok),
            "sla_p95_target_ms": sla_target,
        },
        "results": [r.to_dict() for r in results],
    }

    out_path = Path(args.out)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("-" * 100)
    print(
        f"读接口 P95 ≤ {sla_target:.0f}ms 达标：{len(read_ok)}/{len(read_results)}｜"
        f"总请求 {total_requests}｜总错误 {total_errors}｜"
        f"整体错误率 {report['summary']['overall_error_rate'] * 100:.4f}%"
    )
    print(f"结果已写入：{out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())