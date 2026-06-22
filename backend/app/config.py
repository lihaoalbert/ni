"""应用配置 — pydantic-settings 加载 .env"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # LLM Provider
    llm_provider: Literal["claude", "deepseek"] = "claude"

    # Anthropic
    anthropic_api_key: str = ""
    claude_model_main: str = "claude-sonnet-4-6"
    claude_model_light: str = "claude-haiku-4-5-20251001"
    cache_control_enabled: bool = True  # Day 4 — 默认开启 prompt caching
    # Day 6 — 稳定性配置
    llm_timeout_seconds: float = 30.0  # 单次 chat 总超时
    llm_idle_timeout_seconds: float = 30.0  # 流式：相邻两个事件的空闲超时
    llm_max_retries: int = 3  # 可重试错误的最大尝试次数

    # Phase 1 — 记忆管道
    memory_pipeline_enabled: bool = False  # True = /chat 触发 HaikuExtractor 后台提取

    # DeepSeek
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    # App
    app_env: Literal["development", "production", "test"] = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/companion_ai"

    # Vector DB
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""

    # Cache
    redis_url: str = "redis://localhost:6379/0"


@lru_cache
def get_settings() -> Settings:
    return Settings()
