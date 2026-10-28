"""pytest 全局夹具：隔离测试库 + 进程内 TestClient。

关键点
    `app.config` 在导入期即用 `lru_cache` 固化了 `settings`，因此必须在
    导入任何 `app.*` 模块之前设置好环境变量，否则会连到演示库 `data/itops.db`。
    本文件首屏就完成环境变量注入，保证测试只作用于 `data/test_api.db`。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# --- 必须在 import app.* 之前完成 ---
TEST_DB = ROOT / "data" / "test_api.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["LLM_MODE"] = "mock"  # 离线可复现，不依赖真实 API Key
os.environ["CROSS_CHECK_ENABLED"] = "false"  # 测试不调用第二个模型，避免真实网络请求
# -----------------------------------

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db.base import Base, engine, init_db, session_scope  # noqa: E402
from app.db.seed import seed_all  # noqa: E402
from app.main import app  # noqa: E402
from app.services import cache, metrics, task_registry  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def prepare_database():
    """会话级：重建测试库并写入种子数据（历史工单 / 设备 / 工程师）。"""
    Base.metadata.drop_all(engine)
    init_db()
    with session_scope() as session:
        seed_all(session, force=True)
    yield


@pytest.fixture()
def client():
    """函数级客户端：清空进程内缓存、指标与任务登记，避免用例间互相污染。"""
    cache.clear()
    metrics.clear()
    task_registry.clear()
    with TestClient(app) as test_client:
        yield test_client