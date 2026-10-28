"""灰区交叉验证：用第二个模型复核「可能有误」的工单结论。

设计原则：
- 只在灰区、阈值边缘或抽样时触发，绝大多数工单不额外调用模型；
- 第二个模型独立判断，不告诉它主模型结论，避免先入为主；
- 任一模型判「必须人工」即转人工（只严不松）；
- 复核失败不阻塞主流程，但灰区里的「自动闭环」会降级为转人工。
"""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass, field

from app.agent.state import TicketState
from app.config import settings
from app.constants import URGENCY_CRITICAL
from app.llm.schemas import extract_json

logger = logging.getLogger("itops.llm")

PROMPT = """你是运维工单复核员。请独立判断：仅凭给出的这条历史方案，能否安全地自动闭环这张工单？

判定标准：
- 历史方案与当前故障现象一致、步骤明确可执行，且不涉及安全事故、硬件更换、数据风险 → true
- 方案与现象不符、信息不足、需要现场处理或存在数据/安全风险 → false
- 不确定时一律回答 false

只输出 JSON：{{"auto_resolvable": true/false, "confidence": 0~1, "reason": "30字以内", "risk_notes": ["风险点"]}}

工单描述：{description}
故障类型：{fault_type}｜紧急等级：{urgency}
历史方案匹配度：{score:.2f}
历史方案：{solution}"""


@dataclass(frozen=True)
class CrossCheckResult:
    """一次交叉验证的结果。"""

    status: str  # executed | skipped | unavailable
    trigger: str = ""
    decision: str | None = None
    confidence: float | None = None
    reason: str = ""
    model: str = ""
    elapsed_ms: int = 0
    notes: list[str] = field(default_factory=list)


def need_cross_check(state: TicketState) -> tuple[bool, str]:
    """判断是否需要复核，返回 (是否触发, 触发原因)。"""
    if not settings.cross_check_enabled:
        return False, ""
    if not settings.glm_api_key:
        return False, "GLM 未配置 Key"
    if state.get("fast_path") or state.get("urgency") == URGENCY_CRITICAL:
        return False, "P0 已由规则兜底"

    confidence = state.get("parse_confidence")
    if confidence is None or float(confidence) < settings.cross_check_min_confidence:
        return False, "解析置信度已低于阈值，直接转人工"

    decision, _ = _primary_decision(state)
    if decision == "auto":
        score = float(state.get("best_match_score") or 0.0)
        if settings.match_threshold <= score <= settings.cross_check_gray_score_max:
            return True, "边缘匹配"
        if float(confidence) <= settings.cross_check_max_confidence:
            return True, "置信度灰区"
        if random.random() < settings.cross_check_sample_rate:
            return True, "抽样复核"
        return False, "高置信高匹配，无需复核"
    return False, "主模型已判定转人工"


def _primary_decision(state: TicketState) -> tuple[str, str]:
    """复用路由规则，得到主模型结论（与处置岗判定保持一致）。"""
    from app.agent.routing import decide

    return decide(state)


def _build_client():
    """构造复核客户端（GLM）。"""
    from openai import OpenAI

    return OpenAI(
        api_key=settings.glm_api_key,
        base_url=settings.glm_base_url,
        timeout=settings.glm_timeout,
        max_retries=0,  # 复核失败直接降级，不重试，避免拖长尾延迟
    )


def run_cross_check(state: TicketState, trigger: str) -> CrossCheckResult:
    """调用第二个模型复核；任何异常都返回 unavailable，不抛给主流程。"""
    model = settings.glm_model
    matches = state.get("matched_solutions") or []
    solution = ""
    if matches:
        top = matches[0]
        solution = (
            f"《{top.get('title', '')}》{str(top.get('resolution') or top.get('solution') or '')[:600]}"
        )
    prompt = PROMPT.format(
        description=(state.get("raw_description") or "")[:300],
        fault_type=state.get("fault_type") or "其他",
        urgency=state.get("urgency") or "P2",
        score=float(state.get("best_match_score") or 0.0),
        solution=solution or "未检索到可复用的历史方案",
    )

    started = time.perf_counter()
    try:
        client = _build_client()
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=settings.glm_max_tokens,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or ""
        data = extract_json(content) or {}
        elapsed = int((time.perf_counter() - started) * 1000)
        if not content.strip():
            # 思考型模型可能把额度全用在思考上，正文为空
            logger.warning("交叉验证返回正文为空（可能是思考 token 占满输出上限）：%s", trigger)
            return CrossCheckResult(
                status="unavailable",
                trigger=trigger,
                reason="复核模型只返回思考内容，正文为空",
                model=model,
                elapsed_ms=elapsed,
            )
        auto = data.get("auto_resolvable")
        decision = None if auto is None else ("auto" if bool(auto) else "human")
        notes = [str(n) for n in (data.get("risk_notes") or [])][:3]
        result = CrossCheckResult(
            status="executed",
            trigger=trigger,
            decision=decision,
            confidence=(
                None
                if data.get("confidence") is None
                else round(min(1.0, max(0.0, float(data["confidence"]))), 2)
            ),
            reason=str(data.get("reason") or ""),
            model=model,
            elapsed_ms=elapsed,
            notes=notes,
        )
        logger.info(
            "交叉验证完成：%s｜结论=%s｜置信度=%s｜耗时=%dms",
            trigger,
            decision,
            result.confidence,
            elapsed,
        )
        return result
    except Exception as exc:  # noqa: BLE001 复核失败不能影响主流程
        elapsed = int((time.perf_counter() - started) * 1000)
        logger.warning("交叉验证不可用（%s）：%s", trigger, exc)
        return CrossCheckResult(
            status="unavailable",
            trigger=trigger,
            reason=f"复核失败：{type(exc).__name__}",
            model=model,
            elapsed_ms=elapsed,
        )


def merge(primary: str, result: CrossCheckResult) -> tuple[str, str, bool | None]:
    """合并两个模型的结论，返回 (最终 decision, 合并说明, 是否一致)。

    规则：任一模型判 human 即 human；复核无结论时保持主模型结论。
    """
    if result.status != "executed" or result.decision is None:
        note = "复核不可用，按主模型结论继续"
        return primary, note, None
    agreement = result.decision == primary
    if not agreement:
        return "human", f"主模型与复核模型结论分歧（复核={result.decision}），按从严原则转人工", False
    return primary, "两个模型结论一致", True


def state_patch(result: CrossCheckResult, decision: str, note: str, agreement: bool | None) -> dict:
    """把复核结果整理成可直接写回状态/日志的字段。"""
    return {
        "cross_check_status": result.status,
        "cross_check_trigger": result.trigger,
        "cross_check_decision": decision,
        "cross_check_confidence": result.confidence,
        "cross_check_reason": (result.reason or note)[:120],
        "cross_check_model": result.model,
        "cross_check_elapsed_ms": result.elapsed_ms,
        "cross_check_agreement": agreement,
        "cross_check_notes": result.notes,
    }
