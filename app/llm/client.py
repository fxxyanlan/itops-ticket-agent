"""DeepSeek 客户端（OpenAI 兼容协议）。

两种模式：
- real：调用 DeepSeek Chat Completions，支持 `response_format=json_object` 与原生 `tools`；
- mock：走 `app.llm.mock` 的规则实现，无需 API Key。

对外方法签名在两种模式下完全一致，上层（LangGraph 节点）无需分支判断。
真实调用失败时自动降级到 mock，保证流程不中断（记录 WARN 日志）。
"""

from functools import lru_cache

from app.config import settings
from app.constants import FAULT_TYPES, FAULT_TYPE_OTHER, URGENCIES, URGENCY_DEFAULT
from app.llm import mock
from app.llm.schemas import ClassifyResult, LLMResponse, ReviewResult, TicketParseResult, ToolCall, extract_json
from app.logging_config import get_logger
from app.services.safety_gate import clamp_confidence, format_urgency_guide

logger = get_logger("itops.llm")

PARSE_SYSTEM_PROMPT = f"""你是 IT 运维工单解析助手。请从用户描述中抽取结构化字段，只输出 JSON（不要任何解释或 markdown 包裹）。
字段要求：
- title: 工单标题，20 字以内
- fault_type: 必须是以下之一：打印机/账号权限/网络/硬件/软件/服务器/邮箱/数据库/安全事件/其他
- urgency: 必须是 P0/P1/P2/P3 之一，判定标准如下：
{format_urgency_guide()}
- device_id: 设备编号（形如 PC-1024、SRV-DB-01），没有则为 null；注意不要填设备型号
- user_contact: 用户联系方式，没有则为 null
- summary: 一句话问题摘要
- confidence: 0~1 的小数，表示你对 fault_type 和 urgency 判断的整体把握。
  描述清晰、关键词明确时给 0.8 以上；描述模糊、信息不足或存在歧义时给 0.5 以下。"""

CLASSIFY_SYSTEM_PROMPT = """你是 IT 运维工单分类助手，判断工单是否属于【可自动解决】。
只输出 JSON：{"auto_resolvable": true/false, "reason": "判定理由(30字内)", "confidence": 0~1}
判定准则：
- 常见且有标准处置流程的软性问题（打印机脱机、密码重置、客户端卡顿、邮箱配置等）→ true
- 服务器/数据库/网络核心/安全事件等高风险问题，或业务中断、需要现场/更换硬件 → false
- 描述中出现冒烟、冒火、着火、漏电、触电、爆炸等安全词时，无论看起来像什么普通故障，都必须 → false
- confidence 表示你对本次判定的把握：依据充分给 0.8 以上，信息不足给 0.5 以下"""

RESOLUTION_SYSTEM_PROMPT = """你是 IT 运维工程师。请依据历史工单的处置经验，为该工单生成清晰的解决步骤。
要求：使用 Markdown，先给「设备信息」与「匹配到的历史工单」，再给分步骤的「建议处理步骤」，最后附一句风险提示。不要输出多余寒暄。"""

NOTIFY_SYSTEM_PROMPT = """你是 IT 运维调度助手。请为转人工的工单生成一条简洁的通知消息，包含：工单号、标题、紧急等级、故障类型、转人工原因、分派工程师及其联系方式、预计响应时长，并说明已同步到运维值班群。只输出通知正文。"""

CRITIC_SYSTEM_PROMPT = """你是 IT 运维方案审核专家，负责对「自动闭环」方案做上线前自检。
只输出 JSON：{"verdict": "pass" 或 "reject", "feedback": "审核意见(40字内)", "confidence": 0~1}
审核要点：
- 方案步骤是否完整、可执行，且与故障类型一致；
- 是否体现了设备信息与历史工单依据；
- 是否存在高危操作或未提示的风险。
全部满足返回 pass；任一不满足返回 reject，并给出可执行的修改意见。
confidence 表示你对本次审核结论的把握，依据不足时给 0.5 以下。"""


class LLMClient:
    """统一的 LLM 调用入口。"""

    def __init__(self) -> None:
        self.mode = settings.effective_llm_mode
        self.model = settings.deepseek_model
        self._client = None
        if self.mode == "real":
            try:
                from openai import OpenAI

                self._client = OpenAI(
                    api_key=settings.deepseek_api_key,
                    base_url=settings.deepseek_base_url,
                    timeout=settings.llm_timeout,
                )
                logger.info("LLM 模式：real（模型 %s）", self.model)
            except Exception as exc:  # noqa: BLE001  依赖缺失或初始化失败 → 降级
                logger.error("初始化 DeepSeek 客户端失败，降级为 mock：%s", exc)
                self.mode = "mock"
        else:
            logger.info("LLM 模式：mock（离线规则，无需 API Key）")

    # ------------------------------------------------------------------ #
    # 底层：一次真实模型调用
    # ------------------------------------------------------------------ #
    def _real_chat(self, messages: list[dict], json_mode: bool = False, temperature: float = 0.2) -> str:
        """调用 DeepSeek 并返回文本内容。"""
        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = self._client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""

    # ------------------------------------------------------------------ #
    # 1) 工单解析
    # ------------------------------------------------------------------ #
    def extract_ticket(self, description: str, contact: str | None = None) -> TicketParseResult:
        """抽取工单字段；失败时降级为规则解析。"""
        if self.mode == "real":
            try:
                content = self._real_chat(
                    [
                        {"role": "system", "content": PARSE_SYSTEM_PROMPT},
                        {"role": "user", "content": f"用户描述：{description}\n联系方式：{contact or '未提供'}"},
                    ],
                    json_mode=True,
                )
                data = extract_json(content)
                if data:
                    return self._normalize_parse(data, description, contact)
                logger.warning("解析结果为空，降级为规则解析")
            except Exception as exc:  # noqa: BLE001
                logger.error("模型解析失败，降级为规则解析：%s", exc)
        return mock.parse_ticket(description, contact)

    @staticmethod
    def _normalize_parse(data: dict, description: str, contact: str | None) -> TicketParseResult:
        """约束模型输出到合法枚举，非法值回退默认。"""
        fault_type = str(data.get("fault_type") or FAULT_TYPE_OTHER).strip()
        urgency = str(data.get("urgency") or URGENCY_DEFAULT).strip().upper()
        return TicketParseResult(
            title=str(data.get("title") or "").strip()[:60] or description[:24],
            fault_type=fault_type if fault_type in FAULT_TYPES else FAULT_TYPE_OTHER,
            urgency=urgency if urgency in URGENCIES else URGENCY_DEFAULT,
            device_id=(str(data["device_id"]).strip() or None) if data.get("device_id") else None,
            user_contact=(str(data.get("user_contact")).strip() or None) if data.get("user_contact") else contact,
            summary=str(data.get("summary") or "").strip(),
            confidence=clamp_confidence(data.get("confidence")),
        )

    # ------------------------------------------------------------------ #
    # 2) 意图 & 故障分类
    # ------------------------------------------------------------------ #
    def classify(self, parsed: TicketParseResult, description: str = "") -> ClassifyResult:
        """判断「可自动解决 / 必须人工处理」。"""
        if self.mode == "real":
            try:
                content = self._real_chat(
                    [
                        {"role": "system", "content": CLASSIFY_SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": (
                                f"故障类型：{parsed.fault_type}\n紧急等级：{parsed.urgency}\n"
                                f"用户描述：{description or parsed.summary}"
                            ),
                        },
                    ],
                    json_mode=True,
                )
                data = extract_json(content)
                if "auto_resolvable" in data:
                    return ClassifyResult(
                        auto_resolvable=bool(data["auto_resolvable"]),
                        reason=str(data.get("reason") or ""),
                        confidence=clamp_confidence(data.get("confidence")),
                    )
                logger.warning("分类结果缺少 auto_resolvable，降级为规则分类")
            except Exception as exc:  # noqa: BLE001
                logger.error("模型分类失败，降级为规则分类：%s", exc)
        return mock.classify(parsed, description)

    # ------------------------------------------------------------------ #
    # 3) 原生 Function Calling
    # ------------------------------------------------------------------ #
    def chat_with_tools(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        """携带工具声明调用模型，返回归一化的 tool_calls。"""
        if self.mode == "real":
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=0.2,
                )
                message = response.choices[0].message
                tool_calls: list[ToolCall] = []
                for call in message.tool_calls or []:
                    tool_calls.append(
                        ToolCall(
                            id=call.id,
                            name=call.function.name,
                            arguments=extract_json(call.function.arguments) or {},
                        )
                    )
                return LLMResponse(content=message.content or "", tool_calls=tool_calls)
            except Exception as exc:  # noqa: BLE001
                logger.error("模型工具调用失败，降级为规则决策：%s", exc)
        return mock.chat_with_tools(messages, tools)

    # ------------------------------------------------------------------ #
    # 4) 结果生成
    # ------------------------------------------------------------------ #
    def generate_resolution(
        self, context: dict, matches: list[dict], device: dict | None = None
    ) -> str:
        """生成自动闭环的解决步骤（Markdown）。"""
        if self.mode == "real":
            try:
                history = "\n".join(
                    f"- #{m['ticket_id']}《{m['title']}》匹配度{m['score']}：{m['resolution']}"
                    for m in (matches or [])[:3]
                )
                content = self._real_chat(
                    [
                        {"role": "system", "content": RESOLUTION_SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": (
                                f"工单：{context.get('title')}\n故障类型：{context.get('fault_type')}\n"
                                f"问题描述：{context.get('description')}\n"
                                f"设备信息：{device or '未查到'}\n历史工单：\n{history or '无'}"
                            ),
                        },
                    ],
                    temperature=0.3,
                )
                if content.strip():
                    return content
            except Exception as exc:  # noqa: BLE001
                logger.error("模型生成方案失败，降级为模板：%s", exc)
        return mock.generate_resolution(context, matches, device)

    def generate_notification(self, context: dict, assignee: dict, reason: str) -> str:
        """生成转人工通知消息。"""
        if self.mode == "real":
            try:
                content = self._real_chat(
                    [
                        {"role": "system", "content": NOTIFY_SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": (
                                f"工单号：#{context.get('ticket_id')}，标题：{context.get('title')}，"
                                f"紧急等级：{context.get('urgency')}，故障类型：{context.get('fault_type')}，"
                                f"转人工原因：{reason}，分派工程师：{assignee}"
                            ),
                        },
                    ],
                    temperature=0.3,
                )
                if content.strip():
                    return content
            except Exception as exc:  # noqa: BLE001
                logger.error("模型生成通知失败，降级为模板：%s", exc)
        return mock.generate_notification(context, assignee, reason)

    # ------------------------------------------------------------------ #
    # 5) 方案审核（审核 Agent 自检）
    # ------------------------------------------------------------------ #
    def review_resolution(
        self, context: dict, resolution: str, matches: list[dict], device: dict | None = None
    ) -> ReviewResult:
        """对自动闭环方案做交叉审核，返回 pass / reject。"""
        if self.mode == "real":
            try:
                history = "、".join(f"#{m['ticket_id']}" for m in (matches or [])[:3]) or "无"
                content = self._real_chat(
                    [
                        {"role": "system", "content": CRITIC_SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": (
                                f"工单：{context.get('title')}\n故障类型：{context.get('fault_type')}\n"
                                f"问题描述：{context.get('description')}\n设备信息：{device or '未查到'}\n"
                                f"依据的历史工单：{history}\n待审核方案：\n{resolution or '（空）'}"
                            ),
                        },
                    ],
                    json_mode=True,
                )
                data = extract_json(content)
                verdict = str(data.get("verdict") or "").strip().lower()
                if verdict in ("pass", "reject"):
                    return ReviewResult(
                        verdict=verdict,
                        feedback=str(data.get("feedback") or ""),
                        confidence=clamp_confidence(data.get("confidence")),
                    )
                logger.warning("审核结论非法（%s），降级为规则审核", verdict)
            except Exception as exc:  # noqa: BLE001
                logger.error("模型审核失败，降级为规则审核：%s", exc)
        return mock.review_resolution(context, resolution, matches, device)


@lru_cache(maxsize=1)
def get_llm_client() -> LLMClient:
    """单例 LLM 客户端。"""
    return LLMClient()