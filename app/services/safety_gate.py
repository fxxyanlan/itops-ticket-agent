"""入口安全闸门：确定性的紧急等级规则 + 置信度。

设计目标
    最高危机等级（P0）不依赖大模型判断：只要描述命中冒火、冒烟、漏电等关键词，
    立即按 P0 转人工并给出应急提示，避免「模型把火灾判成普通硬件故障」。
    其余等级仍由模型做语义解析，但规则会给出一个确定性的等级与置信度，
    两者取更严重的一档（只升不降），并在置信度不足时保守转人工。

置信度含义
    0~1 的小数，1 表示非常确定。规则置信度由命中关键词的强度和数量决定；
    模型置信度由提示词要求模型自评。低于 `settings.confidence_threshold`
    时，业务层直接转人工，不参与自动闭环。
"""

import re
from dataclasses import dataclass

from app.constants import URGENCY_CRITICAL, URGENCY_DEFAULT

# ===== 紧急等级描述与关键词 =====
# P0 关键词拆成三组，便于同时推断故障类型
_P0_SAFETY_KEYWORDS = (
    "冒火",
    "着火",
    "起火",
    "冒烟",
    "火灾",
    "火苗",
    "明火",
    "烧焦",
    "焦味",
    "糊味",
    "火花",
    "冒火星",
    "烧起来",
    "爆炸",
    "漏电",
    "触电",
    "电池鼓包",
)
_P0_SECURITY_KEYWORDS = ("数据泄露", "勒索", "入侵", "攻击", "钓鱼")
_P0_OUTAGE_KEYWORDS = ("宕机", "瘫痪", "全部中断", "业务中断", "全公司", "服务器down", "机房断电", "机房进水")
_P0_KEYWORDS = _P0_SAFETY_KEYWORDS + _P0_SECURITY_KEYWORDS + _P0_OUTAGE_KEYWORDS

_P1_KEYWORDS = (
    "紧急",
    "尽快",
    "严重",
    "大量",
    "大面积",
    "多个",
    "影响工作",
    "无法使用",
    "全体",
)
_P3_KEYWORDS = ("咨询", "请问", "有空", "不急", "建议", "了解一下", "怎么设置", "如何操作")

URGENCY_DEFINITIONS: dict[str, dict] = {
    "P0": {
        "name": "灾难级",
        "description": (
            "已经或可能造成人身伤害、火灾、爆炸、漏电、全公司业务中断、核心数据泄露等"
            "灾难性后果。出现冒火、着火、冒烟、起火、焦味、火花、爆炸、漏电、触电等词，"
            "直接判为 P0，立即转人工。"
        ),
        "keywords": _P0_KEYWORDS,
    },
    "P1": {
        "name": "严重级",
        "description": (
            "严重影响个人或部门工作，核心设备或系统无法使用，但暂时没有火灾、漏电、"
            "人身伤害等安全风险。"
        ),
        "keywords": _P1_KEYWORDS,
    },
    "P2": {
        "name": "一般级",
        "description": "单个设备或软件出现一般故障，有替代方案，不影响整体业务，也没有安全风险。",
        "keywords": (),
    },
    "P3": {
        "name": "咨询级",
        "description": "咨询、建议或操作指导，不着急，不影响当前工作，也没有安全风险。",
        "keywords": _P3_KEYWORDS,
    },
}

# 「冒烟测试」是软件测试术语，不能当成火灾
_EXCEPTION_PHRASES = ("冒烟测试",)
# 只识别紧邻关键词的否定表达，避免把「无尘室冒烟」误判成否定
_NEGATION_PATTERN = re.compile(
    r"(?:没有|没|未|不是|并非|不|无|别|勿)"
    r"(?:出现|看到|发现|闻到|明显|任何)?$"
)

_LEVEL_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


@dataclass(frozen=True)
class UrgencyAssessment:
    """一次规则判级的结果。"""

    level: str
    confidence: float
    matched_keywords: tuple[str, ...]
    reason: str
    is_safety_critical: bool
    fault_type_hint: str | None

    @property
    def fast_path(self) -> bool:
        """P0 是否走入口快速通道（不调用大模型，直接转人工）。"""
        return self.level == URGENCY_CRITICAL


def clamp_confidence(value: object, default: float = 0.5) -> float:
    """把外部输入（模型返回值）收敛到 [0,1]；非法值回退到保守默认值。"""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, number))


def level_rank(level: str | None) -> int:
    """等级排序：数字越小越严重；未知等级排最后。"""
    return _LEVEL_RANK.get(str(level or "").upper(), len(_LEVEL_RANK))


def more_severe(first: str | None, second: str | None) -> str:
    """返回两个等级中更严重的一个（只升不降）。"""
    if level_rank(first) <= level_rank(second):
        return str(first or URGENCY_DEFAULT).upper()
    return str(second or URGENCY_DEFAULT).upper()


def _clean_text(text: str) -> str:
    """剔除会造成误报的固定短语。"""
    cleaned = text or ""
    for phrase in _EXCEPTION_PHRASES:
        cleaned = cleaned.replace(phrase, "")
    return cleaned


def _is_negated(text: str, index: int) -> bool:
    """关键词紧邻否定词时视为否定表达。

    兼容「没有冒烟」「没有出现冒烟」「未发现冒烟」，但不会把「无尘室冒烟」
    「不仅冒烟」误判成否定。
    """
    prefix = text[max(0, index - 8) : index]
    return bool(_NEGATION_PATTERN.search(prefix))


def _match_keywords(text: str, keywords: tuple[str, ...]) -> tuple[str, ...]:
    """返回未被否定的命中关键词（去重、保持规则顺序）。"""
    matched: list[str] = []
    for keyword in keywords:
        start = 0
        while True:
            index = text.find(keyword, start)
            if index < 0:
                break
            if not _is_negated(text, index):
                matched.append(keyword)
                break
            start = index + len(keyword)
    return tuple(dict.fromkeys(matched))


def assess_urgency(description: str) -> UrgencyAssessment:
    """用确定性规则判定紧急等级，并给出置信度与命中关键词。"""
    text = _clean_text((description or "").strip())

    p0_matches = _match_keywords(text, _P0_KEYWORDS)
    if p0_matches:
        safety_hits = _match_keywords(text, _P0_SAFETY_KEYWORDS)
        security_hits = _match_keywords(text, _P0_SECURITY_KEYWORDS)
        outage_hits = _match_keywords(text, _P0_OUTAGE_KEYWORDS)
        if safety_hits:
            base, hint = 0.99, "硬件"
            reason = f"命中最高危机关键词「{'、'.join(safety_hits)}」，按 P0 灾难级直接转人工"
        elif security_hits:
            base, hint = 0.95, "安全事件"
            reason = f"命中数据安全关键词「{'、'.join(security_hits)}」，按 P0 灾难级直接转人工"
        else:
            base, hint = 0.92, "服务器"
            reason = f"命中业务中断关键词「{'、'.join(outage_hits)}」，按 P0 灾难级直接转人工"
        confidence = min(0.99, base + 0.01 * (len(p0_matches) - 1))
        return UrgencyAssessment(
            level=URGENCY_CRITICAL,
            confidence=round(confidence, 2),
            matched_keywords=p0_matches,
            reason=reason,
            is_safety_critical=bool(safety_hits),
            fault_type_hint=hint,
        )

    p1_matches = _match_keywords(text, _P1_KEYWORDS)
    if p1_matches:
        confidence = min(0.95, 0.80 + 0.02 * (len(p1_matches) - 1))
        return UrgencyAssessment(
            level="P1",
            confidence=round(confidence, 2),
            matched_keywords=p1_matches,
            reason=f"命中严重级关键词「{'、'.join(p1_matches)}」，按 P1 处理",
            is_safety_critical=False,
            fault_type_hint=None,
        )

    p3_matches = _match_keywords(text, _P3_KEYWORDS)
    if p3_matches:
        confidence = min(0.90, 0.75 + 0.02 * (len(p3_matches) - 1))
        return UrgencyAssessment(
            level="P3",
            confidence=round(confidence, 2),
            matched_keywords=p3_matches,
            reason=f"命中咨询类关键词「{'、'.join(p3_matches)}」，按 P3 处理",
            is_safety_critical=False,
            fault_type_hint=None,
        )

    return UrgencyAssessment(
        level=URGENCY_DEFAULT,
        confidence=0.40,
        matched_keywords=(),
        reason="未命中任何紧急或咨询关键词，按一般故障处理，规则置信度较低",
        is_safety_critical=False,
        fault_type_hint=None,
    )


def format_urgency_guide() -> str:
    """生成喂给模型的等级描述（与规则共用同一份定义，避免口径漂移）。"""
    return "\n".join(
        f"- {level}（{definition['name']}）：{definition['description']}"
        for level, definition in URGENCY_DEFINITIONS.items()
    )


def build_safety_notification(ticket_id: int, assessment: UrgencyAssessment, assignee: dict | None) -> str:
    """入口快速通道的确定性通知消息（不调用大模型）。"""
    who = "待定"
    if assignee:
        who = f"{assignee.get('name', '值班工程师')}（{assignee.get('contact', '')}）"
    keywords = "、".join(assessment.matched_keywords) or "未记录"
    if assessment.is_safety_critical:
        action = (
            "请立即确保人身安全，远离冒烟、明火或漏电设备；在确保安全的前提下切断电源，"
            "禁止用水扑救电气火灾，并联系现场安全负责人或消防。"
        )
    else:
        action = "请立即联系值班工程师，按企业应急预案处置。"
    return (
        f"【紧急工单】#{ticket_id} 触发最高危机规则\n"
        f"命中关键词：{keywords}\n"
        f"规则置信度：{assessment.confidence:.2f}\n"
        f"{action}\n"
        f"已分派工程师：{who}"
    )
