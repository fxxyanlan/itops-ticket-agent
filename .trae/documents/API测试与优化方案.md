# API 真实数据测试与优化方案

## Context（为什么做这件事）

当前项目是一个 SQLite 自洽的单机 demo：7 个 REST 接口，仅 10 条种子工单。用户要求把它当作**可交付的专业工程产物**来验收——真实数据端到端测试、定位并修复性能与健壮性缺陷、产出 <300ms / 99.9% 准确率 / <0.1% 错误率的可核验指标、交付 API 文档与测试报告。

通读实现后确认了 4 个必须先解决的问题：

1. **SLA 冲突**：`POST /api/tickets` 与 `/rerun` 同步跑完整个 Agent（mock ~0.6s、真实 LLM 数秒），架构上不可能 <300ms。且 [streamlit_app.py](file:///c:/Users/xhkj/Documents/trae_projects/demo/frontend/streamlit_app.py#L101-L143) 与 [batch_ticket_test.py](file:///c:/Users/xhkj/Documents/trae_projects/demo/batch_ticket_test.py#L126-L218) 强依赖其「同步返回终态」。
2. **无生产数据源**：只有 10 条历史工单，任何性能测试都无意义。
3. **已定位的确定性缺陷**：`health()` 每请求执行 `create_all()`；Agent 每单写 19–21 次独立事务；检索全量加载 ORM 对象 + Python 端 difflib 打分且缺 `(status, fault_type)` 复合索引；404/500 返回 `{"detail":...}` 与成功响应 `{code,message,data}` **契约不一致**；`description` 无长度上限；`list_logs` 无分页。
4. **无可观测性**：无请求计时、无指标端点，产不出报告需要的 P95/P99。

**用户已确认的 4 项决策**：① 新增异步接口、保留同步接口（零破坏）；② 生成合成大数据集；③ 交付物为根目录中文文件；④ 引入 pytest。

**目标产物**：经测试与优化的 API 实现 + `API文档.md` + `API测试报告.md`，达到可发出面试邀请的专业水准。

---

## 1. 数据与基座（先打通，否则测无所测）

**`scripts/gen_benchmark_data.py`（新增）** — 可复现合成数据生成器
- `random.seed(42)` 确定性；分块 `add_all`（每 1000 条一提交）保证生成速度
- 规模：**20,000 条** `status='已解决'` 历史工单（10 类故障各 2,000 条，模板化变体让 difflib 分数有区分度）+ **2,000 台**设备 + 6 名工程师
- 写入**独立基准库** `data/benchmark.db`（通过 `DATABASE_URL` 覆盖），**不污染 demo 库**——否则会改变检索结果，破坏现有 26 个用例

**`app/db/base.py`（改）** — 引擎与 PRAGMA
- 通过 `event.listens_for(Engine, "connect")` 注入：`journal_mode=WAL`、`synchronous=NORMAL`、`busy_timeout=5000`、`foreign_keys=ON`
- 补 `pool_pre_ping=True`；**先探测 SQLAlchemy 对文件型 SQLite 的默认 poolclass**，确认后再决定是否显式传 `pool_size`（避免 `TypeError`）

**`app/db/models.py`（改）** — `tickets` 增加复合索引 `(status, fault_type)`，直接服务检索过滤

---

## 2. 性能优化（逐项可测量）

| # | 位置 | 问题 | 改法 |
|---|---|---|---|
| 1 | [routes.py](file:///c:/Users/xhkj/Documents/trae_projects/demo/app/api/routes.py#L16-L32) `health()` | 每请求 `init_db()` → `create_all()` 多次查 `sqlite_master` | 改为 TTL 5s 缓存的 `SELECT 1` 探活；建表只在 `lifespan` 做一次（已具备） |
| 2 | [ticket_search.py](file:///c:/Users/xhkj/Documents/trae_projects/demo/app/tools/ticket_search.py#L55-L81) | `select(Ticket)` 实例化全部 ORM 对象；无候选上限 | 改为只 `select` 需要的 5 个列（轻量元组，**语义零变化**）+ 复合索引 + 可配置候选上限 `search_candidate_limit`（默认 2000，安全阀） |
| 3 | `session_scope()` 写路径 | Agent 每单 19–21 次独立事务 commit | 由 WAL + `synchronous=NORMAL` 全局降低提交成本；**先测量再决定**是否做日志批量落库（不预先过度改造） |
| 4 | [ticket_service.py](file:///c:/Users/xhkj/Documents/trae_projects/demo/app/services/ticket_service.py#L54-L65) `list_tickets` | 条件分支重复构造 statement；无 `offset` | 合并为单次查询构造 + 新增 `offset` 分页 |
| 5 | `list_logs` | 无上限，超大工单返回巨量数据 | 新增 `limit`(默认 500) + `offset` |

**缓存（`app/services/cache.py` 新增）** — 极简进程内 TTL 字典缓存，`get/set/invalidate(prefix)`
- 缓存对象：`GET /api/devices`（TTL 30s）、`GET /api/health`（TTL 5s）、`GET /api/tickets*`（TTL 2s）
- **失效集中化**：所有写路径（`create_ticket` / `update_status` / `update_parsed_fields` / 新日志）统一调用 `cache.invalidate("tickets")`，保证正确性可论证

**`app/config.py`（改）** — 新增 `cache_ttl_seconds`、`health_cache_ttl_seconds`、`search_candidate_limit`、`max_page_size`、`default_log_limit`、`expose_error_detail`

---

## 3. 健壮性与契约一致性

**`app/main.py`（改）**
- 新增 `HTTPException` / `RequestValidationError` 异常处理器，统一输出 `{"code","message","detail","data"}`
- **保留 `detail` 字段**——前端 [streamlit_app.py#L31-L48](file:///c:/Users/xhkj/Documents/trae_projects/demo/frontend/streamlit_app.py#L31-L48) 读的就是它，这样既统一契约又零破坏
- 500 兜底处理器停止向客户端回显原始异常文本（改为记日志 + 通用文案，`expose_error_detail=true` 时才回显）
- 新增请求计时中间件：写 `X-Response-Time-Ms` 响应头，并把耗时/状态码记入进程内滚动统计
- 新增 `GET /api/metrics`：返回逐路径 P50/P95/P99、错误率、请求数（**不引入 Prometheus 依赖**，自包含；文档中标注为可选升级项）

**`app/schemas/ticket.py`（改）**
- `description`：`min_length=2, max_length=2000` + validator 去空白并拒绝纯空白
- `user_contact` / `device_id` 加 `max_length`，与 DB 列宽对齐
- 新增分页与异步响应模型

---

## 4. 异步接口（解决 300ms SLA，零破坏）

**`app/api/routes.py`（改）** — 纯新增，同步接口**一行不动**
- `POST /api/tickets/async` → 落库后经 FastAPI `BackgroundTasks` 异步执行 `run_agent`，**立即返回 202** `{ticket_id, status:"处理中", poll_url}`
- `GET /api/tickets/{ticket_id}/status` → `{ticket_id, status, terminal, resolution_mode, elapsed_ms}`
- 新增 `app/services/task_registry.py`：进程内任务状态表（running / done / failed + 起始时间），供 status 接口查询
- 并发安全依赖 §1 的 WAL + `busy_timeout`，后台写不阻塞读

---

## 5. 测试资产

**`tests/`（新增，pytest）**
- `conftest.py`：**在导入任何 `app.*` 之前**设置 `os.environ["DATABASE_URL"]` 指向 `tmp_path` 下的临时库——因为 [config.py](file:///c:/Users/xhkj/Documents/trae_projects/demo/app/config.py#L53-L58) 用 `lru_cache` + 模块级 `settings = get_settings()` 在导入期固化配置，这是必须处理的坑。提供 `client`（`TestClient`）与已播种数据的 fixture
- `test_api_contract.py`：200 契约、字段完整性、统一信封（含 `detail` 兼容）、404/422/500 信封一致
- `test_api_edge.py`：空/超短/超长/纯空白描述、超长 `device_id`、不存在 id、`limit` 越界（0 / -1 / 10000）、`offset` 越界、畸形 JSON、未知 `status`
- `test_api_perf.py`（`@pytest.mark.perf`）：读接口在重复请求下 P95 < 300ms（阈值取自真实测量，避免脆弱断言）
- `pytest.ini`：`testpaths`、`markers = perf`
- `requirements.txt` 增加 `pytest`、`pytest-cov`；原 `batch_ticket_test.py` **保持不动**
- 兼容性核查：确认 `batch_ticket_test.py` 是否传了非枚举 `status` 值，决定 §3 中 `status` 参数是否加枚举校验

**`scripts/api_benchmark.py`（新增）** — 压测与报告数据来源
- `ThreadPoolExecutor` 并发 + `requests.Session`；预热后按并发 C 打 N 次
- 逐接口输出：请求数、错误率、P50/P95/P99/均值、吞吐
- 分场景：读接口并发、`POST /api/tickets/async` 并发、同步 `POST /api/tickets` 单独标注自有 SLA
- 产出 `api_benchmark_result.json` + 控制台表格

---

## 6. 交付文档

**`API文档.md`（新增，根目录）**：概述、Base URL、统一响应信封与错误码表、每个接口的方法/路径/参数表/响应字段表/curl 示例/成功与失败响应示例、分页与缓存规范、**SLA 表（读接口 vs Agent 接口分开承诺）**、变更记录。

**`API测试报告.md`（新增，根目录）**：测试环境与数据规模（标注为合成数据）、功能/边界/异常用例清单与结果、**优化前 vs 优化后逐接口性能对照表**、缺陷清单（问题 → 证据 → 修复）、三项指标的达成情况（对 Agent 接口例外如实说明）、复现命令。

**`README.md`（改）**：§14 文档索引登记上述两份新文档。

---

## 执行顺序

1. 生成基准数据集 → 对**当前未优化代码**跑一次 baseline 压测（拿到「优化前」数据）
2. §1 基座（PRAGMA / 索引 / 引擎）
3. §3 契约与健壮性（异常信封、校验、中间件、metrics）
4. §4 异步接口
5. §2 性能优化（health / 检索 / 分页 / 缓存）
6. 跑第二次压测（拿到「优化后」数据）
7. `tests/` pytest 套件
8. 两份文档 + README 索引

> 保留 baseline，是为了让「优化后」的每个数字都有对照，而不是自说自话。

---

## 验证方式

1. **回归**（不可破坏）：`scripts/init_db.py --reset` → `scripts/demo_test.py` 应 5/5；起后端（mock）→ `batch_ticket_test.py` 应仍为 **26/26 用例、322/322 断言**
2. **新增测试**：`pytest` 全绿（含 `perf` 标记）
3. **性能对照**：`gen_benchmark_data.py` → `api_benchmark.py`，读出优化前后 P95/错误率差异
4. **异步接口手验**：`POST /api/tickets/async` 返回 202 → 轮询 `/status` 直到 `terminal=true`，确认状态与 `resolution_mode` 正确
5. **契约一致性手验**：对不存在的 id 请求详情，确认错误响应同时含 `code`/`message`/`detail` 三个键
6. **指标端点手验**：`GET /api/metrics` 返回逐路径统计

---

## 风险与如实说明

- **300ms 指标范围**：优化后读接口可达标；`POST /api/tickets`（同步跑 Agent）**不可能达标**，报告中按「自有 SLA」单列，并用新异步接口满足提交快路径的 <300ms。这是架构事实，不粉饰。
- **99.9% 准确率 / 0.1% 错误率的定义**：准确率 = 断言通过率 + 字段完整性校验；错误率 = 5xx 占比（压测下实测）。报告中给出明确定义与出处，避免口径含糊。
- **合成数据**：无生产数据源可用，报告中显著标注数据为合成，性能结论仅在该数据规模下成立。
- **测试配置固化坑**：`config.py` 的模块级单例要求 conftest 在导入前设置环境变量，否则测试会打到真实 demo 库——已在 §5 明确处理。
- **缓存正确性**：仅集中式失效 + 短 TTL，不引入分布式缓存；`search_candidate_limit` 是安全阀，默认值足够大以免影响既有行为，报告中说明其语义边界。