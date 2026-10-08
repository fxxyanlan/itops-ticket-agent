"""API 边界与异常测试：入参校验、分页上限、非法枚举、空值与超长值。

统一约定：参数校验失败返回 422，且响应体内可定位到具体字段。
"""

from __future__ import annotations

from app.config import settings

# --------------------------------------------------------------------------- #
# description 边界
# --------------------------------------------------------------------------- #
def test_empty_description_rejected(client):
    r = client.post("/api/tickets", json={"description": ""})
    assert r.status_code == 422
    assert r.json()["code"] == 422


def test_single_char_description_rejected(client):
    r = client.post("/api/tickets", json={"description": "坏"})
    assert r.status_code == 422


def test_whitespace_only_description_rejected(client):
    """全空白应先去空白再判长度，不能绕过 min_length。"""
    r = client.post("/api/tickets", json={"description": "   \n\t  "})
    assert r.status_code == 422


def test_description_missing_rejected(client):
    r = client.post("/api/tickets", json={})
    assert r.status_code == 422


def test_description_max_length_boundary(client):
    """2000 字符为上限：恰好等于放行，超出 1 字符拒绝。"""
    ok = client.post("/api/tickets", json={"description": "打印机脱机" * 400})  # 5×400=2000 字
    assert ok.status_code == 200

    too_long = client.post("/api/tickets", json={"description": "打印机脱机" * 400 + "啊"})
    assert too_long.status_code == 422


# --------------------------------------------------------------------------- #
# 可选字段边界
# --------------------------------------------------------------------------- #
def test_user_contact_too_long_rejected(client):
    r = client.post("/api/tickets", json={"description": "打印机脱机问题", "user_contact": "a" * 129})
    assert r.status_code == 422


def test_device_id_too_long_rejected(client):
    r = client.post("/api/tickets", json={"description": "打印机脱机问题", "device_id": "d" * 65})
    assert r.status_code == 422


def test_blank_optional_fields_normalized_to_none(client):
    """空串 / 空白串应归一为 None，而非落库为空字符串。

    描述刻意不含设备编号等可被解析 Agent 自动抽取的 token，避免二次写入干扰断言。
    """
    r = client.post(
        "/api/tickets",
        json={"description": "VPN 密码忘记需要重置，请协助处理", "user_contact": "   ", "device_id": ""},
    )
    assert r.status_code == 200
    ticket = r.json()["data"]["ticket"]
    assert ticket["user_contact"] is None
    assert ticket["device_id"] is None


# --------------------------------------------------------------------------- #
# 查询参数边界
# --------------------------------------------------------------------------- #
def test_limit_zero_rejected(client):
    assert client.get("/api/tickets", params={"limit": 0}).status_code == 422


def test_limit_over_max_rejected(client):
    assert client.get("/api/tickets", params={"limit": settings.max_page_size + 1}).status_code == 422


def test_limit_at_max_accepted(client):
    assert client.get("/api/tickets", params={"limit": settings.max_page_size}).status_code == 200


def test_negative_offset_rejected(client):
    assert client.get("/api/tickets", params={"offset": -1}).status_code == 422


def test_invalid_status_enum_rejected(client):
    """状态过滤为固定枚举，非枚举值应被拒绝（避免 SQL 全表误命中）。"""
    assert client.get("/api/tickets", params={"status": "不存在的状态"}).status_code == 422


def test_logs_limit_over_max_rejected(client):
    assert client.get("/api/tickets/1/logs", params={"limit": 2001}).status_code == 422


def test_devices_limit_over_max_rejected(client):
    assert client.get("/api/devices", params={"limit": settings.max_page_size + 1}).status_code == 422


# --------------------------------------------------------------------------- #
# 非法路径参数
# --------------------------------------------------------------------------- #
def test_non_integer_ticket_id_rejected(client):
    assert client.get("/api/tickets/abc").status_code == 422


def test_logs_of_unknown_ticket_404(client):
    r = client.get("/api/tickets/999999/logs")
    assert r.status_code == 404
    assert r.json()["code"] == 404


def test_status_of_unknown_ticket_404(client):
    r = client.get("/api/tickets/999999/status")
    assert r.status_code == 404
    assert r.json()["code"] == 404


# --------------------------------------------------------------------------- #
# 错误信封不回显内部实现细节
# --------------------------------------------------------------------------- #
def test_validation_error_locates_field(client):
    body = client.post("/api/tickets", json={"description": ""}).json()
    assert body["code"] == 422
    # 校验器应能定位到 description 字段，并附带结构化错误列表
    assert "description" in body["detail"]
    assert body["errors"]