"""双模型任务路由的测试：分工表、切换顺序、单模型故障降级。"""

import pytest

from app.llm.router import TASK_MODEL, ModelRouter


class _FakeCompletions:
    def __init__(self, fail: bool):
        self.fail = fail

    def create(self, **kwargs):
        if self.fail:
            raise RuntimeError("provider down")
        message = type("M", (), {"content": "ok", "tool_calls": []})()
        choice = type("C", (), {"message": message})()
        return type("R", (), {"choices": [choice]})()


class _FakeClient:
    def __init__(self, fail: bool):
        self.chat = type("Chat", (), {"completions": _FakeCompletions(fail)})()


def _router(deepseek_fail: bool = False, glm_fail: bool = False, glm: bool = True) -> ModelRouter:
    """绕过真实初始化，直接注入两个假客户端。"""
    router = object.__new__(ModelRouter)
    router.mode = "real"
    router._clients = {"deepseek": _FakeClient(deepseek_fail)}
    router._models = {"deepseek": "deepseek-chat"}
    if glm:
        router._clients["glm"] = _FakeClient(glm_fail)
        router._models["glm"] = "glm-4-air"
    return router


def test_task_assignment_matches_model_strengths():
    """推理/结构化任务给 DeepSeek，自然语言生成任务给 GLM。"""
    for task in ("parse", "classify", "tools", "review"):
        assert TASK_MODEL[task] == "deepseek"
    for task in ("resolution", "notification"):
        assert TASK_MODEL[task] == "glm"


def test_route_order_uses_other_model_as_fallback():
    router = _router()
    assert router.order_for("parse") == ["deepseek", "glm"]
    assert router.order_for("resolution") == ["glm", "deepseek"]


def test_fallback_when_primary_provider_fails():
    """GLM 故障时，生成任务切到 DeepSeek 继续完成。"""
    router = _router(glm_fail=True)
    result = router.chat("resolution", [{"role": "user", "content": "写步骤"}])
    assert result.provider == "deepseek"
    assert result.fallback is True


def test_all_providers_fail_raises_for_upper_layer_degrade():
    """两个模型都不可用时抛错，由上层降级到规则实现。"""
    router = _router(deepseek_fail=True, glm_fail=True)
    with pytest.raises(RuntimeError):
        router.chat("parse", [{"role": "user", "content": "解析"}])


def test_missing_glm_key_skips_to_deepseek():
    """未配置 GLM 时，生成类任务仍能由 DeepSeek 完成。"""
    router = _router(glm=False)
    result = router.chat("notification", [{"role": "user", "content": "写通知"}])
    assert result.provider == "deepseek"
    assert router.available("glm") is False
