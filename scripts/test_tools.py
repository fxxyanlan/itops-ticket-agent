"""工具层自测：脱离 LLM 直接调用 3 个工具，验证返回结构。

用法：py scripts/test_tools.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.base import init_db  # noqa: E402
from app.tools.registry import TOOL_SPECS, dispatch, tool_names  # noqa: E402


def show(title: str, payload: object) -> None:
    print(f"\n===== {title} =====")
    print(json.dumps(payload, ensure_ascii=False, indent=2)[:1200])


def main() -> None:
    init_db()

    print("已注册工具：", tool_names())
    print("工具声明数量：", len(TOOL_SPECS))

    show(
        "工具1 检索历史工单（打印机）",
        dispatch(
            "search_similar_tickets",
            {"fault_type": "打印机", "keywords": "打印机一直提示脱机，重启打印服务也不行"},
        ),
    )
    show(
        "工具1 检索历史工单（软件-示波器，预期低分）",
        dispatch(
            "search_similar_tickets",
            {"fault_type": "软件", "keywords": "公司新采购的示波器驱动装不上，型号 XYZ-9"},
        ),
    )
    show("工具2 查询设备资产（PC-1024）", dispatch("get_device_asset", {"device_id": "PC-1024"}))
    show("工具2 查询不存在的设备", dispatch("get_device_asset", {"device_id": "NOT-EXIST-9"}))
    show(
        "工具3 更新工单状态（非法状态）",
        dispatch("update_ticket_status", {"ticket_id": 1, "status": "已关闭"}),
    )


if __name__ == "__main__":
    main()