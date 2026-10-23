"""JMeter `results.jtl` 分析：单轮明细 + 多轮阶梯加压对比 + 拐点判定。

用法（在项目根目录执行）：
    py loadtest/analyze_jtl.py loadtest/results-t1.jtl loadtest/results-t5.jtl ...
    py loadtest/analyze_jtl.py --detail loadtest/results-t40.jtl

约定：并发数从文件名里的 `t<数字>` 解析（如 results-t40.jtl -> 40）。
只读指标取所有以 "GET " 开头的采样器；写接口单独列出。
"""

import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

READ_PREFIX = "GET "


def _quantile(sorted_vals: list[float], p: float) -> float:
    """线性取位分位数（样本内，不做外插）。"""
    n = len(sorted_vals)
    return sorted_vals[min(n - 1, int(n * p))]


def _stats(pairs: list[tuple[float, bool]]) -> dict:
    """由 (elapsed_ms, success) 列表算出统计量。"""
    vals = sorted(x for x, _ in pairs)
    n = len(vals)
    err = sum(1 for _, ok in pairs if not ok)
    return {
        "n": n,
        "avg": sum(vals) / n,
        "p50": _quantile(vals, 0.50),
        "p90": _quantile(vals, 0.90),
        "p95": _quantile(vals, 0.95),
        "p99": _quantile(vals, 0.99),
        "max": max(vals),
        "err%": 100 * err / n,
    }


def load(path: str) -> tuple[list[dict], dict]:
    """返回 (原始行, 按采样器分组的统计)。"""
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    grouped: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    for r in rows:
        grouped[r["label"]].append((float(r["elapsed"]), r["success"].strip().lower() == "true"))
    return rows, {k: _stats(v) for k, v in grouped.items()}


def reads_only(per_label: dict, rows: list[dict]) -> dict:
    """把所有只读采样器合并成一个总体统计。"""
    pairs = [
        (float(r["elapsed"]), r["success"].strip().lower() == "true")
        for r in rows
        if r["label"].startswith(READ_PREFIX)
    ]
    return _stats(pairs) if pairs else {"n": 0}


def elapsed_seconds(rows: list[dict]) -> float:
    """本轮实际墙钟时长（秒）。"""
    starts = [int(r["timeStamp"]) for r in rows]
    ends = [int(r["timeStamp"]) + float(r["elapsed"]) for r in rows]
    return (max(ends) - min(starts)) / 1000.0


def parse_threads(path: str, fallback: int) -> int:
    m = re.search(r"t(\d+)", Path(path).stem)
    return int(m.group(1)) if m else fallback


def fmt(v: float, width: int = 9, digits: int = 1) -> str:
    return f"{v:>{width}.{digits}f}"


def main() -> int:
    args = [a for a in sys.argv[1:]]
    detail = "--detail" in args
    files = [a for a in args if not a.startswith("--")]
    if not files:
        print(__doc__)
        return 1

    table = []
    for index, path in enumerate(files, start=1):
        rows, per_label = load(path)
        reads = reads_only(per_label, rows)
        secs = elapsed_seconds(rows)
        table.append(
            {
                "threads": parse_threads(path, index),
                "path": path,
                "total": len(rows),
                "secs": secs,
                "tput": len(rows) / secs if secs else 0.0,
                "reads": reads,
                "per_label": per_label,
            }
        )
        if detail:
            print(f"\n--- 明细：{path}（并发 {table[-1]['threads']}）---")
            print(f"{'采样器':<44}{'n':>6}{'Avg':>9}{'P50':>9}{'P95':>9}{'P99':>9}{'Max':>9}{'err%':>8}")
            for label, s in sorted(per_label.items()):
                print(
                    f"{label:<44}{s['n']:>6}{fmt(s['avg'])}{fmt(s['p50'])}{fmt(s['p95'])}"
                    f"{fmt(s['p99'])}{fmt(s['max'])}{fmt(s['err%'], 8, 2)}"
                )

    table.sort(key=lambda r: r["threads"])
    print("\n===== 阶梯加压对比（只读接口总体） =====")
    print(
        f"{'并发':>5}{'样本':>8}{'时长s':>9}{'吞吐req/s':>12}"
        f"{'Avg':>10}{'P50':>10}{'P95':>10}{'P99':>10}{'Max':>10}{'err%':>8}"
    )
    for r in table:
        s = r["reads"]
        print(
            f"{r['threads']:>5}{r['total']:>8}{fmt(r['secs'], 9)}{fmt(r['tput'], 12)}"
            f"{fmt(s.get('avg', 0), 10)}{fmt(s.get('p50', 0), 10)}{fmt(s.get('p95', 0), 10)}"
            f"{fmt(s.get('p99', 0), 10)}{fmt(s.get('max', 0), 10)}{fmt(s.get('err%', 0), 8, 2)}"
        )

    # 拐点：吞吐增幅 < 10% 且 P95 相对上一档上涨 > 50%
    print("\n===== 拐点判定 =====")
    knee = None
    for prev, cur in zip(table, table[1:]):
        gain = cur["tput"] / prev["tput"] if prev["tput"] else 0
        jump = cur["reads"].get("p95", 0) / prev["reads"].get("p95", 1) if prev["reads"].get("p95") else 0
        flag = ""
        if gain < 1.10 and jump > 1.50:
            flag = "  <== 拐点"
            knee = knee or prev["threads"]
        print(
            f"并发 {prev['threads']:>3} -> {cur['threads']:>3}：吞吐 {prev['tput']:>9.1f} -> {cur['tput']:>9.1f}"
            f"（x{gain:.2f}）｜P95 {prev['reads'].get('p95', 0):>9.1f} -> {cur['reads'].get('p95', 0):>9.1f}"
            f"（x{jump:.2f}）{flag}"
        )
    print(
        f"\n结论：容量拐点约在并发 {knee} 附近（该档之后吞吐不再增长、P95 明显抬头）"
        if knee
        else "\n结论：本次测试范围内未出现明显拐点（吞吐持续增长、P95 未突增）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
