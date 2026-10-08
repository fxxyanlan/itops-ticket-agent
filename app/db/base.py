"""数据库引擎与会话管理（SQLAlchemy 2.x + SQLite）。

性能与并发要点：
- SQLite 开启 WAL：读不阻塞写、写不阻塞读，显著改善后台 Agent 写日志时的并发表现；
- `synchronous=NORMAL`：WAL 下兼顾安全与提交速度（默认 FULL 每次提交都 fsync）；
- `busy_timeout`：写锁竞争时自动等待重试，避免直接抛 database is locked；
- 连接池显式调参 + `pool_pre_ping`：复用连接、剔除失效连接，降低每请求建连开销。
"""

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import DATA_DIR, settings

_IS_SQLITE = settings.database_url.startswith("sqlite")

# SQLite 需要保证多线程（FastAPI / Streamlit 场景）可用
_connect_args = {"check_same_thread": False} if _IS_SQLITE else {}
_engine_kwargs: dict = {}
if _IS_SQLITE:
    _engine_kwargs = {
        "pool_pre_ping": True,
        "pool_size": 10,
        "max_overflow": 20,
        "pool_recycle": 1800,
    }

DATA_DIR.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    future=True,
    **_engine_kwargs,
)

if _IS_SQLITE:

    @event.listens_for(engine, "connect")
    def _apply_sqlite_pragma(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        """每条新连接建立时应用 SQLite 调优参数。"""
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


def init_db() -> None:
    """建表（不存在时创建）。"""
    from app.db import models  # noqa: F401  确保模型已注册到 metadata

    Base.metadata.create_all(engine)


def probe_db() -> None:
    """轻量探活：只做一次 `SELECT 1`，不触碰 metadata。

    健康检查属于高频接口，不应每次调用都执行 `create_all()`（那会反复查询
    sqlite_master）。探活失败时抛异常，由调用方决定降级策略。
    """
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


@contextmanager
def session_scope() -> Iterator[Session]:
    """事务性会话上下文：正常提交，异常回滚。"""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI 依赖注入用的会话生成器。"""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()