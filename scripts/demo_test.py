"""端到端验收脚本：批量提交 5 条样例工单并比对期望结果（spec §11）。

用法：
    py scripts/demo_test.py          # 进程内直接跑 Agent（无需启动后端）
    py scripts/demo_test.py --http   # 通过 HTTP 调用已启动的后端
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.samples import SAMPLES  # noqa: E402  (直接运行时可导入同目录模块)


def run_local() -> list[dict]:
    """进程内执行：建库 → 种子 → 逐条提交并跑 Agent。"""
    from app.db.base import init_db, session_scope
    from app.db.seed import seed_all
    from app.services import ticket_service
    from app.services.agent_runner import run_agent

    init_db()
    with session_scope() as session:
        print(seed_all(session))

    results = []
    for index, sample in enumerate(SAMPLES, start=1):
        ticket = ticket_service.create_ticket(description=sample["desc"], user_contact=sample["contact"])
        outcome = run_agent(ticket["id"])
        results.append({"ticket": outcome["ticket"], "state": outcome["state"], "index": index})
    return results


def run_http() -> list[dict]:
    """通过 HTTP 调用后端。"""
    import requests

    from app.config import settings

    base = settings.backend_url.rstrip("/")
    results = []
    for index, sample in enumerate(SAMPLES, start=1):
        resp = requests.post(
            f"{base}/api/tickets",
            json={"description": sample["desc"], "user_contact": sample["contact"]},
            timeout=180,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        results.append({"ticket": data["ticket"], "state": data["state"], "index": index})
    return results


def main() -> int:
    use_http = "--http" in sys.argv
    print(f"运行模式：{'HTTP 调用后端' if use_http else '进程内直接执行'}\n")
    results = run_http() if use_http else run_local()

    passed = 0
    for sample, result in zip(SAMPLES, results):
        ticket, state = result["ticket"], result["state"]
        ok_status = ticket["status"] == sample["expect_status"]
        ok_mode = ticket["resolution_mode"] == sample["expect_mode"]
        ok = ok_status and ok_mode
        passed += 1 if ok else 0

        print(f"{'✅ PASS' if ok else '❌ FAIL'}  样例{result['index']}：{sample['desc'][:30]}...")
        print(
            f"    类型={ticket['fault_type']} 等级={ticket['urgency']} "
            f"状态={ticket['status']} 模式={ticket['resolution_mode']} "
            f"匹配度={state.get('best_match_score')} 工具轮数={state.get('iterations')}"
        )
        print(f"    决策原因：{ticket['decision_reason']}")
        if ticket["assignee"]:
            print(f"    分派工程师：{ticket['assignee']}")
        print(f"    期望：{sample['expect_status']} / {sample['expect_mode']}｜说明：{sample['note']}\n")

    print(f"===== 结果：{passed}/{len(SAMPLES)} 通过 =====")
    return 0 if passed == len(SAMPLES) else 1


if __name__ == "__main__":
    raise SystemExit(main())