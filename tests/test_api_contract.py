"""API 契约测试：统一响应信封、字段完整性、分页、异步状态流转。

覆盖接口：健康检查、运行指标、工单提交（同步/异步）、工单列表、工单详情、
任务状态、Agent 日志、设备列表、404 语义。
"""

from __future__ import annotations

FAULT_TYPES = {
    "打印机", "账号权限", "网络", "硬件", "软件",
    "服务器", "邮箱", "数据库", "安全事件", "其他",
}
URGENCIES = {"P0", "P1", "P2", "P3"}
MODES = {"自动闭环", "转人工"}

SAMPLE = {
    "description": "打印机 PC-1024 打印一直提示脱机，重启打印服务也不行",
    "user_contact": "contract@test.com",
    "device_id": "PC-1024",
}


def envelope(body: dict) -> dict:
    """断言并返回统一响应信封。"""
    assert {"code", "message", "data"} <= set(body), f"响应缺少信封字段：{list(body)}"
    return body


# --------------------------------------------------------------------------- #
# 基础 / 元信息
# --------------------------------------------------------------------------- #
def test_health_contract(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = envelope(r.json())
    assert body["code"] == 0
    data = body["data"]
    assert data["status"] == "ok"
    assert data["database"] == "ok"
    assert data["llm_mode"] == "mock"
    assert data["model"]
    assert isinstance(data["match_threshold"], (int, float))
    # 计时中间件写入了耗时响应头
    assert float(r.headers["x-response-time-ms"]) >= 0


def test_metrics_contract(client):
    client.get("/api/health")
    r = client.get("/api/metrics")
    assert r.status_code == 200
    data = envelope(r.json())["data"]
    assert {"runtime", "cache", "tasks"} <= set(data)
    runtime = data["runtime"]
    assert runtime["total_requests"] >= 1
    assert "overall_error_rate" in runtime
    assert isinstance(runtime["paths"], list)
    assert {"hits", "misses"} <= set(data["cache"])


# --------------------------------------------------------------------------- #
# 工单写入
# --------------------------------------------------------------------------- #
def test_create_ticket_contract(client):
    r = client.post("/api/tickets", json=SAMPLE)
    assert r.status_code == 200
    body = envelope(r.json())
    data = body["data"]
    assert {"ticket", "state"} <= set(data)

    ticket = data["ticket"]
    for field in ("id", "title", "description", "fault_type", "urgency",
                  "status", "resolution_mode", "decision_reason",
                  "created_at", "updated_at"):
        assert field in ticket, f"工单字段缺失：{field}"

    assert isinstance(ticket["id"], int) and ticket["id"] > 0
    assert ticket["fault_type"] in FAULT_TYPES
    assert ticket["urgency"] in URGENCIES
    assert ticket["resolution_mode"] in MODES
    assert ticket["decision_reason"]

    # 状态与处理模式互斥一致
    if ticket["resolution_mode"] == "自动闭环":
        assert ticket["status"] == "已解决"
        assert ticket["resolution"]
    else:
        assert ticket["status"] == "转人工"


def test_create_ticket_async_and_poll(client):
    r = client.post("/api/tickets/async", json=SAMPLE)
    assert r.status_code == 202
    data = envelope(r.json())["data"]
    assert data["status"] == "新建"
    ticket_id = data["ticket_id"]
    assert data["poll_url"] == f"/api/tickets/{ticket_id}/status"

    # TestClient 会在响应返回前执行完 BackgroundTasks，故此处应已到达终态
    s = client.get(data["poll_url"])
    assert s.status_code == 200
    status = envelope(s.json())["data"]
    assert status["ticket_id"] == ticket_id
    assert status["state"] in {"done", "failed"}
    assert status["terminal"] is True
    assert status["state"] != "failed"
    assert status["ticket_status"] in {"已解决", "转人工"}
    assert status["resolution_mode"] in MODES
    assert status["elapsed_ms"] is None or status["elapsed_ms"] >= 0


# --------------------------------------------------------------------------- #
# 工单读取
# --------------------------------------------------------------------------- #
def test_list_tickets_contract(client):
    client.post("/api/tickets", json=SAMPLE)
    r = client.get("/api/tickets", params={"limit": 10, "offset": 0})
    assert r.status_code == 200
    data = envelope(r.json())["data"]
    assert isinstance(data, list) and data
    first = data[0]
    assert {"id", "title", "status", "fault_type"} <= set(first)
    # 按 id 倒序
    ids = [item["id"] for item in data]
    assert ids == sorted(ids, reverse=True)


def test_list_tickets_status_filter(client):
    r = client.get("/api/tickets", params={"status": "已解决", "limit": 50})
    assert r.status_code == 200
    for item in envelope(r.json())["data"]:
        assert item["status"] == "已解决"


def test_get_ticket_detail_contract(client):
    created = client.post("/api/tickets", json=SAMPLE).json()["data"]["ticket"]
    r = client.get(f"/api/tickets/{created['id']}")
    assert r.status_code == 200
    detail = envelope(r.json())["data"]
    assert detail["id"] == created["id"]
    assert detail["description"] == SAMPLE["description"]


def test_ticket_logs_contract(client):
    created = client.post("/api/tickets", json=SAMPLE).json()["data"]["ticket"]
    r = client.get(f"/api/tickets/{created['id']}/logs")
    assert r.status_code == 200
    logs = envelope(r.json())["data"]
    assert isinstance(logs, list) and logs
    for field in ("id", "ticket_id", "node", "level", "message", "created_at"):
        assert field in logs[0], f"日志字段缺失：{field}"
    assert all(log["ticket_id"] == created["id"] for log in logs)


def test_logs_pagination(client):
    created = client.post("/api/tickets", json=SAMPLE).json()["data"]["ticket"]
    tid = created["id"]
    page1 = client.get(f"/api/tickets/{tid}/logs", params={"limit": 5, "offset": 0}).json()["data"]
    page2 = client.get(f"/api/tickets/{tid}/logs", params={"limit": 5, "offset": 5}).json()["data"]
    assert len(page1) <= 5
    assert [x["id"] for x in page1] != [x["id"] for x in page2]


def test_devices_contract(client):
    r = client.get("/api/devices", params={"limit": 5})
    assert r.status_code == 200
    devices = envelope(r.json())["data"]
    assert isinstance(devices, list) and devices
    assert {"device_id", "device_type"} <= set(devices[0])


# --------------------------------------------------------------------------- #
# 404 语义：同时携带 code / message / detail（detail 兼容旧前端）
# --------------------------------------------------------------------------- #
def test_not_found_envelope_has_detail(client):
    r = client.get("/api/tickets/999999")
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == 404
    assert body["message"]
    assert body["detail"]  # 向后兼容字段
    assert body["data"] is None


def test_rerun_unknown_ticket_404(client):
    r = client.post("/api/tickets/999999/rerun")
    assert r.status_code == 404
    assert r.json()["code"] == 404