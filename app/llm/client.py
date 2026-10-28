"""统一 LLM 入口：按任务类型路由到 DeepSeek / GLM，并统一降级到规则实现。

两种模式：
- real：解析/分类/工具规划/审核走 DeepSeek，方案与通知生成走 GLM；
  单个模型异常时自动切到另一模型，两者都不可用时降级到 mock；
- mock：直接走 `app.llm.mock` 的规则实现，无需 API Key。

对外方法签名在两种模式下完全一致，上层（LangGraph 节点）无需分支判断。
"""

from functools import lru_cache

from app.config import settings
from app.constants import FAULT_TYPES, FAULT_TYPE_OTHER, URGENCIES, URGENCY_DEFAULT
from app.llm import mock
from app.llm.router import ModelRouter, RouteResult
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
  描述清晰、关键词明确时给 0.8 以上；描述模糊、信息不足或存在歧义时给 0.5 以下。
同时顺带判定该工单能否自动解决，补充三个字段：
- auto_resolvable: true/false。常见且有标准处置流程的软性问题（打印机脱机、密码重置、邮箱配置、客户端卡顿等）→ true；
  服务器/数据库/网络核心/安全事件、业务中断、需现场处理或更换硬件 → false；
  描述中出现冒烟、冒火、着火、漏电、触电、爆炸等安全词时一律 false。
- classify_reason: 判定理由，30 字以内
- classify_confidence: 0~1，表示你对这条判定的把握，信息不足时给 0.5 以下"""

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
    """统一的 LLM 调用入口（内部按任务路由到不同模型）。"""

    def __init__(self) -> None:
        self.mode = settings.effective_llm_mode
        self._router: ModelRouter | None = None
        if self.mode == "real":
            self._router = ModelRouter()
            logger.info(
                "LLM 模式：real（推理模型 %s｜生成模型 %s）",
                self._router.model_name("deepseek") or "未配置",
                self._router.model_name("glm") or "未配置",
            )
        else:
            logger.info("LLM 模式：mock（离线规则，无需 API Key）")

    # ------------------------------------------------------------------ #
    # 底层：按任务路由一次调用；失败返回 None，由调用方降级
    # ------------------------------------------------------------------ #
    def _call(
        self,
        task: str,
        messages: list[dict],
        *,
        json_mode: bool = False,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
    ) -> RouteResult | None:
        """按任务分发到对应模型；任何异常都降级为 None（交给规则实现）。"""
        if self.mode != "real" or self._router is None:
            return None
        try:
            return self._router.chat(
                task,
                messages,
                json_mode=json_mode,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("任务 %s 模型调用失败，降级为规则实现：%s", task, exc)
            return None

    # ------------------------------------------------------------------ #
    # 1) 工单解析
    # ------------------------------------------------------------------ #
    def extract_ticket(self, description: str, contact: str | None = None) -> TicketParseResult:
        """抽取工单字段（DeepSeek）；失败或空结果时降级为规则解析。"""
        result = self._call(
            "parse",
            [
                {"role": "system", "content": PARSE_SYSTEM_PROMPT},
                {"role": "user", "content": f"用户描述：{description}\n联系方式：{contact or '未提供'}"},
            ],
            json_mode=True,
            max_tokens=550,
        )
        if result is not None:
            data = extract_json(result.content)
            if data:
                return self._normalize_parse(data, description, contact)
            logger.warning("解析结果为空，降级为规则解析")
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
            auto_resolvable=(
                bool(data["auto_resolvable"]) if data.get("auto_resolvable") is not None else None
            ),
            classify_reason=str(data.get("classify_reason") or "").strip(),
            classify_confidence=clamp_confidence(data.get("classify_confidence")),
        )

    # ------------------------------------------------------------------ #
    # 2) 意图 & 故障分类
    # ------------------------------------------------------------------ #
    def classify(self, parsed: TicketParseResult, description: str = "") -> ClassifyResult:
        """判断「可自动解决 / 必须人工处理」（DeepSeek）。"""
        result = self._call(
            "classify",
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
            max_tokens=200,
        )
        if result is not None:
            data = extract_json(result.content)
            if "auto_resolvable" in data:
                return ClassifyResult(
                    auto_resolvable=bool(data["auto_resolvable"]),
                    reason=str(data.get("reason") or ""),
                    confidence=clamp_confidence(data.get("confidence")),
                )
            logger.warning("分类结果缺少 auto_resolvable，降级为规则分类")
        return mock.classify(parsed, description)

    # ------------------------------------------------------------------ #
    # 3) 原生 Function Calling
    # ------------------------------------------------------------------ #
    def chat_with_tools(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        """携带工具声明调用模型（DeepSeek），返回归一化的 tool_calls。"""
        result = self._call("tools", messages, max_tokens=250, tools=tools)
        if result is not None:
            return LLMResponse(content=result.content, tool_calls=result.tool_calls)
        return mock.chat_with_tools(messages, tools)

    # ------------------------------------------------------------------ #
    # 4) 结果生成
    # ------------------------------------------------------------------ #
    def generate_resolution(
        self, context: dict, matches: list[dict], device: dict | None = None
    ) -> str:
        """生成自动闭环的解决步骤（GLM，长文本生成）。"""
        history = "\n".join(
            f"- #{m['ticket_id']}《{m['title']}》匹配度{m['score']}：{m['resolution']}"
            for m in (matches or [])[:3]
        )
        result = self._call(
            "resolution",
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
            max_tokens=600,
        )
        if result is not None and result.content.strip():
            return result.content
        return mock.generate_resolution(context, matches, device)

    def generate_notification(self, context: dict, assignee: dict, reason: str) -> str:
        """生成转人工通知消息（GLM，对话式表达）。"""
        result = self._call(
            "notification",
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
            max_tokens=250,
        )
        if result is not None and result.content.strip():
            return result.content
        return mock.generate_notification(context, assignee, reason)

    # ------------------------------------------------------------------ #
    # 5) 方案审核（审核 Agent 自检）
    # ------------------------------------------------------------------ #
    def review_resolution(
        self, context: dict, resolution: str, matches: list[dict], device: dict | None = None
    ) -> ReviewResult:
        """对自动闭环方案做交叉审核（DeepSeek），返回 pass / reject。"""
        history = "、".join(f"#{m['ticket_id']}" for m in (matches or [])[:3]) or "无"
        result = self._call(
            "review",
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
            max_tokens=200,
        )
        if result is not None:
            data = extract_json(result.content)
            verdict = str(data.get("verdict") or "").strip().lower()
            if verdict in ("pass", "reject"):
                return ReviewResult(
                    verdict=verdict,
                    feedback=str(data.get("feedback") or ""),
                    confidence=clamp_confidence(data.get("confidence")),
                )
            logger.warning("审核结论非法（%s），降级为规则审核", verdict)
        return mock.review_resolution(context, resolution, matches, device)


@lru_cache(maxsize=1)
def get_llm_client() -> LLMClient:
    """单例 LLM 客户端。"""
    return LLMClient()