"""LLM Provider 工厂 — 根据配置返回 Provider 实例"""
from __future__ import annotations

from functools import lru_cache

from app.config import Settings, get_settings
from app.llm.base import LLMProvider
from app.llm.claude_provider import ClaudeProvider


@lru_cache
def get_llm_provider() -> LLMProvider:
    """根据 settings.llm_provider 返回对应 Provider（单例）"""
    settings = get_settings()

    if settings.llm_provider == "claude":
        return ClaudeProvider(
            api_key=settings.anthropic_api_key,
            model=settings.claude_model_main,
            cache_control=settings.cache_control_enabled,
            timeout=settings.llm_timeout_seconds,
            idle_timeout=settings.llm_idle_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )

    if settings.llm_provider == "deepseek":
        # Day 1 先占位，后续 Day 5+ 实现
        raise NotImplementedError("DeepSeek provider 将在后续 Day 实现")

    raise ValueError(f"未知的 LLM provider: {settings.llm_provider}")
