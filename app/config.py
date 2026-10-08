"""全局配置：从 .env / 环境变量加载，供各层复用。"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（app/ 的上一级）
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"


class Settings(BaseSettings):
    """应用配置项，字段名与 .env 中的大写变量一一对应（不区分大小写）。"""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM ---
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    llm_mode: str = "auto"  # auto | real | mock
    llm_timeout: int = 60

    # --- 数据库 ---
    database_url: str = f"sqlite:///{(DATA_DIR / 'itops.db').as_posix()}"

    # --- 业务参数 ---
    match_threshold: float = 0.42
    max_iterations: int = 6
    max_review_rounds: int = 1  # 审核 Agent 最多触发的「退回重做」次数

    # --- API 性能与契约 ---
    cache_ttl_seconds: int = 30  # 只读资源（设备列表等）缓存有效期
    health_cache_ttl_seconds: int = 5  # 健康检查数据库探活结果缓存有效期
    search_candidate_limit: int = 2000  # 单次历史工单检索的最大候选数（安全阀）
    max_page_size: int = 500  # 列表接口单页最大条数
    default_log_limit: int = 500  # 日志接口默认返回条数
    expose_error_detail: bool = False  # 是否向客户端回显原始异常文本（生产应关闭）

    # --- 日志 ---
    log_level: str = "INFO"

    # --- 前端 ---
    backend_url: str = "http://127.0.0.1:8000"

    @property
    def effective_llm_mode(self) -> str:
        """把 llm_mode 归一化为 real / mock：auto 时按是否配置了 Key 决定。"""
        mode = (self.llm_mode or "auto").strip().lower()
        if mode in ("real", "mock"):
            return mode
        return "real" if self.deepseek_api_key else "mock"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """单例配置，避免重复读取 .env。"""
    return Settings()


settings = get_settings()