"""LLM 结构化输出模型与 JSON 解析工具。"""

import json
import re

from pydantic import BaseModel, Field

from app.constants import FAULT_TYPE_OTHER, URGENCY_DEFAULT


class TicketParseResult(BaseModel):
    """工单解析结果（节点 parse_ticket 使用）。"""

    title: str = Field(default="", description="工单标题")
    fault_type: str = Field(default=FAULT_TYPE_OTHER, description="故障类型")
    urgency: str = Field(default=URGENCY_DEFAULT, description="紧急等级 P0/P1/P2/P3")
    device_id: str | None = Field(default=None, description="设备编号")
    user_contact: str | None = Field(default=None, description="用户联系方式")
    summary: str = Field(default="", description="一句话问题摘要")


class ClassifyResult(BaseModel):
    """意图 & 故障分类结果（节点 classify 使用）。"""

    auto_resolvable: bool = Field(description="是否属于可自动解决的工单")
    reason: str = Field(default="", description="判定理由")


class ToolCall(BaseModel):
    """归一化后的工具调用（对应 OpenAI tool_calls 结构）。"""

    id: str
    name: str
    arguments: dict


class LLMResponse(BaseModel):
    """一次模型回复的归一化结果。"""

    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict:
    """从模型输出中稳健地提取 JSON 对象。

    依次尝试：直接解析 → 去掉 markdown 代码围栏 → 截取首个 { 到末个 }。
    """
    if not text:
        return {}

    candidates: list[str] = [text.strip()]

    fenced = _FENCE_RE.search(text)
    if fenced:
        candidates.append(fenced.group(1).strip())

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return {}