"""离线 mock LLM：无需 API Key，用规则/模板模拟模型行为。

用途：
1. 无 Key 环境下也能完整演示「解析 → 分类 → 工具调用 → 方案生成」全流程；
2. 真实模式下模型异常时作为降级兜底。

关键点：`chat_with_tools` 通过系统提示词里的【工单上下文】JSON 还原工单信息，
并按「检索 → 资产 → 更新状态」的顺序返回 tool_calls，行为可预测、必然收敛。
"""

import json
import re

from app.constants import (
    AGENT_CONTEXT_MARKER,
    FAULT_TYPE_OTHER,
    HIGH_RISK_TYPES,
    URGENCIES,
    URGENCY_CRITICAL,
    URGENCY_DEFAULT,
)
from app.llm.schemas import ClassifyResult, LLMResponse, TicketParseResult, ToolCall

# ===== 关键词规则 =====
# 故障类型识别（顺序敏感：先匹配到的优先）
_FAULT_TYPE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("安全事件", ("病毒", "木马", "攻击", "泄露", "勒索", "入侵", "钓鱼")),
    ("数据库", ("数据库", "mysql", "oracle", "sql", "db 连接", "表锁", "死锁")),
    ("服务器", ("服务器", "宕机", "系统盘", "虚拟机", "集群", "机房")),
    ("打印机", ("打印机", "打印", "脱机", "卡纸", "墨盒", "碳粉", "扫描仪")),
    ("账号权限", ("密码", "账号", "账户", "权限", "vpn", "登录不了", "锁定", "解锁", "认证")),
    ("邮箱", ("邮箱", "邮件", "outlook", "smtp", "imap", "收不到信")),
    ("网络", ("网络", "断网", "网速", "wifi", "无线", "路由器", "连不上网", "无法上网", "ip 地址")),
    ("硬件", ("投影仪", "显示器", "黑屏", "键盘", "鼠标", "硬盘", "内存", "风扇", "无信号", "电池")),
    ("软件", ("软件", "客户端", "程序", "安装", "驱动", "闪退", "报错", "崩溃", "卡死")),
]

# 紧急等级识别
_P0_KEYWORDS = ("宕机", "瘫痪", "全部中断", "业务中断", "全公司", "数据泄露", "无法访问", "起火", "服务器down")
_P1_KEYWORDS = ("紧急", "尽快", "严重", "大量", "影响工作", "无法使用", "急")
_P3_KEYWORDS = ("咨询", "请问", "有空", "不急", "建议", "了解一下")

# 从描述中抽取设备编号（排除「型号 xxx」，避免把设备型号误当资产编号）
_DEVICE_RE = re.compile(r"\b([A-Za-z]{2,6}-\d{1,6})\b")
_MODEL_RE = re.compile(r"型号\s*\S+")
_CONTEXT_RE = re.compile(re.escape(AGENT_CONTEXT_MARKER) + r"\s*(\{[^\n]*\})")


def _detect_fault_type(text: str) -> str:
    lowered = text.lower()
    for fault_type, keywords in _FAULT_TYPE_RULES:
        if any(keyword in lowered for keyword in keywords):
            return fault_type
    return FAULT_TYPE_OTHER


def _detect_urgency(text: str) -> str:
    if any(keyword in text for keyword in _P0_KEYWORDS):
        return URGENCY_CRITICAL
    if any(keyword in text for keyword in _P1_KEYWORDS):
        return "P1"
    if any(keyword in text for keyword in _P3_KEYWORDS):
        return "P3"
    return URGENCY_DEFAULT


def _detect_device_id(text: str) -> str | None:
    cleaned = _MODEL_RE.sub("", text)
    match = _DEVICE_RE.search(cleaned)
    return match.group(1).upper() if match else None


def parse_ticket(description: str, contact: str | None = None) -> TicketParseResult:
    """规则版工单解析。"""
    text = (description or "").strip()
    fault_type = _detect_fault_type(text)
    title = re.split(r"[，。！？,\.!\?；;\n]", text)[0].strip()[:24] or f"{fault_type}类故障"
    return TicketParseResult(
        title=title,
        fault_type=fault_type,
        urgency=_detect_urgency(text),
        device_id=_detect_device_id(text),
        user_contact=contact,
        summary=text[:60],
    )


def classify(parsed: TicketParseResult, description: str = "") -> ClassifyResult:
    """规则版分类：高危类型 / P0 / 有明确人工关键词 → 需人工处理。"""
    if parsed.urgency not in URGENCIES:
        parsed.urgency = URGENCY_DEFAULT
    if parsed.fault_type in HIGH_RISK_TYPES:
        return ClassifyResult(
            auto_resolvable=False,
            reason=f"故障类型「{parsed.fault_type}」属于高危场景，必须人工介入",
        )
    if parsed.urgency == URGENCY_CRITICAL:
        return ClassifyResult(auto_resolvable=False, reason="紧急等级为 P0，需人工立即响应")
    if any(word in description for word in ("派工程师", "上门", "现场", "更换硬件")):
        return ClassifyResult(auto_resolvable=False, reason="用户明确要求现场/人工处理")
    return ClassifyResult(
        auto_resolvable=True,
        reason=f"常见「{parsed.fault_type}」类问题，可按历史方案自助处理",
    )


def _extract_context(messages: list[dict]) -> dict:
    """从系统提示词中还原工单上下文 JSON。"""
    for message in messages:
        if message.get("role") == "system":
            match = _CONTEXT_RE.search(str(message.get("content", "")))
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    continue
    return {}


def _called_tools(messages: list[dict]) -> set[str]:
    """统计已执行过的工具名（从 tool 消息的 name 字段读取）。"""
    return {str(m.get("name")) for m in messages if m.get("role") == "tool"}


def chat_with_tools(messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
    """规则版工具调用决策：检索 → 资产 → 更新状态（处理中）。"""
    context = _extract_context(messages)
    called = _called_tools(messages)
    ticket_id = context.get("ticket_id")
    fault_type = context.get("fault_type") or FAULT_TYPE_OTHER
    description = context.get("description") or ""
    device_id = context.get("device_id")

    # 第一轮：检索历史方案（必需）+ 查询设备资产（有编号时）
    if "search_similar_tickets" not in called:
        calls = [
            ToolCall(
                id="mock_search_1",
                name="search_similar_tickets",
                arguments={"fault_type": fault_type, "keywords": description, "top_k": 3},
            )
        ]
        if device_id:
            calls.append(
                ToolCall(
                    id="mock_asset_1",
                    name="get_device_asset",
                    arguments={"device_id": device_id},
                )
            )
        return LLMResponse(
            content=f"我先检索「{fault_type}」类历史工单，并核对设备 {device_id or '（未提供）'} 的资产信息。",
            tool_calls=calls,
        )

    # 第二轮：把工单置为处理中
    if "update_ticket_status" not in called and ticket_id is not None:
        return LLMResponse(
            content="已受理，先将工单置为「处理中」。",
            tool_calls=[
                ToolCall(
                    id="mock_status_1",
                    name="update_ticket_status",
                    arguments={
                        "ticket_id": int(ticket_id),
                        "status": "处理中",
                        "note": "Agent 已受理，正在自动排查",
                    },
                )
            ],
        )

    # 第三轮：结束工具调用，交回路由判定
    return LLMResponse(content="工具调用完成，进入结果判定。", tool_calls=[])


def generate_resolution(context: dict, matches: list[dict], device: dict | None = None) -> str:
    """规则版解决方案生成：优先复用历史工单的处置步骤。"""
    fault_type = context.get("fault_type") or FAULT_TYPE_OTHER
    lines: list[str] = []

    if device and device.get("found"):
        lines.append(
            f"**设备信息**：{device.get('device_id')}（{device.get('device_type')}，"
            f"位置 {device.get('location')}，责任人 {device.get('owner')}，"
            f"当前状态 {device.get('health_status')}）"
        )

    best = matches[0] if matches else None
    if best:
        lines.append(
            f"**匹配到历史工单** #{best.get('ticket_id')}《{best.get('title')}》"
            f"（匹配度 {best.get('score')}）"
        )
        lines.append("**建议处理步骤**：\n" + str(best.get("resolution") or "").strip())
    else:
        lines.append(f"**通用处置建议（{fault_type}）**：\n1. 复现并记录故障现象；\n2. 检查相关服务与连接状态；\n3. 按标准手册执行修复并回归验证。")

    lines.append("\n> 若执行后仍未恢复，请回复本工单，系统将自动升级为人工处理。")
    return "\n\n".join(lines)


def generate_notification(context: dict, assignee: dict, reason: str) -> str:
    """规则版通知消息生成（转人工场景）。"""
    assignee_name = assignee.get("name", "值班工程师") if assignee else "值班工程师"
    contact = assignee.get("contact", "") if assignee else ""
    level = assignee.get("level", "") if assignee else ""
    return (
        f"【工单转人工通知】工单 #{context.get('ticket_id')}《{context.get('title')}》\n"
        f"紧急等级：{context.get('urgency')}｜故障类型：{context.get('fault_type')}\n"
        f"转人工原因：{reason}\n"
        f"已分派工程师：{assignee_name}（{level}，{contact}），"
        f"预计响应时长约 {assignee.get('avg_hours', 2) if assignee else 2} 小时。\n"
        f"请及时跟进处理。（消息已同步至企业微信运维值班群）"
    )