"""双模型任务路由：按任务类型把请求分发给 DeepSeek 或 GLM。

分工依据（能力属性优先，而非模型名气）：
- DeepSeek：结构化抽取、等级判定、工具规划、方案审核 —— 偏逻辑推理与确定性输出；
- GLM：解决步骤、转人工通知 —— 偏自然语言生成与可读性表达。

容错顺序：任务指定模型 → 另一模型 → 抛错（由上层降级到规则实现）。
每次调用只允许一次切换，避免在故障时反复往返。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.config import settings
from app.llm.schemas import ToolCall, extract_json
from app.logging_config import get_logger

logger = get_logger("itops.llm")

# 任务 → 首发模型。调整分工只需改这一张表。
TASK_MODEL: dict[str, str] = {
    "parse": "deepseek",  # 结构化抽取 + 等级/可自动解决性判断
    "classify": "deepseek",  # 独立分类判定（合并解析未产出时兜底）
    "tools": "deepseek",  # Function Calling 工具规划
    "review": "deepseek",  # 方案审核，需要严格逻辑核对
    "resolution": "glm",  # 面向用户的解决步骤生成
    "notification": "glm",  # 面向工程师的通知文案
}

PROVIDERS = ("deepseek", "glm")


@dataclass
class RouteResult:
    """一次路由调用的结果，附带实际服务的模型。"""

    provider: str
    model: str
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    elapsed_ms: int = 0
    fallback: bool = False


class ModelRouter:
    """按任务分发到两个模型；任一模型不可用时自动切换。"""

    def __init__(self) -> None:
        self.mode = settings.effective_llm_mode
        self._clients: dict[str, object] = {}
        self._models: dict[str, str] = {}
        if self.mode == "real":
            self._init_provider("deepseek", settings.deepseek_api_key,
                                settings.deepseek_base_url, settings.deepseek_model,
                                settings.llm_timeout)
            self._init_provider("glm", settings.glm_api_key,
                                settings.glm_base_url, settings.glm_model,
                                settings.glm_timeout)

    def _init_provider(self, name: str, key: str, base_url: str, model: str, timeout: int) -> None:
        if not key:
            logger.info("模型 %s 未配置 Key，相关任务将走备用模型", name)
            return
        try:
            from openai import OpenAI

            self._clients[name] = OpenAI(
                api_key=key, base_url=base_url, timeout=timeout, max_retries=0
            )
            self._models[name] = model
        except Exception as exc:  # noqa: BLE001
            logger.error("初始化模型 %s 失败：%s", name, exc)

    def available(self, provider: str) -> bool:
        return provider in self._clients

    def model_name(self, provider: str) -> str:
        return self._models.get(provider, "")

    def order_for(self, task: str) -> list[str]:
        """返回该任务的尝试顺序：指定模型在前，另一模型兜底。"""
        primary = TASK_MODEL.get(task, "deepseek")
        return [primary] + [p for p in PROVIDERS if p != primary]

    def chat(
        self,
        task: str,
        messages: list[dict],
        *,
        json_mode: bool = False,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
    ) -> RouteResult:
        """按任务路由调用；两个模型都失败时抛错，由上层降级。"""
        errors: list[str] = []
        for index, provider in enumerate(self.order_for(task)):
            if not self.available(provider):
                errors.append(f"{provider}: 未配置")
                continue
            kwargs: dict = {
                "model": self._models[provider],
                "messages": messages,
                "temperature": temperature,
            }
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"

            started = time.perf_counter()
            try:
                response = self._clients[provider].chat.completions.create(**kwargs)  # type: ignore[attr-defined]
                message = response.choices[0].message
                elapsed = int((time.perf_counter() - started) * 1000)
                calls = [
                    ToolCall(
                        id=call.id,
                        name=call.function.name,
                        arguments=extract_json(call.function.arguments) or {},
                    )
                    for call in (message.tool_calls or [])
                ]
                result = RouteResult(
                    provider=provider,
                    model=self._models[provider],
                    content=message.content or "",
                    tool_calls=calls,
                    elapsed_ms=elapsed,
                    fallback=index > 0,
                )
                logger.info(
                    "模型路由：任务=%s｜模型=%s/%s｜耗时=%dms%s",
                    task, provider, self._models[provider], elapsed,
                    "（已切换备用模型）" if index > 0 else "",
                )
                return result
            except Exception as exc:  # noqa: BLE001 单模型失败切另一模型
                elapsed = int((time.perf_counter() - started) * 1000)
                errors.append(f"{provider}: {type(exc).__name__}({elapsed}ms)")
                logger.warning("模型 %s 调用失败（任务=%s，%dms）：%s", provider, task, elapsed, exc)

        logger.error("任务 %s 两个模型均不可用：%s", task, "；".join(errors))
        raise RuntimeError(f"所有模型调用失败：{'；'.join(errors)}")

    def describe(self) -> dict:
        """供健康检查展示的路由表与可用状态。"""
        return {
            "routes": dict(TASK_MODEL),
            "providers": {
                p: {"model": self._models.get(p, ""), "available": self.available(p)}
                for p in PROVIDERS
            },
        }
