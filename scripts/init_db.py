"""数据库初始化脚本：建表 + 写入种子数据。

用法：
    py scripts/init_db.py          # 幂等，可重复执行
    py scripts/init_db.py --reset  # 清空并重建（演示前重置用）
"""

import sys
from pathlib import Path

# 把项目根目录加入 sys.path，保证直接运行时能 import app.*
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.base import Base, engine, init_db, session_scope  # noqa: E402
from app.db.seed import seed_all  # noqa: E402
from app.logging_config import get_logger  # noqa: E402

logger = get_logger("itops.init_db")


def main() -> None:
    reset = "--reset" in sys.argv
    if reset:
        logger.warning("--reset：正在删除所有表并重建")
        Base.metadata.drop_all(engine)

    init_db()
    logger.info("数据库表已就绪")

    with session_scope() as session:
        message = seed_all(session, force=reset)
    logger.info(message)


if __name__ == "__main__":
    main()