# Tasks：IT 运维智能工单自动处理 Agent

> 配套文档：[spec.md](spec.md) ｜ [checklist.md](checklist.md)
> 说明：按阶段推进，每阶段完成即可验证。任务 ID 形如 `T{阶段}.{序号}`。

---

## 阶段一览

| 阶段 | 名称 | 目标 | 依赖 |
|---|---|---|---|
| P0 | 脚手架与配置 | 目录结构、依赖、配置、日志系统就绪 | — |
| P1 | 数据层 | ORM 模型 + 建表 + 种子数据 | P0 |
| P2 | 工具层 | 3 个业务工具 + 注册表（Function Calling Schema） | P1 |
| P3 | LLM 层 | DeepSeek 客户端（结构化输出 + 工具调用 + mock 兜底） | P0 |
| P4 | LangGraph 状态机 | State / 节点 / 条件边 / 图组装 | P2, P3 |
| P5 | 服务与 API | 工单服务、日志服务、FastAPI 路由 | P4 |
| P6 | Streamlit 前端 | 提交工单 / 工单列表 / Agent 日志 | P5 |
| P7 | 测试与交付 | 测试样例脚本、一键启动、README | P6 |

---

## P0 脚手架与配置

| ID | 任务 | 产出 | 验收 |
|---|---|---|---|
| T0.1 | 建立目录结构 | `app/{db,schemas,llm,tools,agent,services,api}`、`frontend/`、`scripts/`、`data/`、`logs/` | 目录存在，含 `__init__.py` |
| T0.2 | 编写 `requirements.txt` | 依赖清单 | `pip install -r requirements.txt` 成功 |
| T0.3 | 配置模块 `app/config.py` | pydantic-settings 读取 `.env` | 缺 Key 不崩溃（mock 模式可跑） |
| T0.4 | `.env.example`（+ 生成 `.env`） | 环境变量模板 | 字段与 spec §10 一致 |
| T0.5 | 日志系统 `app/logging_config.py` | 控制台 + `RotatingFileHandler` | 打印日志同时写入 `logs/app.log` |

**阶段验收**：`py -c "from app.config import settings"` 无报错；日志文件生成。

---

## P1 数据层

| ID | 任务 | 产出 | 验收 |
|---|---|---|---|
| T1.1 | 引擎与会话 `app/db/base.py` | `engine`/`SessionLocal`/`Base`/`get_db` | 能连接 SQLite 并建目录 |
| T1.2 | ORM 模型 `app/db/models.py` | `Ticket`/`AgentLog`/`DeviceAsset`/`Engineer` | 字段与 spec §4 一致 |
| T1.3 | Pydantic 模型 `app/schemas/ticket.py` | 请求/响应/日志 Schema | 校验生效（必填、枚举、urgency） |
| T1.4 | 种子数据 `app/db/seed.py` | 10+ 历史已解决工单、8 台设备、5 名工程师 | 重复执行幂等 |
| T1.5 | 初始化脚本 `scripts/init_db.py` | 建表 + 灌种子 | `py scripts/init_db.py` 后 `data/itops.db` 有数据 |

**阶段验收**：`sqlite3`/SQLAlchemy 查询到种子数据；历史工单覆盖打印机/账号/网络/服务器等类型。

---

## P2 工具层（原生 Function Calling）

| ID | 任务 | 产出 | 验收 |
|---|---|---|---|
| T2.1 | 工具注册表 `app/tools/registry.py` | 工具函数注册 + OpenAI Tools Schema 导出（`TOOLS_SPEC` / `dispatch()`） | `TOOLS_SPEC` 含 3 个工具定义 |
| T2.2 | 工具1 `app/tools/ticket_search.py` | 历史工单检索 + `difflib` 相似度打分 | 传入类型/关键词返回 top-k 与 `best_score` |
| T2.3 | 工具2 `app/tools/asset_query.py` | 设备资产查询 | 命中返回资产；未命中返回 `found:false` |
| T2.4 | 工具3 `app/tools/ticket_status.py` | 工单状态更新（写库 + 写日志） | 状态与 `updated_at` 正确变更 |
| T2.5 | 工具单元自测 `scripts/test_tools.py` | 3 工具的直调验证 | 离线可直接运行并打印结果 |

**阶段验收**：`py scripts/test_tools.py` 三个工具全部返回预期结构；工具3 变更可在 DB 中查到。

---

## P3 LLM 层

| ID | 任务 | 产出 | 验收 |
|---|---|---|---|
| T3.1 | DeepSeek 客户端 `app/llm/client.py` | `openai` SDK 封装（base_url/model/超时/重试） | `LLM_MODE=real` 下可正常对话 |
| T3.2 | 结构化输出 `app/llm/schemas.py` + `extract_json()` | 工单解析模型、分类模型；JSON 解析 + 失败重试1次 | 返回 Pydantic 对象 |
| T3.3 | 工具调用接口 | `chat_with_tools(messages, tools)` 返回 `message.tool_calls` | 返回原生 tool_calls 结构 |
| T3.4 | mock 兜底 `app/llm/mock.py` | 规则版：抽取/分类/工具决策/方案生成 | `LLM_MODE=mock` 全流程可离线跑通 |

**阶段验收**：两种模式均可产出「解析+分类+工具决策+方案」四类结果，接口签名一致。

---

## P4 LangGraph 状态机（核心）

| ID | 任务 | 产出 | 验收 |
|---|---|---|---|
| T4.1 | State 定义 `app/agent/state.py` | `TicketState`（含 `messages`、`iterations`、证据字段） | 字段与 spec §5.1 一致 |
| T4.2 | 解析/分类 Agent `app/agent/agents/intake.py` / `app/agent/agents/triage.py` | LLM 抽取 + 分类，规则兜底，写日志 | 样例1/2 字段抽取正确 |
| T4.3 | 检索 Agent + 工具执行 `app/agent/agents/research.py`（含 `execute_tools`） | ReAct 子循环：LLM 决策 → 执行工具 → 回灌 `ToolMessage`；模型漏调检索工具时内部确定性补齐证据 | 日志可见真实 `tool_calls` 与结果 |
| T4.4 | 处置 Agent `app/agent/agents/resolver.py` | 两个内部分支：自动闭环方案 / 转人工派单通知 | 两类分支仍可达；状态/assignee/notification 正确 |
| T4.5 | 调度与路由 `app/agent/routing.py::plan_next` + `route_supervisor` / `route_after_research`（`decide()` 实现 spec §5.4 规则） | 确定性 Agent 调度 + 分支路由 | 规则分支可单测断言 |
| T4.6 | 审核 Agent `app/agent/agents/critic.py` | 对自动闭环方案做上线前交叉审核（pass/reject），reject 退回处置 Agent 重做，受 `max_review_rounds` 保护 | 审核 Agent 参与自动闭环，重试有上限 |
| T4.7 | 收尾 + 图组装 `app/agent/agents/finalize.py` / `app/agent/graph.py` | Supervisor 中心化拓扑 + 2 处条件边 + 循环上限 | `graph.invoke()` 端到端返回终态 |

**阶段验收**：`scripts/test_graph.py` 用 5 条样例直跑图，自动闭环/转人工两类分支均被覆盖。

---

## P5 服务与 API

| ID | 任务 | 产出 | 验收 |
|---|---|---|---|
| T5.1 | 工单服务 `app/services/ticket_service.py` | 建单/查单/列表/状态流转 | 状态枚举受控 |
| T5.2 | 日志服务 `app/services/log_service.py` | `agent_logs` 写入/查询 | 每步日志可按 `ticket_id` 拉取 |
| T5.3 | Agent 执行封装 `app/services/agent_runner.py` | 组装初始 State → 调图 → 结果回写工单 | 一次调用完成闭环 |
| T5.4 | 路由 `app/api/routes.py` | spec §7 全部接口 | 各接口返回 `{code,message,data}` |
| T5.5 | 应用入口 `app/main.py` | FastAPI 装配、CORS、全局异常处理、启动建表 | `GET /api/health` 返回 ok |

**阶段验收**：`py -m uvicorn app.main:app --port 8000` 后，`POST /api/tickets` 提交样例能返回终态；`GET /api/tickets/{id}/logs` 有完整日志。

---

## P6 Streamlit 前端

| ID | 任务 | 产出 | 验收 |
|---|---|---|---|
| T6.1 | 页面骨架 `frontend/streamlit_app.py` | 三 Tab + 侧边栏（后端地址/健康/LLM模式） | 页面可访问，健康状态正确 |
| T6.2 | 提交工单页 | 表单 + 结果卡片（状态/决策/方案/通知） | 提交后即时看到处理结果 |
| T6.3 | 工单列表页 | 表格 + 状态筛选 + 详情 | 数据与后端一致，可刷新 |
| T6.4 | Agent 日志页 | 选择工单 → 节点/工具调用时间线 | 展示 detail JSON 与级别 |

**阶段验收**：前端可完整演示「提交 → 自动闭环 / 转人工 → 查日志」。

---

## P7 测试与交付

| ID | 任务 | 产出 | 验收 |
|---|---|---|---|
| T7.1 | 测试样例脚本 `scripts/demo_test.py` | 批量提交 5 条样例并打印结果摘要 | 输出与 spec §11 预期一致 |
| T7.2 | 一键启动 `run.ps1` + 分启动脚本 | 后端+前端一键拉起 | Trae 终端执行成功 |
| T7.3 | `README.md` | 目录结构、启动命令、测试样例、FAQ | 新人照做可跑通 |
| T7.4 | 端到端验收 | 按 checklist 全量自检 | checklist 全绿 |
| T7.5 | 代码注释与清理 | 关键节点/工具/路由中文注释 | 无调试残留、无未用依赖 |

**阶段验收**：从零环境按 README 操作，可复现全部验收项。

---

## 里程碑

- **M1（P0–P1）**：项目骨架 + 数据就绪，可查 DB。
- **M2（P2–P3）**：工具与 LLM 层就绪，可单独自测。
- **M3（P4）**：LangGraph 状态机跑通，两类分支均可达 —— **核心里程碑**。
- **M4（P5–P6）**：API + 前端可交互演示。
- **M5（P7）**：测试样例 + 一键启动 + README 交付。