# Spec：IT 运维智能工单自动处理 Agent

> 版本：v1.0（待确认）
> 目标产物：一个可在 Trae 终端一键启动、端到端跑通「工单提交 → Agent 自动处理 → 状态流转 → 结果回写」的可运行 Demo。

---

## 1. 项目目标

员工以自然语言提交 IT 故障工单，Agent 自动完成**完整工单闭环**，核心价值在于：

1. **LangGraph 状态机**驱动的业务流程编排（不是线性脚本，也不是纯问答）。
2. **大模型原生 Function Calling** 的真实多工具调用（3 个业务工具）。
3. **工单状态真实流转**并持久化到 SQLite（新建 → 处理中 → 已解决 / 转人工）。
4. 能自动解决的自动闭环；不能自动解决 / 高危的自动转人工并分派工程师。

**明确不做**：纯知识库 RAG 问答；重型多 Agent 框架（CrewAI / Agno / AutoGen 等一律不用）。

---

## 2. 技术栈

| 层 | 选型 | 说明 |
|---|---|---|
| 语言 | Python 3.13 | 本机 `py` 启动器可用（3.13.15） |
| Web 框架 | FastAPI + Uvicorn | 提供 REST API |
| ORM | SQLAlchemy 2.x | SQLite 持久化 |
| 校验/序列化 | Pydantic v2 + pydantic-settings | 请求/响应与配置 |
| LLM | DeepSeek（`openai` SDK，OpenAI 兼容协议） | `deepseek-chat`，支持 `tools` + `response_format=json_object` |
| 编排 | LangGraph（StateGraph + 条件边） | 唯一编排框架，`langchain-core` 仅用于消息类型 |
| 前端 | Streamlit | 提交工单 / 工单列表 / Agent 日志 |
| 日志 | 标准 `logging`（控制台 + 滚动文件）+ DB 落库 | 双重可观测 |
| 数据库 | SQLite（`data/itops.db`） | 免安装 |

依赖清单（`requirements.txt`）：`fastapi`、`uvicorn[standard]`、`sqlalchemy`、`pydantic`、`pydantic-settings`、`python-dotenv`、`openai`、`langgraph`、`langchain-core`、`streamlit`、`requests`、`httpx`。

---

## 3. 系统架构

```
┌──────────────┐   HTTP    ┌───────────────────────────────────────────┐
│  Streamlit   │ ────────► │  FastAPI                                  │
│  前端 (8501) │           │  ├─ /api/tickets  (CRUD + 触发 Agent)      │
└──────────────┘           │  └─ 调用 LangGraph Graph.invoke(state)     │
                           └───────────────┬───────────────────────────┘
                                           │
                        ┌──────────────────▼──────────────────┐
                        │  LangGraph 状态机 (Agent 编排)      │
                        │  parse → classify → agent ⟲ tools   │
                        │            ↓ 条件边（分支路由）      │
                        │   auto_resolve  │  escalate_human   │
                        └──────────────────┬──────────────────┘
                                           │ 原生 Function Calling
                     ┌─────────────────────┼─────────────────────┐
                     ▼                     ▼                     ▼
             工具1 历史工单检索      工具2 设备资产查询      工具3 工单状态更新
                     └─────────────────────┼─────────────────────┘
                                           ▼
                                ┌──────────────────┐
                                │ SQLite (SQLAlchemy)│
                                │ tickets / logs /   │
                                │ assets / engineers │
                                └──────────────────┘
```

**处理时序（单次工单）**
1. 前端 POST `/api/tickets` → 工单落库（状态=新建）。
2. FastAPI 组装 `TicketState` → `graph.invoke()`。
3. `parse_ticket`：LLM 结构化抽取字段并回写工单。
4. `classify`：LLM 判定「可自动解决 / 必须人工处理」+ 原因。
5. `agent`：LLM 携带工具 Schema，自主决定调用哪些工具（原生 Function Calling）。
6. `execute_tools`：执行工具调用，结果回灌消息，循环直至无 tool_calls（≤6 轮保护）。
7. 条件边 `route_after_agent` 决策走自动闭环还是转人工。
8. 终态节点写回状态、解决方案、派单信息，生成通知文本。
9. 全流程每步落 `agent_logs`，前端可查。

---

## 4. 数据模型（SQLAlchemy / SQLite）

### 4.1 `tickets` 工单表
| 字段 | 类型 | 说明 |
|---|---|---|
| id | int PK | 工单号 |
| title | str | 标题（LLM 抽取） |
| description | text | 员工原始描述 |
| fault_type | str | 故障类型（网络/硬件/软件/账号权限/服务器/打印机/邮箱/其他） |
| urgency | str | 紧急等级 P0/P1/P2/P3（P0 最高） |
| device_id | str? | 设备编号（LLM 抽取，可空） |
| user_contact | str? | 用户联系方式 |
| status | str | 新建 / 处理中 / 已解决 / 转人工 |
| resolution_mode | str? | 自动闭环 / 转人工（终态标记） |
| resolution | text? | 解决方案 / 处理步骤（Markdown 文本） |
| assignee | str? | 分派工程师（转人工时） |
| notify_message | text? | 生成的通知消息 |
| created_at / updated_at | datetime | 时间戳 |

### 4.2 `agent_logs` Agent 处理日志表
| 字段 | 类型 | 说明 |
|---|---|---|
| id | int PK | |
| ticket_id | int FK | 关联工单 |
| node | str | 节点名（parse_ticket/classify/agent/tool:xxx/route/...） |
| level | str | INFO/WARN/ERROR |
| message | str | 人类可读摘要 |
| detail | text (JSON) | 结构化细节（tool 入参出参、LLM 决策） |
| created_at | datetime | |

### 4.3 `device_assets` 设备资产表
`device_id`(PK)、`hostname`、`device_type`、`os`、`ip`、`location`、`owner`、`purchase_date`、`warranty_until`、`health_status`。

### 4.4 `engineers` 运维工程师表
`id`、`name`、`specialty`（对应 fault_type）、`level`、`contact`、`active_tickets`（用于负载兜底分派）。

### 4.5 历史工单数据来源
历史解决方案**复用 `tickets` 表**中 `status='已解决'` 的记录（种子数据预置 8~10 条覆盖各故障类型），不额外建表。

---

## 5. LangGraph 状态机设计

### 5.1 State（`TicketState`，TypedDict）
```python
class TicketState(TypedDict):
    ticket_id: int
    raw_description: str
    user_contact: str | None
    # 解析结果
    title: str | None
    fault_type: str | None
    urgency: str | None          # P0/P1/P2/P3
    device_id: str | None
    # 分类结果
    auto_resolvable: bool | None
    classify_reason: str | None
    # 工具证据
    matched_solutions: list[dict]   # 工具1 结果 [{ticket_id,title,resolution,score}]
    best_match_score: float
    device_info: dict | None        # 工具2 结果
    status_updates: list[dict]      # 工具3 调用记录
    # 决策与产出
    decision: str | None            # auto | human
    decision_reason: str | None
    resolution: str | None
    assignee: str | None
    notify_message: str | None
    final_status: str | None
    # 循环控制
    messages: Annotated[list[dict], operator.add]   # OpenAI 原生消息格式
    iterations: int
    errors: list[str]
```

### 5.2 节点（Nodes）
| 节点 | 职责 | 关键动作 |
|---|---|---|
| `parse_ticket` | 工单解析 | LLM `json_object` 抽取 title/fault_type/urgency/device_id/user_contact；失败时规则兜底（关键字映射） |
| `classify` | 意图 & 故障分类 | LLM 输出 `auto_resolvable` + 原因；与高危规则取「或」 |
| `agent` | 决策中枢 | 绑定 3 工具 Schema 调用 DeepSeek，解析 `message.tool_calls` |
| `execute_tools` | 工具执行 | 逐个执行 tool_call，结果以 `ToolMessage` 回灌；写日志 |
| `gather_evidence` | 证据兜底 | 若 LLM 未调用「历史工单检索」，确定性补齐调用，保证路由判定有据 |
| `auto_resolve` | 自动闭环 | LLM 生成解决步骤（Markdown），状态→已解决，mode=自动闭环 |
| `escalate_human` | 转人工 | 按 fault_type 分派工程师，状态→转人工，生成通知消息 |
| `finalize` | 收尾 | 最终状态落库 + 汇总日志 |

### 5.3 边与条件边（分支路由是核心考点）
```
START → parse_ticket → classify → agent
agent ──(有 tool_calls)──► execute_tools ──► agent        # ReAct 循环，iterations≤6
agent ──(无 tool_calls)──► route_after_agent   [条件边]
route_after_agent:
    ├─ 缺检索证据 → gather_evidence → route_after_evidence [条件边]
    ├─ auto  → auto_resolve → finalize → END
    └─ human → escalate_human → finalize → END
route_after_evidence:
    ├─ auto  → auto_resolve → finalize → END
    └─ human → escalate_human → finalize → END
```

### 5.4 路由判定规则（代码内确定性实现，不交给 LLM 拍板）
判定为 **转人工（human）**，只要满足任一：
- `urgency == "P0"`；或
- `fault_type` ∈ `HIGH_RISK_TYPES`（服务器故障、数据库、网络核心、安全事件）；或
- `classify.auto_resolvable == False`；或
- `best_match_score < MATCH_THRESHOLD`（默认 **0.42**，可配置）。

否则 → **自动闭环（auto）**。`decision_reason` 记录命中原因。

匹配度算法：对 `status='已解决'` 的历史工单，用 `difflib.SequenceMatcher` 对（故障类型 + 描述关键字）做相似度，取 top-3，最高分即 `best_match_score`。

---

## 6. 工具定义（原生 Function Calling）

统一以 OpenAI Tools 格式声明，`registry` 集中注册。

### 工具 1：`search_similar_tickets`
```json
{
  "name": "search_similar_tickets",
  "description": "按故障类型与问题描述检索历史已解决工单，返回相似解决方案与匹配度分数",
  "parameters": {
    "type": "object",
    "properties": {
      "fault_type": {"type": "string", "description": "故障类型"},
      "keywords":   {"type": "string", "description": "问题描述关键词/原始描述"},
      "top_k":      {"type": "integer", "description": "返回条数，默认3"}
    },
    "required": ["fault_type", "keywords"]
  }
}
```
返回：`{"matches":[{"ticket_id":12,"title":"...","resolution":"...","score":0.81}], "best_score":0.81}`

### 工具 2：`get_device_asset`
```json
{
  "name": "get_device_asset",
  "description": "根据设备编号查询设备资产信息（型号/系统/IP/位置/负责人/保修状态）",
  "parameters": {
    "type": "object",
    "properties": {"device_id": {"type": "string", "description": "设备编号，如 PC-1024"}},
    "required": ["device_id"]
  }
}
```
返回：资产字典；不存在时 `{"found": false, "message": "..."}`。

### 工具 3：`update_ticket_status`
```json
{
  "name": "update_ticket_status",
  "description": "更新工单状态并追加处理备注（新建/处理中/已解决/转人工）",
  "parameters": {
    "type": "object",
    "properties": {
      "ticket_id": {"type": "integer"},
      "status":    {"type": "string", "enum": ["新建","处理中","已解决","转人工"]},
      "note":      {"type": "string", "description": "状态变更备注"}
    },
    "required": ["ticket_id", "status"]
  }
}
```
返回：`{"ok": true, "status": "处理中", "updated_at": "..."}`，同时落 `agent_logs`。

> LLM 在 `agent` 节点负责主动调用工具；`execute_tools` 节点统一执行并记录。终态由 `auto_resolve` / `escalate_human` 节点强制写库，保证状态一定流转到位。

---

## 7. FastAPI 接口设计

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/tickets` | 提交工单，同步跑完 Agent，返回工单详情 + 处理结果 |
| GET | `/api/tickets?status=&limit=` | 工单列表（支持状态过滤） |
| GET | `/api/tickets/{id}` | 工单详情 |
| GET | `/api/tickets/{id}/logs` | 该工单的 Agent 处理日志（按时间序） |
| POST | `/api/tickets/{id}/rerun` | 重新触发 Agent（便于演示/重试） |
| GET | `/api/devices` | 设备资产列表（前端辅助查看，可选） |
| GET | `/api/health` | 健康检查（含 LLM 模式、DB 连通性） |

统一响应体 `{code, message, data}`；异常经全局 handler 转为 `code!=0` 的 JSON。

---

## 8. Streamlit 前端设计

单页 + 三个 Tab（`frontend/streamlit_app.py`，命名不可用 `app.py`，避免与 `app` 包冲突）：

1. **提交工单**：文本框（自然语言描述）+ 联系方式 + 可选设备编号 → 提交 → 展示 Agent 处理结果卡片（状态、决策、解决方案/通知消息、决策原因）。
2. **工单列表**：表格展示全部工单（ID/标题/类型/等级/状态/处理模式/assignee/时间），支持状态筛选；点击/选择行查看详情。
3. **Agent 处理日志**：选择工单 → 时间线展示每个节点与工具调用的日志（节点名、级别、摘要、耗时/JSON 细节）。

侧边栏：后端地址、健康状态、LLM 模式（real/mock）。前端通过 `requests` 调 FastAPI，不直接访问 DB。

---

## 9. 日志与可观测

- **文件日志**：`logs/app.log`，`RotatingFileHandler`（5MB × 3），格式含时间/级别/模块/工单号。
- **控制台日志**：INFO 及以上直出，便于终端观察 Agent 思考过程。
- **DB 日志**：每个节点/工具调用写 `agent_logs`，`detail` 存 JSON（工具入参出参、LLM 原始决策摘要），供前端可视化。
- 记录内容：解析结果、分类结论、每轮 LLM 决策与 tool_calls、工具返回摘要、路由判定与原因、最终状态变更。
- 敏感信息：API Key 不落日志；用户联系方式在 detail 中仅保留必要字段。

---

## 10. 配置与一键启动

### 环境变量（`.env`，提供 `.env.example`）
```
DEEPSEEK_API_KEY=sk-xxxx
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
DATABASE_URL=sqlite:///./data/itops.db
MATCH_THRESHOLD=0.42
LLM_MODE=auto            # auto(有Key走真实/无Key降级mock) | real | mock
LOG_LEVEL=INFO
```

### 启动方式（Trae 内置终端）
```powershell
py -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env   # 填入 DEEPSEEK_API_KEY
py scripts\init_db.py         # 建表 + 种子数据
.\run.ps1                     # 一键启动后端(8000) + 前端(8501)
```
另提供分启动脚本 `run_backend.ps1` / `run_frontend.ps1`。

### 离线可用性（已确认：保留）
提供 `LLM_MODE=mock`：无需 API Key，用规则/模板模拟抽取、分类、工具决策与方案生成，保证全流程、状态流转、日志、前端均可离线演示与自测。`real` 模式走真实 DeepSeek。两种模式对外接口签名一致，上层无需分支判断。

---

## 11. 测试样例（验收用）

| # | 描述 | 期望结果 |
|---|---|---|
| 1 | 「我的打印机 PC-1024 打印一直提示脱机，重启打印服务也不行」 | 类型=打印机、非 P0 → 命中历史方案 → **已解决 / 自动闭环**，输出步骤 |
| 2 | 「核心数据库服务器宕机了，业务全部中断，十万火急！」 | 类型=服务器/数据库、P0/高危 → **转人工**，分派工程师 + 通知消息 |
| 3 | 「公司新采购的示波器驱动装不上，型号 XYZ-9」 | 历史无匹配（score<0.42）→ **转人工** |
| 4 | 「我忘记 VPN 密码了，需要重置」 | 类型=账号权限、有历史方案 → **已解决 / 自动闭环** |
| 5 | 「会议室投影仪没信号，设备编号 PJ-03」 | 有资产记录但方案匹配不足 → 视分数走对应分支，验证条件边 |

`scripts/demo_test.py`：批量提交上述样例并打印每单的决策、状态、耗时与日志摘要。

---

## 12. 验收标准（Definition of Done）

1. 一条命令完成环境准备 + 数据库初始化；`run.ps1` 启动后前后端均可访问。
2. 5 条测试样例全部跑通，结果符合上表预期；自动闭环与转人工两类分支均被真实覆盖。
3. 3 个工具在真实 `LLM_MODE=real` 下由 **模型原生 tool_calls** 触发（日志可见 `tool_calls` 与入参出参）；`mock` 模式下可离线复现。
4. LangGraph 图中**至少 2 处条件边**（`route_after_agent`、`route_after_evidence`），分支路由可解释。
5. 工单状态真实持久化到 SQLite，重启服务后数据仍在；`agent_logs` 每步可查。
6. 日志同时输出到控制台与 `logs/app.log`，含每步 Agent 思考与工具调用记录。
7. 代码含中文注释，`README.md` 含目录结构、启动命令、测试样例与常见问题。
8. 未使用 CrewAI/Agno 等重型框架。

---

## 13. 风险与假设

| 风险/假设 | 应对 |
|---|---|
| 无 DeepSeek API Key 或额度不足 | `LLM_MODE=mock` 离线兜底；错误时降级并记录 WARN |
| LLM 不按预期调用工具 | 路由前有 `gather_evidence` 确定性兜底；`iterations` 上限防死循环 |
| LLM 返回非合法 JSON | `response_format=json_object` + 解析失败重试1次 + 规则兜底解析 |
| 相似度阈值误判 | 阈值可配置；种子数据覆盖各类型，README 说明调优方式 |
| Windows PowerShell 脚本策略限制 | README 给出 `Set-ExecutionPolicy -Scope Process Bypass` 或直接用两条启动命令 |

---

## 14. 决策记录（已确认）

| # | 决策点 | 结论 |
|---|---|---|
| 1 | 离线兜底模式 | **保留** `LLM_MODE=mock`，无 Key 也可完整演示与自测 |
| 2 | 工单处理方式 | **同步**：提交即跑完 Agent 并返回终态（演示直观） |
| 3 | 前端形态 | Streamlit 单页三 Tab（提交工单 / 工单列表 / Agent 日志） |
| 4 | 目录结构 | FastAPI 后端（`app/` 分层）+ Streamlit 前端（`frontend/`）两服务，`run.ps1` 一键拉起 |
| 5 | 规划文档位置 | 项目根目录 `spec.md` / `tasks.md` / `checklist.md` |

> 上述计划已确认并完成编码（P0–P7）。端到端实测结果见 §15。

---

## 15. 验收结果（实测记录，2026-10-04）

已完成两种模式的端到端验收，**全部通过**。详表同步见 [checklist.md](checklist.md)。

### 15.1 真实模式（`LLM_MODE=auto` + `.env` 已配 Key → 生效 `real`）

> 后端启动日志：`服务启动完成｜LLM 模式=real｜模型=deepseek-chat｜相似度阈值=0.42`。
> 全程真实调用证据：日志中每步均为 `POST https://api.deepseek.com/chat/completions "HTTP/1.1 200 OK"`，无降级、无 500、无 traceback。

| 验证项 | 方式 | 结果 |
|---|---|---|
| 读取 .env 的 Key | `/api/health` | ✅ `llm_mode=real`、`model=deepseek-chat`、`database=ok`、`match_threshold=0.42` |
| 真实 LLM 直连探针（11 项） | 直调 LLM 层 | ✅ 解析/分类/原生 `tool_calls`/方案生成全部真实返回 |
| 工具层 | `py scripts/test_tools.py` | ✅ 打印机 0.6383、示波器 0.1702、非法状态被拦截 |
| 状态机逐节点 | `py scripts/test_graph.py` | ✅ 3 轮 ReAct 循环 → 自动闭环（匹配度 0.62） |
| 端到端（进程内 / HTTP） | `py scripts/demo_test.py [--http]` | ✅ **5/5 通过**（两种方式各 5/5） |
| REST 全接口（14 项） | health/POST/详情/日志/重跑/列表/过滤/设备/404 | ✅ 14/14 通过 |
| Streamlit 前端 | 浏览器实测三 Tab + 真实提交 | ✅ 侧边栏 `LLM 模式: real`；提交工单并正确裁决、无报错 |

端到端 5 样例实测（真实模式）：

| 样例 | 类型/等级 | 状态/模式 | 匹配度 | 工具轮数 |
|---|---|---|---|---|
| 1 打印机脱机 PC-1024 | 打印机/P2 | 已解决 / 自动闭环 | 0.5455 | 3 |
| 2 核心数据库宕机 | 服务器/P0 | 转人工（张伟） | 0.1875 | 2 |
| 3 示波器驱动装不上 | 硬件/P2 | 转人工（孙磊） | 0.1935 | 3 |
| 4 VPN 密码重置 | 账号权限/P3 | 已解决 / 自动闭环 | 0.75 | 3 |
| 5 投影仪无信号 PJ-03 | 硬件/P2 | 转人工（孙磊） | 0.2 | 3 |

### 15.2 离线模式（`LLM_MODE=mock`）

无需 API Key 即跑通全流程：`demo_test.py` **5/5 通过**，REST 接口、三 Tab 前端、PowerShell 脚本均验证通过。

### 15.3 已知非缺陷现象（如实记录）

- **重跑存在 LLM 非确定性**：同一工单重跑结果可能不同（实测工单 #11 首次 `转人工`、重跑 `已解决/自动闭环`），
  根因是真实模型两次生成的检索 `keywords` 不同导致 `difflib` 相似度变化。关键分支规则（P0 / 高危类型 / 阈值）由代码确定性裁决，不受影响。
- **真实模型与 mock 分类口径略有差异**（如样例3 mock 判「软件」、真实判「硬件」），但最终分支结果一致。

### 15.4 交付状态

- 依赖：`requirements.txt` 全量安装成功（langgraph 1.2.12 / openai 3.24.0 / streamlit 1.65.0 / fastapi 0.142.2 等）。
- 数据：验收后数据库已重置为纯净种子状态（历史工单 10 / 设备 10 / 工程师 6）。
- 一键启动：`run.ps1`（及 `run_backend.ps1` / `run_frontend.ps1`）语法与命令均已实测通过。