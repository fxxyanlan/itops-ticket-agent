"""数据库引擎与会话管理（SQLAlchemy 2.x + SQLite）。"""

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import DATA_DIR, settings

# SQLite 需要保证多线程（FastAPI / Streamlit 场景）可用
_connect_args = (
    {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
)

DATA_DIR.mkdir(parents=True, exist_ok=True)

engine = create_engine(settings.database_url, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


def init_db() -> None:
    """建表（不存在时创建）。"""
    from app.db import models  # noqa: F401  确保模型已注册到 metadata

    Base.metadata.create_all(engine)


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