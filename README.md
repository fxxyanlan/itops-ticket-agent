# IT 运维智能工单自动处理 Agent

基于 **LangGraph 状态机 + DeepSeek 原生 Function Calling** 的 IT 运维工单自动闭环 Demo。

员工用自然语言提交故障工单，Agent 自动完成：**工单解析 → 意图/故障分类 → 多工具调用 → 分支决策（自动闭环 / 转人工）→ 状态流转落库 → 生成方案或派单通知**。

> 注意：本项目的核心是 **业务流程编排（状态机）+ 原生工具调用 + 工单状态流转**，不是知识库问答。
> 编排框架只用了 LangGraph，未使用 CrewAI / Agno / AutoGen 等重型多 Agent 框架。

---

## 1. 功能特性

| 能力 | 说明 |
|---|---|
| 工单解析 | LLM 结构化抽取标题、故障类型、紧急等级、设备编号、联系方式 |
| 意图分类 | 区分「可自动解决」与「必须人工处理」 |
| 原生 Function Calling | 3 个业务工具由模型自主决策调用（非硬编码 if-else） |
| 条件边分支 | 2 处 LangGraph 条件边：工具循环路由 + 自动/人工分流 |
| 状态持久化 | 工单状态与 Agent 日志全部落 SQLite |
| 全链路日志 | 控制台 + `logs/app.log` + DB `agent_logs`，每步可查 |
| 离线可用 | `LLM_MODE=mock` 无需 API Key 即可完整演示 |
| 可视化前端 | Streamlit：提交工单 / 工单列表 / Agent 处理日志 |

---

## 2. 目录结构

```
demo/
├── README.md                   # 本文档
├── 使用说明.md                 # 使用手册（功能/操作流程/FAQ/关键功能点详解）
├── spec.md / tasks.md / checklist.md   # 规格、任务拆解、验收清单
├── requirements.txt
├── .env.example                # 环境变量模板
├── run.ps1                     # 一键启动（后端 + 前端）
├── run_backend.ps1 / run_frontend.ps1  # 分启动
├── app/
│   ├── config.py               # 配置（pydantic-settings）
│   ├── constants.py            # 状态/故障类型/高危类型等常量
│   ├── logging_config.py       # 日志系统（控制台 + 滚动文件）
│   ├── main.py                 # FastAPI 入口
│   ├── db/
│   │   ├── base.py             # engine / session / init_db
│   │   ├── models.py           # Ticket / AgentLog / DeviceAsset / Engineer
│   │   └── seed.py             # 历史工单 + 设备 + 工程师种子数据
│   ├── schemas/ticket.py       # Pydantic 请求/响应模型
│   ├── llm/
│   │   ├── client.py           # DeepSeek 客户端（real / mock 双模式）
│   │   ├── schemas.py          # 结构化输出模型 + JSON 提取
│   │   └── mock.py             # 离线规则实现
│   ├── tools/                  # ★ 供模型调用的 3 个工具
│   │   ├── registry.py         # 工具注册表 + TOOL_SPECS + dispatch()
│   │   ├── ticket_search.py    # 工具1：检索历史同类型工单方案
│   │   ├── asset_query.py      # 工具2：查询设备资产
│   │   └── ticket_status.py    # 工具3：更新工单状态
│   ├── agent/                  # ★ LangGraph 状态机
│   │   ├── state.py            # TicketState 定义
│   │   ├── nodes.py            # 8 个节点实现
│   │   ├── routing.py          # 条件边路由 + 分支判定规则
│   │   └── graph.py            # 图组装（StateGraph + 条件边）
│   ├── services/
│   │   ├── ticket_service.py   # 工单 CRUD / 状态流转 / 派单
│   │   ├── log_service.py      # Agent 日志落库
│   │   └── agent_runner.py     # 组初始状态 → 调图 → 回写结果
│   └── api/routes.py           # REST 接口
├── frontend/streamlit_app.py   # Streamlit 三 Tab 页面（不能命名为 app.py，会与 app 包冲突）
├── scripts/
│   ├── init_db.py              # 建表 + 种子数据
│   ├── test_tools.py           # 工具层自测
│   ├── test_graph.py           # 状态机逐节点追踪
│   ├── demo_test.py            # 5 条样例端到端验收
│   └── samples.py              # 测试样例定义
├── data/                       # SQLite 数据库文件
└── logs/                       # 运行日志
```

---

## 3. 快速开始（Trae 内置终端）

```powershell
# 一键启动（推荐）：自动完成 虚拟环境 → 依赖安装 → .env → 建库 → 起后端(8000) → 起前端(8501)
.\run.ps1
```

`run.ps1` 幂等：仅在缺失时自动创建虚拟环境、安装依赖、生成 `.env`；数据库初始化可反复执行。
启动后自动打开浏览器；按 `Ctrl+C` 停止前端，脚本会**同时关闭后端**。
如需真实大模型，编辑 `.env` 填入 `DEEPSEEK_API_KEY` 后重新运行（`LLM_MODE=auto`：无 Key 时自动走离线 mock）。
日志：后端 `logs\backend.out.log` / `logs\backend.err.log`，应用 `logs\app.log`。

手动分步（等价，供排障）：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env      # 如需真实模型，编辑该文件填入 DEEPSEEK_API_KEY
.\.venv\Scripts\python.exe scripts\init_db.py
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
.\.venv\Scripts\python.exe -m streamlit run frontend\streamlit_app.py --server.port 8501
```

访问：

- 前端页面：http://127.0.0.1:8501
- 后端接口文档：http://127.0.0.1:8000/docs

> 若 PowerShell 禁止执行脚本，先执行：
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`
> 或直接用两条命令分别启动：
> `py -m uvicorn app.main:app --port 8000` 与 `py -m streamlit run frontend/streamlit_app.py --server.port 8501`

### 只跑后端 / 只跑前端

```powershell
.\run_backend.ps1     # http://127.0.0.1:8000/docs
.\run_frontend.ps1    # http://127.0.0.1:8501
```

---

## 4. LangGraph 状态机

```
START → parse_ticket → classify → agent
agent ──(有 tool_calls)──► execute_tools ──► agent        # ReAct 循环（≤6 轮）
agent ──(条件边1 route_after_agent)──┬─► gather_evidence ──(条件边2)──┐
                                     ├─► auto_resolve   ────────────┴─► finalize → END
                                     └─► escalate_human ──────────────► finalize → END
```

| 节点 | 职责 |
|---|---|
| `parse_ticket` | LLM 抽取工单字段并回写 |
| `classify` | 判定可自动解决 / 必须人工 |
| `agent` | 携带工具声明调用模型，解析原生 `tool_calls` |
| `execute_tools` | 执行工具并把结果回灌为 `tool` 消息 |
| `gather_evidence` | 模型漏调检索工具时的确定性兜底 |
| `auto_resolve` | 生成解决步骤 → 状态「已解决」+ 标记自动闭环 |
| `escalate_human` | 按故障类型分派工程师 → 状态「转人工」+ 生成通知 |
| `finalize` | 收尾汇总日志 |

### 分支判定规则（代码确定性实现，保证可解释）

命中**任一**条件即 **转人工**：

1. 紧急等级为 `P0`；
2. 故障类型属于高危：`服务器 / 数据库 / 网络核心 / 安全事件`；
3. 模型分类判定 `auto_resolvable = false`；
4. 历史方案最高匹配度 `< MATCH_THRESHOLD`（默认 `0.42`）。

否则 → **自动闭环**。`decision_reason` 字段会记录命中的具体原因。

---

## 5. 三个工具（原生 Function Calling）

| 工具 | 作用 | 数据源 |
|---|---|---|
| `search_similar_tickets` | 按故障类型 + 描述检索历史已解决工单，返回 top-3 与最高匹配度 | `tickets` 表中 `status='已解决'` 的记录 |
| `get_device_asset` | 按设备编号查询型号/系统/IP/位置/责任人/保修/健康状态 | `device_assets` 表 |
| `update_ticket_status` | 更新工单状态（新建/处理中/已解决/转人工）并追加备注 | `tickets` 表 + `agent_logs` |

声明集中定义在 [registry.py](app/tools/registry.py)，实现在 `app/tools/*.py`。

---

## 6. REST 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/tickets` | 提交工单，同步跑完 Agent 并返回终态 |
| GET | `/api/tickets?status=&limit=` | 工单列表（可按状态过滤） |
| GET | `/api/tickets/{id}` | 工单详情 |
| GET | `/api/tickets/{id}/logs` | 该工单的 Agent 处理日志 |
| POST | `/api/tickets/{id}/rerun` | 重新触发 Agent |
| GET | `/api/devices` | 设备资产列表 |
| GET | `/api/health` | 健康检查（LLM 模式 / DB 连通性） |

统一响应体：`{"code": 0, "message": "ok", "data": ...}`。

---

## 7. 测试样例

```powershell
# 5 条样例端到端验收（进程内直接执行，无需启动服务）
.\.venv\Scripts\python.exe scripts\demo_test.py

# 通过已启动的后端提交
.\.venv\Scripts\python.exe scripts\demo_test.py --http

# 工具层自测
.\.venv\Scripts\python.exe scripts\test_tools.py

# 单条工单逐节点追踪状态机
.\.venv\Scripts\python.exe scripts\test_graph.py
.\.venv\Scripts\python.exe scripts\test_graph.py "核心数据库服务器宕机了，业务全部中断"
```

| # | 输入描述 | 期望结果 |
|---|---|---|
| 1 | 我的打印机 PC-1024 打印一直提示脱机，重启打印服务也不行 | 已解决 / 自动闭环 |
| 2 | 核心数据库服务器宕机了，业务全部中断，十万火急！ | 转人工（P0 + 高危类型） |
| 3 | 公司新采购的示波器驱动装不上，型号 XYZ-9 | 转人工（无匹配方案） |
| 4 | 我忘记 VPN 密码了，需要重置 | 已解决 / 自动闭环 |
| 5 | 会议室投影仪没信号，设备编号 PJ-03 | 转人工（匹配度不足） |

### 用 curl 单测

```powershell
curl -X POST http://127.0.0.1:8000/api/tickets `
  -H "Content-Type: application/json" `
  -d '{\"description\": \"我的打印机 PC-1024 打印一直提示脱机，重启打印服务也不行\", \"user_contact\": \"张小明\"}'
```

---

## 8. 配置项（.env）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 空 | DeepSeek API Key（mock 模式下可留空） |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容地址 |
| `DEEPSEEK_MODEL` | `deepseek-chat` | 模型名 |
| `LLM_MODE` | `auto` | `auto`（有 Key 走真实，否则 mock）/ `real` / `mock` |
| `DATABASE_URL` | `sqlite:///./data/itops.db` | 数据库连接串 |
| `MATCH_THRESHOLD` | `0.42` | 历史方案相似度阈值，低于则转人工 |
| `MAX_ITERATIONS` | `6` | 单工单最大工具循环轮数 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `BACKEND_URL` | `http://127.0.0.1:8000` | 前端访问的后端地址 |

---

## 9. 日志与可观测

- **控制台**：实时打印每个节点与工具调用，便于观察 Agent 思考过程。
- **文件**：`logs/app.log`，5MB × 3 滚动。
- **数据库**：`agent_logs` 表按 `ticket_id` 记录每步（节点名、级别、摘要、JSON 细节），前端「Agent 处理日志」Tab 可视化。

日志示例：

```
2026-10-04 10:20:31 | INFO    | itops.agent.nodes | [ticket#1] [工单解析] 解析完成：类型=打印机｜等级=P2｜设备=PC-1024
2026-10-04 10:20:33 | INFO    | itops.agent.nodes | [ticket#1] [Agent决策] 第 1 轮决策：调用工具 [search_similar_tickets、get_device_asset]
2026-10-04 10:20:33 | INFO    | itops.agent.nodes | [ticket#1] [tool:search_similar_tickets] 检索到 3 条历史工单，最高匹配度 0.62
2026-10-04 10:20:34 | WARN    | itops.agent.nodes | [ticket#2] [转人工] ⚠️ 转人工：状态 → 转人工｜原因：紧急等级 P0（业务中断级），必须人工立即处理
```

---

## 10. 常见问题（FAQ）

**Q1：没有 DeepSeek API Key 能跑吗？**
可以。`LLM_MODE=auto` 且未配置 Key 时会自动使用 `mock`（离线规则），全流程、状态流转、日志、前端均可正常演示。

**Q2：真实模式下想切换模型？**
修改 `.env` 中 `DEEPSEEK_MODEL`（如 `deepseek-chat`），重启后端即可。

**Q3：为什么工单「转人工」了，明明有类似历史工单？**
检查 `decision_reason`，常见原因：① 紧急等级 P0；② 故障类型属于高危（服务器/数据库等）；③ 相似度低于 `MATCH_THRESHOLD`。可下调 `MATCH_THRESHOLD` 或补充种子数据。

**Q4：数据想重置？**
执行 `.\.venv\Scripts\python.exe scripts\init_db.py --reset`，会清空并重建全部表与种子数据。

**Q5：模型没有调用工具怎么办？**
`gather_evidence` 节点会确定性补齐历史工单检索，保证分支判定有据；同时日志里会有 `[证据补齐]` 的 WARN 记录。

**Q6：端口被占用？**
`run.ps1` 默认 8000/8501，可改用
`py -m uvicorn app.main:app --port 8001` 与 `py -m streamlit run frontend/streamlit_app.py --server.port 8502`，并在前端侧边栏修改后端地址。

**Q7：`pip install` 很慢或超时？**
换国内镜像：
`.\.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`

**Q8：运行 `.ps1` 报「无法加载文件，未对文件进行数字签名」？**
执行 `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` 后再运行；或直接使用 README 里的两条命令行方式。

**Q9：为什么前端入口文件名是 `streamlit_app.py` 而不是 `app.py`？**
Streamlit 会把脚本所在目录加入 `sys.path`。若命名为 `app.py`，`import app.config` 会解析到这个脚本自身而不是项目的 `app` 包，
从而抛出 `ModuleNotFoundError: No module named 'app.config'; 'app' is not a package`。

---

## 11. 部署到 GitHub（分步教程）

### 11.0 前置检查

- 已安装 Git：`git --version` 能输出版本
- 已有 GitHub 账号
- 确认 `.gitignore` 已排除敏感与大文件。本仓库已包含：`.venv/`、`__pycache__/`、`*.pyc`、`.env`、`data/*.db`、`logs/*.log*`、`.streamlit/`、`.pytest_cache/`

### 11.1 首次发布（本地 → GitHub）

**① 初始化本地仓库**

```powershell
git init
git branch -M main
```

**② 提交前安全检查（关键一步）**

```powershell
git status --short
git check-ignore -v .env data/itops.db logs/app.log
```

> 上面 `check-ignore` 应为每个文件都输出一条忽略规则。若 `.env` **没有**被忽略，请先修正 `.gitignore` 再继续；**任何情况下都不要提交 `.env`**。

**③ 暂存并提交**

```powershell
git add .
git commit -m "feat: IT 运维智能工单自动处理 Agent 初始版本"
```

**④ 在 GitHub 创建空仓库**

1. 打开 https://github.com/new
2. 填写仓库名（如 `itops-ticket-agent`），选择 Public / Private
3. **不要**勾选 "Add a README / .gitignore / license"（避免与本地首次提交冲突）
4. 点击 **Create repository**

**⑤ 关联远程并推送**

```powershell
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

**⑥ 验证**

- 刷新 GitHub 仓库页面，应能看到代码与 `README.md`
- 确认**没有**上传 `.env`、`.venv/`、`data/itops.db`、`logs/` 等文件

### 11.2 后续更新

```powershell
git add .
git commit -m "fix: 修复 xxx"
git push
```

### 11.3 使用 SSH（可选，免每次输入凭据）

```powershell
ssh-keygen -t ed25519 -C "你的邮箱"
# 将 ~/.ssh/id_ed25519.pub 的内容粘贴到 GitHub → Settings → SSH and GPG keys → New SSH key
git remote set-url origin git@github.com:<你的用户名>/<仓库名>.git
```

### 11.4 推送认证失败排查

- GitHub 自 2021 起**不再支持账号密码推送**，需使用 **PAT（Personal Access Token）** 或 **SSH**。
- PAT 生成：GitHub → Settings → Developer settings → Personal access tokens；推送时在"密码"处粘贴 token。
- 若 Windows 凭据管理器缓存了旧密码：控制面板 → 凭据管理器 → Windows 凭据 → 删除 `git:https://github.com`。

### 11.5 密钥安全（重要）

若曾误将 API Key 提交过，即使之后删除文件，**历史提交中仍可检出**。此时应：
1. 立即到 DeepSeek 控制台**轮换（重置）该 Key**；
2. 用 `git filter-repo` 清理历史，或删除仓库后重新按 §11.1 发布。