"""LangGraph 状态机直跑测试：单条工单打印每个节点后的状态与日志。

用法：
    py scripts/test_graph.py                          # 用默认样例（打印机）
    py scripts/test_graph.py "核心数据库宕机，业务中断"  # 自定义描述
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent.graph import get_graph  # noqa: E402
from app.agent.state import initial_state  # noqa: E402
from app.db.base import init_db, session_scope  # noqa: E402
from app.db.seed import seed_all  # noqa: E402
from app.services import ticket_service  # noqa: E402

DEFAULT_DESC = "我的打印机 PC-1024 打印一直提示脱机，重启打印服务也不行"


def main() -> None:
    description = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DESC

    init_db()
    with session_scope() as session:
        seed_all(session)

    ticket = ticket_service.create_ticket(description=description, user_contact="测试用户 / 13000000000")
    print(f"新建工单 #{ticket['id']}：{description}\n")

    state = initial_state(ticket)
    print("===== 逐节点追踪（stream）=====")
    for chunk in get_graph().stream(state, config={"recursion_limit": 60}):
        for node_name, node_state in chunk.items():
            if node_name == "__start__" or node_state is None:
                continue
            node_state = node_state or {}
            summary = {
                "title": node_state.get("title"),
                "fault_type": node_state.get("fault_type"),
                "urgency": node_state.get("urgency"),
                "decision": node_state.get("decision"),
                "final_status": node_state.get("final_status"),
                "iterations": node_state.get("iterations"),
            }
            print(f"  ▶ {node_name:<16} {json.dumps(summary, ensure_ascii=False)}")

    print("\n===== 最终结果 =====")
    final = ticket_service.get_ticket(ticket["id"])
    print(json.dumps({k: v for k, v in final.items() if k != "resolution"}, ensure_ascii=False, indent=2))
    print("\n===== 解决步骤 / 通知消息 =====")
    print(final.get("resolution") or final.get("notify_message") or "（无）")

    print("\n===== Agent 日志（前 12 条）=====")
    for log in ticket_service.list_logs(ticket["id"])[:12]:
        print(f"  [{log['level']:<5}] {log['node']:<26} {log['message']}")


if __name__ == "__main__":
    main()