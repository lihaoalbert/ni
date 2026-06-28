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
    anthropic_base_url: str = ""  # 默认走 api.anthropic.com;改 MiniMax / 其他代理时填
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
    # Phase 2 Loop 4c — Memory Backend 开关
    #   inmemory: 进程内 dict,无外部依赖(默认,CI/单测/本地)
    #   qdrant:   向量库,需配 qdrant_url + embedding
    memory_backend: Literal["inmemory", "qdrant"] = "inmemory"
    qdrant_collection: str = "memory_facts"

    # Phase 2 Loop 4b — Embedding 配置
    embedding_model: str = "BAAI/bge-small-zh-v1.5"  # sentence-transformers 模型
    embedding_device: str = "cpu"  # cpu / mps(Mac GPU) / cuda

    # Cache
    redis_url: str = "redis://localhost:6379/0"

    # Phase 2 Loop 5 — 语音 Provider 开关
    #   mock: 进程内 Mock,无外部依赖(默认,CI/单测/本地开发)
    #   volcengine: 火山引擎真实 API
    tts_provider: Literal["mock", "volcengine"] = "mock"
    stt_provider: Literal["mock", "volcengine"] = "mock"

    # 火山引擎语音配置
    # TTS 用新版 openspeech V3(API Key + resource_id 模型选择器)
    # STT 仍用旧版 openspeech V1(app_id + access_key + secret_key)
    volc_app_id: str = ""
    volc_access_key: str = ""
    volc_secret_key: str = ""
    volc_api_key: str = ""
    volc_resource_id: str = "seed-tts-2.0"  # X-Api-Resource-Id,模型版本选择器
    volc_tts_endpoint: str = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
    volc_stt_endpoint: str = "https://openspeech.bytedance.com/api/v1/asr"
    volc_default_voice: str = "saturn_zh_female_cancan_tob"  # 知性灿灿(V3 大模型配对音色)
    volc_cluster: str = "volcano_tts"  # STT V1 body 字段
    # TTS 缓存配置
    # backend: memory (进程内 LRU,默认) | redis (多 worker 共享)
    tts_cache_backend: Literal["memory", "redis"] = "memory"
    tts_cache_max_size: int = 128  # memory backend 用
    tts_cache_ttl_seconds: int = 7 * 24 * 3600  # 7 天,redis backend 用
    tts_cache_key_prefix: str = "tts:"


@lru_cache
def get_settings() -> Settings:
    return Settings()
