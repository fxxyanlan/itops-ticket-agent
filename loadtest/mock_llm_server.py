"""本地 Mock LLM：OpenAI 兼容的 /v1/chat/completions，固定返回假数据（零 token 消耗）。

为什么需要它
    压测要跑真实的接口与 Agent 编排，但**不能真的调用 DeepSeek**（又慢又烧 token）。
    两种免 token 方案：
      A. 内置免 token：`.env` 里设 `LLM_MODE=mock`（应用直接走规则实现，零外呼，最省事）；
      B. 本 Mock 接口：让应用保持 `real` 代码路径，只把「模型服务」换成本地假接口
         （仍然走 openai SDK → 解析 tool_calls → 执行工具 → 回填，更贴近真实行为）。

启动（二选一）
    1) 本机直跑： python loadtest/mock_llm_server.py --port 8001
    2) Docker：  docker compose --profile mock-llm up mock-llm

接入（改 .env，然后重启后端）
    DEEPSEEK_BASE_URL=http://127.0.0.1:8001/v1
    DEEPSEEK_API_KEY=sk-mock
    LLM_MODE=real

固定返回规则（按请求特征分支，保证可复现）
    - 带 tools 且消息里还没有 role=tool → 返回固定 tool_calls（search_similar_tickets）
    - 带 response_format=json_object   → 返回一段固定 JSON（一份同时满足 解析/分类/审核 三种 schema）
    - 其余（生成方案 / 转人工通知）      → 返回固定 Markdown 文本
"""

from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 一份 JSON 同时覆盖三种调用所需字段：
#   解析(extract_ticket) 要 title/fault_type/urgency/device_id/summary
#   分类(classify)       要 auto_resolvable
#   审核(review_resolution) 要 verdict
FIXED_JSON: dict = {
    "title": "打印机脱机无法打印",
    "fault_type": "打印机",
    "urgency": "P2",
    "device_id": "PC-1024",
    "user_contact": "压测用户",
    "summary": "打印机提示脱机 重启打印服务无效",
    "auto_resolvable": True,
    "reason": "常见软性问题 有标准处置流程",
    "verdict": "pass",
    "feedback": "方案完整可执行",
}

FIXED_MARKDOWN = """### 设备信息
- 设备编号：PC-1024｜型号：HP LaserJet Pro｜位置：3 楼办公区

### 匹配到的历史工单
- #12《打印机脱机无法打印》匹配度 0.86

### 建议处理步骤
1. 打开「服务」，重启 Print Spooler；
2. 清除打印队列后重新添加打印机；
3. 确认网络连通性与驱动版本。

> 风险提示：若重启服务后仍脱机，请联系运维现场检查硬件。"""

# 固定的一次工具调用（参数名与 app/tools/ticket_search.py 的 SPEC 一致）
FIXED_TOOL = {
    "name": "search_similar_tickets",
    "arguments": {"fault_type": "打印机", "keywords": "打印机脱机无法打印", "top_k": 3},
}


def build_chat_response(req: dict) -> dict:
    """根据请求特征，构造一个确定性的 chat.completion 响应。"""
    messages = req.get("messages") or []
    has_tools = bool(req.get("tools"))
    already_called = any(m.get("role") == "tool" for m in messages)
    json_mode = (req.get("response_format") or {}).get("type") == "json_object"

    tool_calls = None
    content = None
    finish_reason = "stop"

    if has_tools and not already_called:
        # 第一轮：给出一次工具调用意图（模型只生成意图，执行在应用侧）
        tool_calls = [
            {
                "id": "call_mock_1",
                "type": "function",
                "function": {
                    "name": FIXED_TOOL["name"],
                    "arguments": json.dumps(FIXED_TOOL["arguments"], ensure_ascii=False),
                },
            }
        ]
        finish_reason = "tool_calls"
    elif json_mode:
        content = json.dumps(FIXED_JSON, ensure_ascii=False)
    else:
        content = FIXED_MARKDOWN

    message: dict = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls

    return {
        "id": "chatcmpl-mock",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": req.get("model") or "mock-model",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # noqa: D102  静音逐请求日志
        pass

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/").endswith("/health"):
            self._send({"status": "ok", "service": "mock-llm"})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(raw.decode("utf-8"))
        except Exception:  # noqa: BLE001  坏请求也不应该让压测中断
            req = {}
        self._send(build_chat_response(req))


def main() -> None:
    parser = argparse.ArgumentParser(description="本地 Mock LLM（OpenAI 兼容，固定假数据）")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址（容器内需 0.0.0.0）")
    parser.add_argument("--port", type=int, default=8001, help="监听端口")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)  # 多线程，扛得住并发压测
    print(f"Mock LLM 已启动：http://{args.host}:{args.port}/v1/chat/completions", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Mock LLM 已停止", flush=True)


if __name__ == "__main__":
    main()
