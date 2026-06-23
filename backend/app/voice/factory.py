"""Voice Provider 工厂 — Loop 5c/d

根据 settings.tts_provider / settings.stt_provider 选 Mock 或 火山引擎。
TTS 默认包一层 cache(降本),cache backend 由 settings.tts_cache_backend 选:
  - memory: 进程内 LRU(default,单 worker 够用)
  - redis:  多 worker 共享(降本更猛,但要 Redis)
STT 不缓存(每次转写都不同,缓存无意义)。

设计:
- factory 接收 settings,返回的 TTS 一定是 cache 包装(memory 或 redis)
- 火山模式缺凭据 → ValueError(早 fail,比 500 友好)
- STT 不走 cache:base64 音频每次都不同,缓存几乎不命中
"""
from __future__ import annotations

import logging

from app.config import Settings
from app.voice.base import STTProvider, TTSProvider
from app.voice.cache import TTSCache
from app.voice.mock import MockSTTProvider, MockTTSProvider
from app.voice.redis_cache import RedisTTSCache, _create_redis_client
from app.voice.volcengine import VolcengineConfig, VolcengineSTTProvider, VolcengineTTSProvider

logger = logging.getLogger(__name__)


def _build_volcengine_config(settings: Settings) -> VolcengineConfig:
    """从 settings 读火山配置,缺凭据直接抛 ValueError"""
    if not (settings.volc_app_id and settings.volc_access_key and settings.volc_secret_key):
        raise ValueError(
            "火山引擎凭据未配置:需要在 .env 设置 "
            "VOLC_APP_ID / VOLC_ACCESS_KEY / VOLC_SECRET_KEY"
        )
    return VolcengineConfig(
        app_id=settings.volc_app_id,
        access_key=settings.volc_access_key,
        secret_key=settings.volc_secret_key,
        tts_endpoint=settings.volc_tts_endpoint,
        stt_endpoint=settings.volc_stt_endpoint,
        cluster=settings.volc_cluster,
        default_voice=settings.volc_default_voice,
    )


def _build_tts_cache(
    settings: Settings,
    raw: TTSProvider,
) -> TTSProvider:
    """根据 settings.tts_cache_backend 包 cache(memory 或 redis)"""
    if settings.tts_cache_backend == "redis":
        try:
            redis_client = _create_redis_client(settings.redis_url)
        except Exception as e:
            # Redis 不可用 → 降级到 memory cache(只记日志)
            logger.warning(
                "Redis 客户端创建失败,降级到 memory cache: %s", e
            )
            return TTSCache(raw, max_size=settings.tts_cache_max_size)

        logger.info(
            "TTS cache: redis (ttl=%ds, prefix=%s)",
            settings.tts_cache_ttl_seconds, settings.tts_cache_key_prefix,
        )
        return RedisTTSCache(
            raw,
            redis=redis_client,  # type: ignore[arg-type]
            ttl_seconds=settings.tts_cache_ttl_seconds,
            key_prefix=settings.tts_cache_key_prefix,
        )
    else:
        logger.debug(
            "TTS cache: memory (max_size=%d)", settings.tts_cache_max_size
        )
        return TTSCache(raw, max_size=settings.tts_cache_max_size)


def get_tts_provider(settings: Settings) -> TTSProvider:
    """根据 settings.tts_provider 选 TTS 实现,默认包 cache

    Returns:
        TTSCache(MockTTSProvider) 或 RedisTTSCache(VolcengineTTSProvider)

    Raises:
        ValueError: 选了 volcengine 但 .env 缺凭据
    """
    if settings.tts_provider == "volcengine":
        config = _build_volcengine_config(settings)
        logger.info("TTS provider: volcengine (cluster=%s)", config.cluster)
        raw: TTSProvider = VolcengineTTSProvider(config)
    else:
        logger.debug("TTS provider: mock")
        raw = MockTTSProvider()

    return _build_tts_cache(settings, raw)


def get_stt_provider(settings: Settings) -> STTProvider:
    """根据 settings.stt_provider 选 STT 实现(不缓存)

    Returns:
        MockSTTProvider 或 VolcengineSTTProvider
    """
    if settings.stt_provider == "volcengine":
        config = _build_volcengine_config(settings)
        logger.info("STT provider: volcengine (cluster=%s)", config.cluster)
        return VolcengineSTTProvider(config)
    else:
        logger.debug("STT provider: mock")
        return MockSTTProvider()