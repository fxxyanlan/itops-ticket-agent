"""工具注册表：集中声明大模型可调用的工具，并统一分发执行。

- `TOOL_SPECS`：传给 DeepSeek 的 OpenAI Tools 声明（原生 Function Calling）。
- `dispatch()`：按 tool_call.name 路由到对应工具函数，异常统一转结构化错误。
"""

import json
from typing import Any, Callable

from app.logging_config import get_logger
from app.tools import asset_query, ticket_search, ticket_status

logger = get_logger("itops.tools")

# 工具声明（顺序即模型看到的顺序）
TOOL_SPECS: list[dict] = [
    ticket_search.SPEC,
    asset_query.SPEC,
    ticket_status.SPEC,
]

# 名称 → 执行函数
_TOOL_FUNCS: dict[str, Callable[..., dict]] = {
    "search_similar_tickets": ticket_search.run,
    "get_device_asset": asset_query.run,
    "update_ticket_status": ticket_status.run,
}


def tool_names() -> list[str]:
    """返回已注册的工具名列表。"""
    return list(_TOOL_FUNCS.keys())


def _parse_arguments(arguments: Any) -> dict:
    """tool_call.arguments 可能是 JSON 字符串，也可能已是 dict。"""
    if arguments is None:
        return {}
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        text = arguments.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except json.JSONDecodeError:
            logger.warning("工具参数 JSON 解析失败：%s", text[:200])
            return {}
    return {}


def dispatch(name: str, arguments: Any) -> dict:
    """执行一次工具调用，返回结构化结果（异常不抛出，转为 error 字段）。"""
    func = _TOOL_FUNCS.get(name)
    if func is None:
        logger.error("未知工具：%s", name)
        return {"ok": False, "error": f"未知工具：{name}"}

    kwargs = _parse_arguments(arguments)
    try:
        result = func(**kwargs)
        return result if isinstance(result, dict) else {"ok": True, "result": result}
    except TypeError as exc:  # 参数不匹配（模型幻觉出多余字段等）
        logger.error("工具 %s 参数不匹配：%s", name, exc)
        return {"ok": False, "error": f"参数不匹配：{exc}"}
    except Exception as exc:  # noqa: BLE001  工具异常不应中断整张图
        logger.exception("工具 %s 执行异常", name)
        return {"ok": False, "error": f"工具执行异常：{exc}"}