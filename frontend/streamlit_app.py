"""Streamlit 前端：提交工单 / 工单列表 / Agent 处理日志。

启动：
    py -m streamlit run frontend/streamlit_app.py --server.port 8501

注意：文件名不能叫 app.py，否则会和项目里的 `app` 包同名，
Streamlit 把脚本目录加入 sys.path 后会导致 `import app.config` 解析失败。
"""

import json
import sys
from pathlib import Path

import requests
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402

st.set_page_config(page_title="IT 运维智能工单 Agent", page_icon="🛠️", layout="wide")

STATUS_COLORS = {"新建": "🔵", "处理中": "🟡", "已解决": "🟢", "转人工": "🔴"}
MODE_ICONS = {"自动闭环": "✅", "转人工": "⚠️"}


# --------------------------------------------------------------------------- #
# 后端调用封装
# --------------------------------------------------------------------------- #
def api(method: str, path: str, **kwargs):
    """统一调用后端；失败时返回 (None, 错误信息)。"""
    url = f"{st.session_state.backend_url.rstrip('/')}{path}"
    try:
        resp = requests.request(method, url, timeout=300, **kwargs)
        resp.raise_for_status()
        return resp.json(), None
    except requests.exceptions.ConnectionError:
        return None, f"无法连接后端：{url}（请先启动 FastAPI 服务）"
    except requests.exceptions.HTTPError as exc:
        detail = ""
        try:
            detail = exc.response.json().get("detail", "")
        except Exception:  # noqa: BLE001
            detail = exc.response.text[:200]
        return None, f"后端返回错误 {exc.response.status_code}：{detail}"
    except Exception as exc:  # noqa: BLE001
        return None, f"请求异常：{exc}"


# --------------------------------------------------------------------------- #
# 侧边栏
# --------------------------------------------------------------------------- #
st.sidebar.title("🛠️ IT 运维工单 Agent")
st.session_state.setdefault("backend_url", settings.backend_url)
st.session_state.backend_url = st.sidebar.text_input("后端地址", value=st.session_state.backend_url)

health, health_error = api("GET", "/api/health")
if health:
    data = health.get("data") or {}
    st.sidebar.success("后端已连接")
    st.sidebar.markdown(
        f"- LLM 模式：`{data.get('llm_mode')}`\n"
        f"- 模型：`{data.get('model')}`\n"
        f"- 数据库：`{data.get('database')}`\n"
        f"- 相似度阈值：`{data.get('match_threshold')}`"
    )
else:
    st.sidebar.error(health_error or "后端不可用")
    st.sidebar.caption("启动命令：`py -m uvicorn app.main:app --port 8000`")

st.sidebar.divider()
st.sidebar.caption("LangGraph 状态机 + DeepSeek 原生 Function Calling")

tab_submit, tab_list, tab_logs = st.tabs(["📝 提交工单", "📋 工单列表", "🧠 Agent 处理日志"])


# --------------------------------------------------------------------------- #
# Tab1 提交工单
# --------------------------------------------------------------------------- #
with tab_submit:
    st.subheader("提交 IT 故障工单")
    st.caption("用自然语言描述问题即可，Agent 会自动解析、检索历史方案并决定「自动闭环」或「转人工」。")

    with st.form("ticket_form"):
        description = st.text_area(
            "故障描述",
            height=120,
            placeholder="例如：我的打印机 PC-1024 打印一直提示脱机，重启打印服务也不行",
        )
        col1, col2 = st.columns(2)
        contact = col1.text_input("联系方式", placeholder="姓名 / 手机号或邮箱")
        device_id = col2.text_input("设备编号（可留空，模型会自动识别）", placeholder="PC-1024")
        submitted = st.form_submit_button("提交并交给 Agent 处理", type="primary")

    # 提交：结果写入 session_state，避免 Streamlit 重跑后结果消失
    if submitted:
        if not description.strip():
            st.warning("请先填写故障描述")
        else:
            with st.spinner("Agent 正在处理：解析 → 分类 → 工具调用 → 分支决策..."):
                payload = {"description": description.strip()}
                if contact.strip():
                    payload["user_contact"] = contact.strip()
                if device_id.strip():
                    payload["device_id"] = device_id.strip()
                result, error = api("POST", "/api/tickets", json=payload)
            st.session_state["submit_result"] = result
            st.session_state["submit_error"] = error
            st.session_state["submit_desc"] = description.strip()

    # 渲染最近一次提交结果（跨 rerun 持久化）
    last_result = st.session_state.get("submit_result")
    last_error = st.session_state.get("submit_error")
    if last_error:
        st.error(last_error)
    elif last_result:
        ticket = last_result["data"]["ticket"]
        state = last_result["data"]["state"]
        mode_icon = MODE_ICONS.get(ticket["resolution_mode"] or "", "")
        if st.session_state.get("submit_desc"):
            st.caption(f"最近提交：{st.session_state['submit_desc']}")
        st.success(f"{mode_icon} 处理完成，工单 #{ticket['id']} 当前状态：{ticket['status']}")

        info_cols = st.columns(4)
        info_cols[0].metric("故障类型", ticket["fault_type"])
        info_cols[1].metric("紧急等级", ticket["urgency"])
        info_cols[2].metric("处理模式", ticket["resolution_mode"] or "-")
        info_cols[3].metric("最高匹配度", f"{state.get('best_match_score', 0):.2f}")

        st.markdown(f"**决策原因**：{ticket['decision_reason']}")
        if ticket["assignee"]:
            st.markdown(f"**分派工程师**：{ticket['assignee']}")

        if ticket["resolution"]:
            st.markdown("#### 自动生成的解决步骤")
            st.markdown(ticket["resolution"])
        if ticket["notify_message"]:
            st.markdown("#### 转人工通知消息")
            st.code(ticket["notify_message"], language="text")

        with st.expander("查看状态快照（state）"):
            st.json(state)


# --------------------------------------------------------------------------- #
# Tab2 工单列表
# --------------------------------------------------------------------------- #
with tab_list:
    st.subheader("工单列表")
    col_filter, col_refresh = st.columns([3, 1])
    status_filter = col_filter.selectbox("按状态筛选", ["全部", "新建", "处理中", "已解决", "转人工"])
    col_refresh.write("")
    if col_refresh.button("🔄 刷新"):
        st.rerun()

    query = "" if status_filter == "全部" else f"?status={status_filter}"
    listing, error = api("GET", f"/api/tickets{query}")
    if error:
        st.error(error)
    else:
        rows = listing.get("data") or []
        if not rows:
            st.info("暂无工单，去「提交工单」页创建一个吧")
        else:
            table = [
                {
                    "ID": r["id"],
                    "标题": r["title"],
                    "故障类型": r["fault_type"],
                    "等级": r["urgency"],
                    "状态": f"{STATUS_COLORS.get(r['status'], '')} {r['status']}",
                    "处理模式": r["resolution_mode"] or "-",
                    "工程师": r["assignee"] or "-",
                    "创建时间": r["created_at"][:19],
                }
                for r in rows
            ]
            st.dataframe(table, use_container_width=True, hide_index=True)

            st.divider()
            options = {f"#{r['id']} {r['title']}": r["id"] for r in rows}
            selected = st.selectbox("选择工单查看详情", list(options.keys()))
            detail, detail_error = api("GET", f"/api/tickets/{options[selected]}")
            if detail_error:
                st.error(detail_error)
            else:
                item = detail["data"]
                st.markdown(f"**状态**：{STATUS_COLORS.get(item['status'], '')} {item['status']}")
                st.markdown(f"**原始描述**：{item['description']}")
                st.markdown(f"**决策原因**：{item['decision_reason'] or '-'}")
                if item["resolution"]:
                    st.markdown("#### 解决步骤")
                    st.markdown(item["resolution"])
                if item["notify_message"]:
                    st.markdown("#### 通知消息")
                    st.code(item["notify_message"], language="text")


# --------------------------------------------------------------------------- #
# Tab3 Agent 处理日志
# --------------------------------------------------------------------------- #
with tab_logs:
    st.subheader("Agent 处理日志")
    all_tickets, error = api("GET", "/api/tickets?limit=200")
    if error:
        st.error(error)
    else:
        rows = all_tickets.get("data") or []
        if not rows:
            st.info("暂无工单，先去提交一个工单吧")
        else:
            options = {f"#{r['id']} {r['title']}（{r['status']}）": r["id"] for r in rows}
            selected = st.selectbox("选择工单", list(options.keys()), key="log_ticket")
            logs, log_error = api("GET", f"/api/tickets/{options[selected]}/logs")
            if log_error:
                st.error(log_error)
            else:
                entries = logs.get("data") or []
                st.caption(f"共 {len(entries)} 条日志（含每个节点与每次工具调用）")
                for entry in entries:
                    badge = {"INFO": "🟢", "WARN": "🟠", "ERROR": "🔴"}.get(entry["level"], "⚪")
                    st.markdown(f"{badge} `{entry['created_at'][11:19]}` **{entry['node']}** — {entry['message']}")
                    if entry.get("detail"):
                        with st.expander("查看细节（JSON）"):
                            try:
                                st.json(json.loads(entry["detail"]))
                            except json.JSONDecodeError:
                                st.code(entry["detail"], language="text")