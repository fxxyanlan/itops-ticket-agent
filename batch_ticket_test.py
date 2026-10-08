r"""个人技能评估测试：IT 运维智能工单 Agent —— 批量用例验证与指标统计。

用途
    以「个人技能评估」为目的，对工单 Agent 的编排链路做功能、边界与常见场景验证，
    产出可复现、可核验的结果与指标（JSON / CSV / 控制台 Markdown 表格）。

测试范围（26 个用例）
    基础功能 20 项 + 边界条件 6 项（空描述、超短描述、超长描述、枚举外类型、无匹配、已知回归）

判定口径（客观、可核验，不依赖主观打分）
    - 契约断言：HTTP 状态、响应结构、字段枚举合法性
    - 业务一致性断言：状态与处理模式互斥一致、产出物齐备、日志可追溯
    - 规则一致性断言：P0 / 高危类型 / 相似度低于阈值 → 必须转人工
    - 已知回归断言：对有历史匹配的用例断言「自动闭环」（基于既有验证结论）

运行方式
    1) 重置数据库：.\.venv\Scripts\python.exe scripts\init_db.py --reset
    2) 启动后端（建议离线模式以保证可复现）：
         $env:LLM_MODE="mock"; .\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
    3) 执行：.\.venv\Scripts\python.exe batch_ticket_test.py
       （可用环境变量 BASE_URL 覆盖后端地址，默认 http://127.0.0.1:8000）

产物
    skill_assessment_result.json / skill_assessment_result.csv
"""

from __future__ import annotations

import csv
import json
import os
import time
from datetime import datetime
from statistics import mean

import requests

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
TICKETS_API = f"{BASE_URL}/api/tickets"
HEALTH_API = f"{BASE_URL}/api/health"
TIMEOUT = int(os.environ.get("TEST_TIMEOUT", "300"))

FAULT_TYPES = {"打印机", "账号权限", "网络", "硬件", "软件", "服务器", "邮箱", "数据库", "安全事件", "其他"}
URGENCIES = {"P0", "P1", "P2", "P3"}
HIGH_RISK_TYPES = {"服务器", "数据库", "网络核心", "安全事件"}
MATCH_THRESHOLD = 0.42
MODE_AUTO, MODE_HUMAN = "自动闭环", "转人工"
STATUS_RESOLVED, STATUS_HUMAN = "已解决", "转人工"

# --------------------------------------------------------------------------- #
# 测试用例集：id / category / 输入 / 预期
#   expect_mode  : 断言的期望处理模式；None 表示不做模式断言（仅记录实测值 + 契约断言）
#   expect_http  : 断言的 HTTP 状态码（用于非法输入用例）
# --------------------------------------------------------------------------- #
CASES: list[dict] = [
    # ---- 常见应用场景（基础功能） ----
    {"id": 1, "category": "常见场景", "description": "Office软件字体缺失，无法正常显示文档",
     "user_contact": "test1@test.com", "device_id": "dev_003"},
    {"id": 2, "category": "常见场景", "description": "电脑系统时间错误，需要同步服务器时间",
     "user_contact": "test2@test.com", "device_id": "dev_004"},
    {"id": 3, "category": "常见场景", "description": "打印机卡纸，无法打印文件，不影响核心业务",
     "user_contact": "test3@test.com", "device_id": "dev_002"},
    {"id": 4, "category": "高危P0", "description": "服务器宕机，业务全部中断，紧急故障",
     "user_contact": "test4@test.com", "device_id": "dev_005", "expect_mode": MODE_HUMAN},
    {"id": 5, "category": "常见场景", "description": "财务部台式机，网线更换后无法访问内网，重新插拔网线",
     "user_contact": "test5@test.com", "device_id": "dev_001"},
    {"id": 6, "category": "常见场景", "description": "电脑屏幕亮度无法调节，显卡驱动简易重置即可修复",
     "user_contact": "test6@test.com", "device_id": "dev_006"},
    {"id": 7, "category": "常见场景", "description": "浏览器缓存过多，网页打开很慢，清理缓存就能恢复",
     "user_contact": "test7@test.com", "device_id": "dev_007"},
    {"id": 8, "category": "常见场景", "description": "电脑无法连接公司WiFi，忘记重新登录域账号",
     "user_contact": "test8@test.com", "device_id": "dev_008"},
    {"id": 9, "category": "常见场景", "description": "鼠标光标卡顿，USB接口重新插拔即可",
     "user_contact": "test9@test.com", "device_id": "dev_009"},
    {"id": 10, "category": "常见场景", "description": "Outlook收件箱爆满，归档旧邮件释放空间",
     "user_contact": "test10@test.com", "device_id": "dev_010"},
    {"id": 11, "category": "常见场景", "description": "电脑无法访问共享文件夹，权限需要刷新",
     "user_contact": "test11@test.com", "device_id": "dev_011"},
    {"id": 12, "category": "常见场景", "description": "软件弹窗提示证书过期，临时导入证书",
     "user_contact": "test12@test.com", "device_id": "dev_012"},
    {"id": 13, "category": "常见场景", "description": "电脑风扇异响，大概率积灰，需要现场拆机清理",
     "user_contact": "test13@test.com", "device_id": "dev_013"},
    {"id": 14, "category": "高危", "description": "数据库连接间歇性报错，偶发不稳定，需要人工排查日志",
     "user_contact": "test14@test.com", "device_id": "dev_014", "expect_mode": MODE_HUMAN},
    {"id": 15, "category": "高危", "description": "机房服务器报警，磁盘RAID出现降级风险",
     "user_contact": "test15@test.com", "device_id": "dev_015", "expect_mode": MODE_HUMAN},
    {"id": 16, "category": "常见场景", "description": "笔记本进水，开机黑屏，硬件损坏",
     "user_contact": "test16@test.com", "device_id": "dev_016"},
    {"id": 17, "category": "常见场景", "description": "VPN偶尔断线，网络环境不稳定，尝试切换节点重试",
     "user_contact": "test17@test.com", "device_id": "dev_017"},
    {"id": 18, "category": "常见场景", "description": "企业网盘上传文件提示大小超限",
     "user_contact": "test18@test.com", "device_id": "dev_018"},
    {"id": 19, "category": "常见场景", "description": "投影仪画面偏色，线材松动，重新插拔HDMI线",
     "user_contact": "test19@test.com", "device_id": "dev_019"},
    {"id": 20, "category": "常见场景", "description": "业务系统页面按钮点击无响应，前端页面缓存问题",
     "user_contact": "test20@test.com", "device_id": "dev_020"},
    # ---- 边界条件 ----
    {"id": 21, "category": "边界-非法输入", "description": "", "expect_http": 422},
    {"id": 22, "category": "边界-非法输入", "description": "坏", "expect_http": 422},
    {"id": 23, "category": "边界-超长文本",
     "description": "打印机 PC-1024 打印一直提示脱机，重启打印服务也不行。" * 20,
     "user_contact": "test23@test.com", "device_id": "PC-1024"},
    {"id": 24, "category": "基础-已知回归", "description": "我的打印机 PC-1024 打印一直提示脱机，重启打印服务也不行",
     "user_contact": "张小明", "device_id": "PC-1024", "expect_mode": MODE_AUTO},
    {"id": 25, "category": "边界-无匹配", "description": "新采购的示波器 XYZ-9 驱动装不上，之前从未处理过",
     "user_contact": "test25@test.com", "expect_mode": MODE_HUMAN},
    {"id": 26, "category": "边界-枚举外类型", "description": "我的显示器屏幕不亮，可能是线材问题",
     "user_contact": "test26@test.com", "device_id": "MON-01"},
]


# --------------------------------------------------------------------------- #
# 单个用例执行
# --------------------------------------------------------------------------- #
def check(checks: list, name: str, ok: bool, detail: str = "") -> None:
    checks.append({"name": name, "ok": bool(ok), "detail": detail})


def fetch_logs(ticket_id: int) -> list:
    try:
        r = requests.get(f"{TICKETS_API}/{ticket_id}/logs", timeout=TIMEOUT)
        return (r.json().get("data") or []) if r.status_code == 200 else []
    except Exception:  # noqa: BLE001
        return []


def run_case(case: dict) -> dict:
    payload = {"description": case["description"]}
    if case.get("user_contact"):
        payload["user_contact"] = case["user_contact"]
    if case.get("device_id"):
        payload["device_id"] = case["device_id"]

    record: dict = {
        "id": case["id"], "category": case["category"],
        "description": case["description"], "payload": payload,
        "expect_mode": case.get("expect_mode"), "expect_http": case.get("expect_http", 200),
        "checks": [], "duration_sec": None,
        "http": None, "status": "", "mode": "", "fault_type": "", "urgency": "",
        "assignee": "", "best_match_score": None, "iterations": None,
        "log_count": 0, "tool_log_count": 0, "reason": "", "error": "",
    }
    checks = record["checks"]

    start = time.perf_counter()
    try:
        resp = requests.post(TICKETS_API, json=payload, timeout=TIMEOUT)
        record["duration_sec"] = round(time.perf_counter() - start, 4)
        record["http"] = resp.status_code
    except Exception as exc:  # noqa: BLE001
        record["duration_sec"] = round(time.perf_counter() - start, 4)
        record["error"] = f"请求异常：{exc}"
        check(checks, "HTTP 响应", False, record["error"])
        return record

    # --- 非法输入用例：仅断言状态码 ---
    if record["expect_http"] != 200:
        check(checks, "HTTP 状态码", resp.status_code == record["expect_http"],
              f"实际 {resp.status_code}，期望 {record['expect_http']}")
        return record

    # --- 正常用例：契约 + 业务一致性 ---
    check(checks, "HTTP 状态码", resp.status_code == 200, f"实际 {resp.status_code}")
    try:
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        check(checks, "响应为合法 JSON", False, str(exc))
        return record

    check(checks, "响应 code=0", body.get("code") == 0, f"code={body.get('code')}")
    data = body.get("data") or {}
    ticket = data.get("ticket") or {}
    state = data.get("state") or {}
    record.update({
        "status": ticket.get("status", ""),
        "mode": ticket.get("resolution_mode") or "",
        "fault_type": ticket.get("fault_type", ""),
        "urgency": ticket.get("urgency", ""),
        "assignee": ticket.get("assignee") or "",
        "reason": ticket.get("decision_reason") or "",
        "best_match_score": state.get("best_match_score"),
        "iterations": state.get("iterations"),
    })

    check(checks, "工单已落库(id)", bool(ticket.get("id")), f"id={ticket.get('id')}")
    check(checks, "故障类型在枚举内", record["fault_type"] in FAULT_TYPES, record["fault_type"])
    check(checks, "紧急等级合法", record["urgency"] in URGENCIES, record["urgency"])
    check(checks, "处理模式合法", record["mode"] in {MODE_AUTO, MODE_HUMAN}, record["mode"])
    check(checks, "决策原因非空", bool(record["reason"]), record["reason"][:40])

    # 状态与处理模式互斥一致
    consistent = (record["mode"] == MODE_AUTO and record["status"] == STATUS_RESOLVED) or \
                 (record["mode"] == MODE_HUMAN and record["status"] == STATUS_HUMAN)
    check(checks, "状态与处理模式一致", consistent, f"{record['status']} / {record['mode']}")

    # 产出物齐备
    if record["mode"] == MODE_AUTO:
        check(checks, "自动闭环产出解决步骤", bool(ticket.get("resolution")), "resolution 非空")
        check(checks, "自动闭环不分派工程师", not record["assignee"], f"assignee={record['assignee'] or '空'}")
    else:
        check(checks, "转人工产出通知消息", bool(ticket.get("notify_message")), "notify_message 非空")
        check(checks, "转人工已分派工程师", bool(record["assignee"]), f"assignee={record['assignee']}")

    # 规则一致性：P0 / 高危 → 必须转人工
    if record["urgency"] == "P0" or record["fault_type"] in HIGH_RISK_TYPES:
        check(checks, "规则:高危/P0→转人工", record["mode"] == MODE_HUMAN,
              f"{record['urgency']}/{record['fault_type']} → {record['mode']}")

    # 规则一致性：相似度低于阈值 → 必须转人工
    score = record["best_match_score"]
    if isinstance(score, (int, float)) and score < MATCH_THRESHOLD:
        check(checks, "规则:低于阈值→转人工", record["mode"] == MODE_HUMAN, f"score={score:.4f}")

    # 日志可追溯
    logs = fetch_logs(ticket["id"])
    record["log_count"] = len(logs)
    record["tool_log_count"] = sum(1 for x in logs if str(x.get("node", "")).startswith("tool:"))
    check(checks, "日志条目充足(≥8)", record["log_count"] >= 8, f"{record['log_count']} 条")
    check(checks, "含工具调用日志", record["tool_log_count"] >= 1, f"{record['tool_log_count']} 条")

    # 已知回归断言
    if record["expect_mode"]:
        check(checks, f"预期模式={record['expect_mode']}", record["mode"] == record["expect_mode"],
              f"实际 {record['mode']}")

    return record


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main() -> int:
    print("=" * 78)
    print("个人技能评估测试 · IT 运维智能工单 Agent")
    print(f"开始时间：{datetime.now():%Y-%m-%d %H:%M:%S}    后端：{BASE_URL}")
    try:
        health = requests.get(HEALTH_API, timeout=10).json().get("data", {})
        print(f"环境自检：llm_mode={health.get('llm_mode')}  model={health.get('model')}  db={health.get('database')}")
    except Exception as exc:  # noqa: BLE001
        print(f"[致命] 无法连接后端 {HEALTH_API}：{exc}")
        print("请先启动后端：$env:LLM_MODE=\"mock\"; .\\.venv\\Scripts\\python.exe -m uvicorn app.main:app --port 8000")
        return 2
    print("=" * 78)

    records = []
    for case in CASES:
        rec = run_case(case)
        records.append(rec)
        total = len(rec["checks"])
        passed = sum(1 for c in rec["checks"] if c["ok"])
        flag = "PASS" if total and passed == total else "FAIL"
        print(f"[{flag}] #{rec['id']:>2} {rec['category']:<10} "
              f"HTTP={rec['http']} 模式={rec['mode'] or '-':<4} 类型={rec['fault_type'] or '-':<4} "
              f"等级={rec['urgency'] or '-':<3} 耗时={rec['duration_sec']}s 断言 {passed}/{total}")
        for c in rec["checks"]:
            if not c["ok"]:
                print(f"        └ 未通过：{c['name']}（{c['detail']}）")

    # ---- 指标汇总 ----
    executed = [r for r in records if r["expect_http"] == 200]
    invalid = [r for r in records if r["expect_http"] != 200]
    all_checks = [c for r in records for c in r["checks"]]
    passed_checks = [c for c in all_checks if c["ok"]]
    case_pass = [r for r in records if r["checks"] and all(c["ok"] for c in r["checks"])]
    auto_closed = [r for r in executed if r["mode"] == MODE_AUTO]
    durations = sorted(r["duration_sec"] for r in executed if r["duration_sec"] is not None)

    def pct(vals, p):
        if not vals:
            return 0.0
        return vals[min(len(vals) - 1, int(len(vals) * p))]

    summary = {
        "测试项目": "IT 运维智能工单自动处理 Agent · 个人技能评估测试",
        "测试日期": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "后端地址": BASE_URL,
        "用例总数": len(records),
        "有效执行用例": len(executed),
        "非法输入用例": len(invalid),
        "用例通过数": len(case_pass),
        "用例通过率": round(len(case_pass) / len(records) * 100, 2),
        "断言总数": len(all_checks),
        "断言通过数": len(passed_checks),
        "断言通过率": round(len(passed_checks) / len(all_checks) * 100, 2) if all_checks else 0.0,
        "自动闭环数": len(auto_closed),
        "自动闭环率": round(len(auto_closed) / len(executed) * 100, 2) if executed else 0.0,
        "耗时均值秒": round(mean(durations), 4) if durations else 0.0,
        "耗时P50秒": round(pct(durations, 0.50), 4),
        "耗时P90秒": round(pct(durations, 0.90), 4),
        "耗时P95秒": round(pct(durations, 0.95), 4),
        "耗时最大秒": round(max(durations), 4) if durations else 0.0,
        "耗时总计秒": round(sum(durations), 4),
        "平均迭代轮数": round(mean([r["iterations"] for r in executed if isinstance(r["iterations"], int)]), 2)
        if any(isinstance(r["iterations"], int) for r in executed) else None,
    }

    print("\n" + "=" * 78)
    print("测试汇总")
    print("=" * 78)
    for k, v in summary.items():
        print(f"{k}：{v}")

    with open("skill_assessment_result.json", "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "records": records}, f, ensure_ascii=False, indent=2)

    with open("skill_assessment_result.csv", "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["用例", "类别", "输入描述", "预期模式", "实际模式", "状态",
                         "故障类型", "等级", "分派", "匹配度", "迭代", "日志数", "工具日志",
                         "耗时(秒)", "断言通过", "断言总数", "结论"])
        for r in records:
            p = sum(1 for c in r["checks"] if c["ok"])
            writer.writerow([
                r["id"], r["category"], r["description"][:60], r["expect_mode"] or "-",
                r["mode"] or "-", r["status"] or "-", r["fault_type"] or "-", r["urgency"] or "-",
                r["assignee"] or "-", r["best_match_score"], r["iterations"], r["log_count"],
                r["tool_log_count"], r["duration_sec"], p, len(r["checks"]),
                "PASS" if r["checks"] and p == len(r["checks"]) else "FAIL",
            ])

    print("\n产物：skill_assessment_result.json / skill_assessment_result.csv")
    failed = [r for r in records if not (r["checks"] and all(c["ok"] for c in r["checks"]))]
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())