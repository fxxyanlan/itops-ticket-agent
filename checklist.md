# Checklist：IT 运维智能工单自动处理 Agent

> 用法：每项完成后打勾。`[A]` = 自动化可验证项，`[M]` = 需人工确认项。
> 关联：[spec.md](spec.md) ｜ [tasks.md](tasks.md)

---

## 验收结果（2026-10-04，LLM_MODE=mock 离线模式）

| 验证项 | 命令 / 方式 | 结果 |
|---|---|---|
| 依赖安装 | `pip install -r requirements.txt` | ✅ 通过（需联网；本机沙箱限制网络时用 `-i https://pypi.tuna.tsinghua.edu.cn/simple`） |
| 建表 + 种子数据 | `py scripts/init_db.py` | ✅ 历史工单 10 / 设备 10 / 工程师 6 |
| 工具层 | `py scripts/test_tools.py` | ✅ 3 个工具返回结构正确；打印机匹配度 0.64，示波器 0.17，非法状态被拦截 |
| 状态机逐节点 | `py scripts/test_graph.py` | ✅ 解析→分类→3 轮 ReAct 工具循环→自动闭环→收尾 |
| 端到端验收 | `py scripts/demo_test.py` | ✅ **5/5 通过**（自动闭环与转人工两类分支均覆盖） |
| REST 接口 | `GET /api/health`、`POST /api/tickets`、`GET /api/tickets/{id}/logs` | ✅ 状态/日志/过滤均正确 |
| Streamlit 前端 | 浏览器实测三个 Tab | ✅ 提交→结果卡片、工单列表、Agent 日志全部正常 |
| PowerShell 脚本 | `Parser::ParseFile` + 实际命令 | ✅ run.ps1 / run_backend.ps1 / run_frontend.ps1 语法与命令均通过 |

---

## 验收结果（2026-10-04，LLM_MODE=real 真实 DeepSeek 模式）

> 前置：`.env` 已配置 `DEEPSEEK_API_KEY`（长度 35），`LLM_MODE=auto` → 生效模式 `real`。
> 后端启动日志：`服务启动完成｜LLM 模式=real｜模型=deepseek-chat｜相似度阈值=0.42`。

| 验证项 | 命令 / 方式 | 结果 |
|---|---|---|
| 读取 .env 的 Key | `effective_llm_mode` / `/api/health` | ✅ `llm_mode=real`，`model=deepseek-chat`，`database=ok` |
| 真实 LLM 直连探针 | `probe_real.py`（11 项） | ✅ 全部 HTTP 200；解析/分类/原生 `tool_calls`/方案生成均真实返回 |
| 工具层（真实） | `py scripts/test_tools.py` | ✅ 3 工具正常；打印机 0.6383、示波器 0.1702、非法状态被拦截 |
| 状态机逐节点（真实） | `py scripts/test_graph.py` | ✅ 解析→分类→3 轮 ReAct 循环→自动闭环（匹配度 0.62）→收尾 |
| 端到端（进程内） | `py scripts/demo_test.py` | ✅ **5/5 通过** |
| 端到端（HTTP） | `py scripts/demo_test.py --http` | ✅ **5/5 通过**（样例1/4 自动闭环，样例2/3/5 转人工） |
| REST 全接口（真实） | `probe_api.py`（14 项） | ✅ 14/14 通过；health/POST/详情/日志/重跑/列表/过滤/设备/404 全部符合预期 |
| 日志完整性（真实） | `GET /api/tickets/{id}/logs` | ✅ 14 条日志覆盖 10 类节点；转人工写入 1 条 `WARN`；含每次工具调用入参出参 |
| Streamlit 前端（真实） | 浏览器实测三 Tab + 真实提交 | ✅ 侧边栏 `LLM 模式: real`；提交键盘工单 #17 → 转人工/孙磊；无报错 |
| 后端异常检查 | uvicorn 全量日志 | ✅ 全程仅 `HTTP/1.1 200 OK`，无 500、无 traceback |

**已知非缺陷现象（如实记录）**：
- 同一工单「重跑」结果可能不同（LLM 非确定性）：工单 #11 首次 = 转人工（匹配度 0.3667），
  重跑 = 已解决/自动闭环（匹配度 0.50）。原因是真实模型两次生成的 `keywords` 不同，导致 `difflib` 相似度变化。
  关键分支规则（P0 / 高危类型 / 阈值）由代码确定性裁决，不受此影响。
- 真实模型与 mock 的分类口径略有差异（如样例3 mock 判「软件」、真实判「硬件」），但最终分支结果一致。

**未验证项（如实说明）**：
- `run.ps1` 的一键串联未整跑（其三个子步骤已分别实测通过；脚本仅负责顺序调用与后台拉起）。

**过程中修复的三个问题**：
1. 前端入口文件原名 `frontend/app.py`，与项目 `app` 包同名导致 `import app.config` 失败 → 已改名 `frontend/streamlit_app.py`。
2. 提交结果未持久化，Streamlit 重跑后结果卡片消失 → 已用 `st.session_state` 保存并每轮渲染。
3. PowerShell 5.1 解析含中文的 .ps1 需 UTF-8 BOM → 三个脚本已加 BOM。

---

## 0. 前置确认（编码前）

- [ ] [M] 已确认是否需要 `LLM_MODE=mock` 离线兜底模式
- [ ] [M] 已确认工单处理为**同步**（提交即返回结果）
- [ ] [M] 已确认前端为 Streamlit 单页三 Tab
- [ ] [M] 已确认规划文档放项目根目录
- [ ] [M] 已确认整体计划，同意开始编码

---

## 1. 脚手架与配置（P0）

- [ ] 目录结构齐全：`app/{db,schemas,llm,tools,agent,services,api}`、`frontend/`、`scripts/`、`data/`、`logs/`
- [ ] [A] `pip install -r requirements.txt` 无报错
- [ ] [A] `.env.example` 字段完整（KEY/BASE_URL/MODEL/DATABASE_URL/MATCH_THRESHOLD/LLM_MODE/LOG_LEVEL）
- [ ] [A] `app/config.py` 可在无 API Key 时正常加载（mock 模式）
- [ ] [A] 日志同时输出到控制台与 `logs/app.log`（滚动 5MB×3）
- [ ] [A] API Key 不出现在任何日志中

---

## 2. 数据层（P1）

- [ ] [A] `Ticket` 表含 14 个字段（含 `resolution_mode`/`assignee`/`notify_message`）
- [ ] [A] `AgentLog` 表含 `node`/`level`/`message`/`detail`(JSON)
- [ ] [A] `DeviceAsset`、`Engineer` 表建成
- [ ] [A] 种子数据：历史已解决工单 ≥10 条、设备 ≥8 台、工程师 ≥5 名
- [ ] [A] `py scripts/init_db.py` 幂等，可重复执行
- [ ] [A] 历史工单覆盖：打印机 / 账号权限 / 网络 / 服务器 / 邮箱 等类型

---

## 3. 工具层（P2）

- [ ] [A] `TOOLS_SPEC` 含 3 个工具的 OpenAI Function Calling Schema
- [ ] 工具1 `search_similar_tickets`：按 `fault_type` + `keywords` 返回 top-k 与 `best_score`
- [ ] [A] 工具1 相似度用 `difflib.SequenceMatcher`，分数 ∈ [0,1]
- [ ] [A] 工具2 `get_device_asset`：命中返回资产；未命中返回 `found:false`
- [ ] [A] 工具3 `update_ticket_status`：状态枚举校验（新建/处理中/已解决/转人工）
- [ ] [A] 工具3 变更后 DB `updated_at` 更新，且写入 `agent_logs`
- [ ] [A] `py scripts/test_tools.py` 三工具直接调用全部通过
- [ ] [A] 工具异常被捕获并返回结构化错误，不中断图执行

---

## 4. LLM 层（P3）

- [ ] [A] `LLM_MODE=real` 下 DeepSeek 客户端可完成一次对话
- [ ] [A] `LLM_MODE=mock` 下无需 API Key 即完成抽取/分类/工具决策/方案生成
- [ ] [A] `extract_json()` 解析失败自动重试1次，仍失败时走规则兜底
- [ ] [A] `chat_with_tools()` 返回原生 `tool_calls`（含 `id`/`function.name`/`arguments`）
- [ ] [A] 两种模式对外接口签名一致，上层无需分支判断

---

## 5. LangGraph 状态机（P4）—— 核心

- [ ] [A] `TicketState` 含 `messages`(`Annotated[list[dict], operator.add]`，OpenAI 原生格式)、`iterations`、证据与决策字段
- [ ] [A] `parse_ticket` 抽取 title/fault_type/urgency/device_id/user_contact 并回写工单
- [ ] [A] `classify` 输出 `auto_resolvable` + `classify_reason`
- [ ] [A] `agent` 节点真实使用 LLM 原生 Function Calling 决策工具
- [ ] [A] `execute_tools` 逐个执行并按 `ToolMessage` 回灌，ReAct 循环成立
- [ ] [A] 循环有上限（iterations ≤ 6），不会死循环
- [ ] [A] `gather_evidence` 兜底：LLM 未调工具时仍保证有检索证据
- [ ] [A] **条件边1** `route_after_agent` 存在且生效
- [ ] [A] **条件边2** `route_after_evidence` 存在且生效
- [ ] [A] 路由规则：P0 → 转人工
- [ ] [A] 路由规则：高危类型（服务器/数据库/网络核心/安全事件）→ 转人工
- [ ] [A] 路由规则：`best_match_score < MATCH_THRESHOLD(0.42)` → 转人工
- [ ] [A] 路由规则：`auto_resolvable=False` → 转人工
- [ ] [A] 否则 → 自动闭环；`decision_reason` 记录命中原因
- [ ] [A] `auto_resolve`：生成 Markdown 解决步骤，状态→已解决，mode=自动闭环
- [ ] [A] `escalate_human`：按 fault_type 分派工程师（兜底按负载），状态→转人工，生成通知消息
- [ ] [A] `finalize` 汇总落库，图返回完整终态
- [ ] [A] `scripts/test_graph.py` 直跑图，5 条样例两类分支均覆盖
- [ ] [A] 未使用 CrewAI/Agno/AutoGen 等重型框架（依赖清单可证）

---

## 6. 服务与 API（P5）

- [ ] [A] `POST /api/tickets` 提交即跑完 Agent 并返回终态
- [ ] [A] `GET /api/tickets` 支持 `status` 过滤与 `limit`
- [ ] [A] `GET /api/tickets/{id}` 返回详情
- [ ] [A] `GET /api/tickets/{id}/logs` 按时间序返回处理日志
- [ ] [A] `POST /api/tickets/{id}/rerun` 可重新触发
- [ ] [A] `GET /api/health` 返回 LLM 模式与 DB 连通性
- [ ] [A] 统一响应体 `{code,message,data}`；异常不抛 500 裸栈
- [ ] [A] 服务重启后工单与日志数据仍在（SQLite 持久化）
- [ ] [A] CORS 允许前端 8501 访问

---

## 7. Streamlit 前端（P6）

- [ ] [A] 侧边栏可配置后端地址并显示健康状态 / LLM 模式
- [ ] [A] Tab1 提交工单：表单 → 结果卡片（状态/决策/方案或通知/决策原因）
- [ ] [A] Tab2 工单列表：表格 + 状态筛选 + 详情
- [ ] [A] Tab3 Agent 日志：按工单展示节点与工具调用的时间线（含 detail JSON）
- [ ] [A] 前端不直连 DB，全部经 FastAPI
- [ ] [A] 后端不可用时有友好错误提示（非白屏）

---

## 8. 测试与交付（P7）

- [ ] [A] 样例1 打印机脱机 → 已解决 / 自动闭环
- [ ] [A] 样例2 数据库宕机 P0 → 转人工 + 已分派 + 通知消息
- [ ] [A] 样例3 无历史匹配设备 → 转人工
- [ ] [A] 样例4 VPN 密码重置 → 已解决 / 自动闭环
- [ ] [A] 样例5 投影仪无信号 → 按分数走对应分支，验证条件边
- [ ] [A] `py scripts/demo_test.py` 输出与 spec §11 预期一致
- [ ] [A] `run.ps1` 一键拉起后端(8000)+前端(8501)
- [ ] [A] 分启动脚本 `run_backend.ps1` / `run_frontend.ps1` 可用
- [ ] [A] `README.md` 含目录结构 / 启动命令 / 测试样例 / FAQ
- [ ] [A] 关键逻辑含中文注释（节点、工具、路由、状态流转）
- [ ] [A] 仓库无调试残留、无未使用依赖

---

## 9. 最终验收（DoD 对齐 spec §12）

- [ ] [M] 一条命令完成环境准备与 DB 初始化，`run.ps1` 启动后前后端可访问
- [ ] [M] 5 条样例全部符合预期，自动闭环与转人工均真实覆盖
- [ ] [M] 真实模式下日志可见模型原生 `tool_calls` 与入参出参
- [ ] [M] LangGraph 图中至少 2 处条件边，分支可解释
- [ ] [M] 状态持久化 + 每步日志可查
- [ ] [M] 控制台与 `logs/app.log` 双通道日志含 Agent 思考与工具调用
- [ ] [M] 代码有注释，README 完整
- [ ] [M] 未使用重型多 Agent 框架