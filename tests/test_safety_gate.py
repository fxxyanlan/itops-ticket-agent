"""入口安全闸门与置信度规则的测试。

覆盖：
- P0 安全关键词直接触发快速通道（不调用大模型）；
- 否定句、「冒烟测试」等误报场景不触发；
- 每个等级都有描述语，且等级只升不降；
- 解析/分类置信度低于阈值时转人工，置信度缺失也转人工。
"""

from app.agent.routing import decide, plan_next
from app.services.safety_gate import assess_urgency, format_urgency_guide, more_severe


def test_fire_keyword_triggers_p0_fast_path():
    assessment = assess_urgency("电脑冒火了，机箱有明火和焦味，请马上处理")
    assert assessment.level == "P0"
    assert assessment.fast_path is True
    assert assessment.is_safety_critical is True
    assert assessment.fault_type_hint == "硬件"
    assert "冒火" in assessment.matched_keywords
    assert assessment.confidence >= 0.99


def test_outage_keyword_triggers_p0_but_not_fire_notice():
    assessment = assess_urgency("全公司网络宕机了，业务中断")
    assert assessment.level == "P0"
    assert assessment.fast_path is True
    assert assessment.is_safety_critical is False
    assert assessment.fault_type_hint == "服务器"


def test_negated_smoke_does_not_trigger():
    assessment = assess_urgency("没有冒烟，也没有焦味，只是打印机脱机")
    assert assessment.level != "P0"
    assert assessment.fast_path is False
    assert "冒烟" not in assessment.matched_keywords


def test_smoke_test_term_does_not_trigger():
    assessment = assess_urgency("冒烟测试环境跑一下打印机的联调")
    assert assessment.level != "P0"
    assert assessment.fast_path is False


def test_room_name_with_negation_char_still_triggers():
    assessment = assess_urgency("无尘室的服务器冒烟了")
    assert assessment.level == "P0"
    assert assessment.fast_path is True


def test_urgency_levels_have_descriptions_and_confidence():
    guide = format_urgency_guide()
    for level in ("P0", "P1", "P2", "P3"):
        assert level in guide
    assert "冒火" in guide and "火灾" in guide

    assert assess_urgency("紧急，打印机坏了").level == "P1"
    assert assess_urgency("有空帮我看看打印机").level == "P3"
    assert assess_urgency("打印机脱机了").level == "P2"


def test_only_escalate_never_downgrade():
    assert more_severe("P2", "P1") == "P1"
    assert more_severe("P1", "P2") == "P1"
    assert more_severe("P3", "P2") == "P2"


def _base_state() -> dict:
    return {
        "urgency": "P2",
        "fault_type": "打印机",
        "auto_resolvable": True,
        "best_match_score": 0.90,
        "parse_confidence": 0.80,
        "classify_confidence": 0.80,
        "critic_verdict": "pass",
    }


def test_routing_allows_auto_when_confident():
    decision, _ = decide(_base_state())
    assert decision == "auto"


def test_routing_escalates_low_parse_confidence():
    state = {**_base_state(), "parse_confidence": 0.30}
    decision, reason = decide(state)
    assert decision == "human"
    assert "解析置信度" in reason


def test_routing_escalates_low_classify_confidence():
    state = {**_base_state(), "classify_confidence": 0.30}
    decision, reason = decide(state)
    assert decision == "human"
    assert "分类置信度" in reason


def test_routing_escalates_missing_confidence():
    state = _base_state()
    state.pop("parse_confidence")
    decision, reason = decide(state)
    assert decision == "human"
    assert "解析置信度" in reason


def test_low_parse_confidence_skips_retrieval():
    state = {
        "title": "电脑很慢",
        "fault_type": "其他",
        "parse_confidence": 0.30,
    }
    next_agent, reason = plan_next(state)
    assert next_agent == "resolver"
    assert "跳过检索" in reason


def test_fire_ticket_fast_path_api(client):
    r = client.post(
        "/api/tickets",
        json={
            "description": "电脑冒火，机箱有明火和焦味，请立即处理",
            "user_contact": "13800000000",
            "device_id": "PC-1024",
        },
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["fast_path"] is True
    assert data["ticket"]["status"] == "转人工"
    assert data["ticket"]["resolution_mode"] == "转人工"
    assert data["ticket"]["urgency"] == "P0"
    assert data["ticket"]["fault_type"] == "硬件"
    assert "冒火" in data["safety_alert"]
    assert "紧急工单" in data["ticket"]["notify_message"]
    assert data["state"]["rule_confidence"] >= 0.99

    logs = client.get(f"/api/tickets/{data['ticket']['id']}/logs").json()["data"]
    assert any(log["node"] == "安全闸门" for log in logs)


def test_negated_fire_does_not_take_fast_path(client):
    r = client.post(
        "/api/tickets",
        json={"description": "没有冒烟，也没有焦味，只是打印机脱机了"},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert not data.get("fast_path")
    assert data["ticket"]["urgency"] != "P0"


def test_async_fire_ticket_is_terminal_immediately(client):
    r = client.post(
        "/api/tickets/async",
        json={"description": "机箱冒烟并起火了，请马上处理"},
    )
    assert r.status_code == 202
    data = r.json()["data"]
    assert data["fast_path"] is True
    assert data["status"] == "转人工"

    status = client.get(data["poll_url"]).json()["data"]
    assert status["terminal"] is True
    assert status["ticket_status"] == "转人工"
