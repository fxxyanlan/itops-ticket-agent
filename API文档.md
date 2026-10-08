# API 文档 · IT 运维智能工单自动处理 Agent

> 版本：与 `app/__version__` 同步 ｜ 基地址：`http://127.0.0.1:8000` ｜ 交互式文档：`/docs`（Swagger UI）、`/redoc`

本文件面向**接口调用方**（前端、第三方系统、测试同学），完整描述请求参数、返回结构、
错误语义与性能特征。测试过程与实测数据见 [API测试报告.md](API测试报告.md)。

---

## 1. 通用约定

### 1.1 统一响应信封

所有接口（**含全部错误响应**）返回同一结构：

```json
{ "code": 0, "message": "ok", "detail": null, "data": {} }
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `code` | int | 业务码。`0` 表示成功；非 0 与 HTTP 状态码一致（如 404 / 422 / 500） |
| `message` | string | 人类可读的结果描述 |
| `detail` | string \| null | **兼容字段**，前端错误提示读取它；成功时为 `null` |
| `data` | object \| array \| null | 业务数据主体；错误时为 `null` |

> 参数校验失败时额外返回 `errors`（结构化错误列表，便于前端逐字段定位）。

### 1.2 HTTP 状态码语义

| 状态码 | 含义 | 触发场景 |
|---|---|---|
| `200` | 成功 | 常规查询 / 同步提交完成 |
| `202` | 已受理 | 仅 `/api/tickets/async`，Agent 转后台执行 |
| `404` | 资源不存在 | 工单 ID 不存在（详情 / 日志 / 状态 / 重跑） |
| `422` | 参数校验失败 | 缺字段、超长、枚举非法、分页越界 |
| `500` | 服务内部异常 | 兜底；默认**不回显**内部异常文本（可配置开启） |

### 1.3 分页约定

列表类接口统一使用 `limit` + `offset`：

- `limit`：单页条数，`1 ≤ limit ≤ 500`（服务端上限 `MAX_PAGE_SIZE`）；
- `offset`：偏移量，`≥ 0`；
- 越界返回 `422`，不做静默截断。

### 1.4 响应时间与缓存

- 每个响应携带 `X-Response-Time-Ms` 头（服务端处理耗时，毫秒）。
- 只读接口（列表 / 详情 / 日志 / 设备 / 健康检查）带 **TTL 缓存**（默认 30s，健康探活 5s）；
  任何写路径（创建 / 状态更新 / 写日志）都会**集中失效**相关缓存，保证读到的是最新数据。
- 提交类接口（`POST /api/tickets`、`/rerun`）**同步执行完整 Agent 编排**，耗时由 Agent 主导，
  **不属于 300ms 目标范围**；需要快速受理请使用异步接口。

### 1.5 枚举字典

| 枚举 | 取值 |
|---|---|
| 工单状态 `status` | `新建` / `处理中` / `已解决` / `转人工` |
| 处理模式 `resolution_mode` | `自动闭环` / `转人工` |
| 故障类型 `fault_type` | `打印机` / `账号权限` / `网络` / `硬件` / `软件` / `服务器` / `邮箱` / `数据库` / `安全事件` / `其他` |
| 紧急等级 `urgency` | `P0` / `P1` / `P2` / `P3` |

**硬规则**（由代码判定，不受模型输出影响）：命中 `P0`、故障类型属高危（`服务器`/`数据库`/`网络核心`/`安全事件`）、
分类判定需人工、或历史最佳匹配度 `< 0.42` → 一律 `转人工`。

---

## 2. 接口清单

| 方法 | 路径 | 说明 | 响应时间定位 |
|---|---|---|---|
| GET | `/api/health` | 健康检查（LLM 模式 / 数据库连通性） | 只读，TTL 缓存 |
| GET | `/api/metrics` | 运行指标（逐路径分位耗时 / 错误率 / 缓存命中 / 异步任务） | 只读 |
| POST | `/api/tickets` | 提交工单（**同步**跑完 Agent） | Agent 主导，非 300ms 目标 |
| POST | `/api/tickets/async` | 提交工单（**异步**，立即 202） | 满足 300ms 目标 |
| GET | `/api/tickets/{id}/status` | 异步任务状态轮询 | 只读，TTL 缓存 |
| GET | `/api/tickets` | 工单列表（状态过滤 + 分页） | 只读，TTL 缓存 |
| GET | `/api/tickets/{id}` | 工单详情 | 只读，TTL 缓存 |
| GET | `/api/tickets/{id}/logs` | Agent 处理日志（分页） | 只读，TTL 缓存 |
| POST | `/api/tickets/{id}/rerun` | 重新触发 Agent | Agent 主导，非 300ms 目标 |
| GET | `/api/devices` | 设备资产列表 | 只读，TTL 缓存 |

---

## 3. 接口详述

### 3.1 `GET /api/health`

健康检查。数据库探活为轻量 `SELECT 1`，结果按 5s 缓存。

**响应 200**

```json
{
  "code": 0,
  "message": "ok",
  "detail": null,
  "data": {
    "status": "ok",
    "llm_mode": "mock",
    "model": "deepseek-chat",
    "database": "ok",
    "match_threshold": 0.42
  }
}
```

> 探活失败时 `status="degraded"`、`database="error: <异常类名>"`（仅回传异常类型，不回显堆栈）。

---

### 3.2 `GET /api/metrics`

运行期指标快照。样本为**进程内环形缓冲**（每路径最近 2000 条），服务重启即清零。

**响应 200**

```json
{
  "code": 0, "message": "ok", "detail": null,
  "data": {
    "runtime": {
      "total_requests": 1240,
      "total_errors": 0,
      "overall_error_rate": 0.0,
      "sample_window_per_path": 2000,
      "paths": [
        { "path": "/api/tickets", "count": 200, "errors": 0,
          "error_rate": 0.0, "p50_ms": 10.0, "p95_ms": 59.85, "p99_ms": 68.48, "max_ms": 71.2 }
      ]
    },
    "cache": { "hits": 1180, "misses": 60, "entries": 4, "hit_rate": 0.9516 },
    "tasks": { "running": 0, "done": 20, "failed": 0, "total": 20 }
  }
}
```

> 约定：`status_code >= 400` 计入 `errors`。

---

### 3.3 `POST /api/tickets` （同步提交）

落库后**立即同步执行完整 Agent 闭环**并返回终态结果。

**请求体**

| 字段 | 类型 | 必填 | 约束 | 说明 |
|---|---|---|---|---|
| `description` | string | 是 | 去空白后长度 `2 ~ 2000` | 自然语言故障描述 |
| `user_contact` | string | 否 | `≤ 128`；空白串归一为 `null` | 联系方式 |
| `device_id` | string | 否 | `≤ 64`；空白串归一为 `null` | 设备编号（Agent 也会尝试自动抽取） |

**请求示例**

```json
{
  "description": "我的打印机 PC-1024 打印一直提示脱机，重启打印服务也不行",
  "user_contact": "张小明",
  "device_id": "PC-1024"
}
```

**响应 200**

```json
{
  "code": 0, "message": "处理完成", "detail": null,
  "data": {
    "ticket": {
      "id": 11, "title": "打印机提示脱机无法打印",
      "description": "我的打印机 PC-1024 打印一直提示脱机，重启打印服务也不行",
      "fault_type": "打印机", "urgency": "P2",
      "device_id": "PC-1024", "user_contact": "张小明",
      "status": "已解决", "resolution_mode": "自动闭环",
      "resolution": "1. 重启 Print Spooler；2. 清空打印队列；…",
      "assignee": null, "notify_message": null,
      "decision_reason": "命中成熟方案（匹配度 0.55 ≥ 阈值 0.42），可自动闭环",
      "created_at": "2026-10-08T20:35:18", "updated_at": "2026-10-08T20:35:19"
    },
    "state": {
      "ticket_id": 11, "fault_type": "打印机", "urgency": "P2",
      "auto_resolvable": true, "best_match_score": 0.5455, "iterations": 3,
      "agent_trace": [ { "node": "调度中枢", "action": "调度决策 → 解析Agent" } ]
    }
  }
}
```

**错误**

| 状态码 | 场景 |
|---|---|
| 422 | `description` 缺失 / 长度不合规 / 可选字段超长 |
| 500 | Agent 执行异常 |

---

### 3.4 `POST /api/tickets/async` （异步提交）

落库并登记后台任务后**立即返回 202**，由状态接口轮询终态。用于满足提交类接口的响应时间目标。

**请求体**：同 `POST /api/tickets`。

**响应 202**

```json
{
  "code": 0, "message": "已受理，Agent 正在后台处理", "detail": null,
  "data": { "ticket_id": 20021, "status": "新建", "poll_url": "/api/tickets/20021/status" }
}
```

> 受理耗时仅含落库 + 任务登记；后台 Agent 与请求解耦。

---

### 3.5 `GET /api/tickets/{id}/status`

异步任务状态轮询。建议以指数退避轮询至 `terminal=true`。

**响应 200**

```json
{
  "code": 0, "message": "ok", "detail": null,
  "data": {
    "ticket_id": 20021, "state": "done", "terminal": true,
    "ticket_status": "转人工", "resolution_mode": "转人工",
    "elapsed_ms": 137.58, "error": ""
  }
}
```

| `state` | 含义 |
|---|---|
| `running` | 后台执行中（`terminal=false`） |
| `done` | 成功完成（`terminal=true`） |
| `failed` | 执行失败，`error` 非空（`terminal=true`） |
| `unknown` | 未登记（进程重启 / 该工单由同步接口创建），`terminal` 以数据库状态判定 |

**错误**：`404` 工单不存在。

---

### 3.6 `GET /api/tickets`

工单列表，按 `id` 倒序。

**查询参数**

| 参数 | 类型 | 默认 | 约束 |
|---|---|---|---|
| `status` | string | 无 | 仅接受 4 个状态枚举之一，非法值返回 422 |
| `limit` | int | 100 | `1 ~ 500` |
| `offset` | int | 0 | `≥ 0` |

**响应 200**：`data` 为工单对象数组（字段同 3.3 的 `ticket`）。

---

### 3.7 `GET /api/tickets/{id}`

工单详情。**错误**：`404`（`code`/`message`/`detail` 均为 404 语义）。

---

### 3.8 `GET /api/tickets/{id}/logs`

该工单的 Agent 处理日志（时间正序，支持分页）。

| 参数 | 默认 | 约束 |
|---|---|---|
| `limit` | 500 | `1 ~ 2000` |
| `offset` | 0 | `≥ 0` |

**响应 200**

```json
{
  "code": 0, "message": "ok", "detail": null,
  "data": [
    { "id": 1, "ticket_id": 11, "node": "调度中枢", "level": "INFO",
      "message": "调度决策 → 解析Agent｜缺少结构化工单字段，先由解析 Agent 抽取",
      "detail": "{\"next\":\"intake\"}", "created_at": "2026-10-08T20:35:18" }
  ]
}
```

**错误**：`404` 工单不存在。

---

### 3.9 `POST /api/tickets/{id}/rerun`

对已存在工单重新触发 Agent（演示 / 重试）。同步执行，返回结构同 3.3。
**错误**：`404` 工单不存在；`500` Agent 异常。

---

### 3.10 `GET /api/devices`

设备资产列表。`limit` 默认 200，约束 `1 ~ 500`。

---

## 4. 典型调用时序（异步提交）

```text
客户端                         服务端
  │  POST /api/tickets/async      │
  │ ─────────────────────────────▶│  落库 + 登记任务 → 202
  │  ◀─────────────────────────────│  {ticket_id, poll_url}
  │                               │  （后台线程执行 Agent 编排）
  │  GET /api/tickets/{id}/status │
  │ ─────────────────────────────▶│  state=running, terminal=false
  │  ◀─────────────────────────────│
  │        … 轮询（0.5s→1s→2s）…   │
  │  GET /api/tickets/{id}/status │
  │ ─────────────────────────────▶│  state=done, terminal=true
  │  ◀─────────────────────────────│  ticket_status / resolution_mode
  │  GET /api/tickets/{id}/logs   │  查看逐节点轨迹
  │ ─────────────────────────────▶│
```

---

## 5. 可维护性与扩展约定

- **契约单一出口**：所有响应经 `ApiResponse` 信封与全局异常处理器产出，新增接口无需自建错误格式。
- **配置集中**：分页上限、缓存 TTL、检索候选上限等均来自 `app/config.py`，改配置不改代码。
- **枚举集中**：状态 / 故障类型 / 紧急等级定义在 `app/constants.py`，新增类型仅需在此登记。
- **降级可观测**：LLM 双模式（`real` / `mock`）由 `LLM_MODE` 控制，无 Key 也可离线端到端运行。
- **可观测性**：`/api/metrics` 暴露逐路径分位耗时与缓存命中率，便于线上定位退化。

---

## 6. 相关文档

| 文档 | 内容 |
|---|---|
| [API测试报告.md](API测试报告.md) | 测试方案、用例、性能对照、优化措施与指标达成情况 |
| [使用说明.md](使用说明.md) | 安装、配置、操作指南与故障排查 |
| [spec.md](spec.md) | 架构设计、数据模型、状态机与接口设计 |