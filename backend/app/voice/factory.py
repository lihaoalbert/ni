"""Voice Provider 工厂 — Loop 5c

根据 settings.tts_provider / settings.stt_provider 选 Mock 或 火山引擎。
TTS 默认包一层 TTSCache(降本),STT 不缓存(每次转写都不同,缓存无意义)。

设计:
- factory 接收 settings,返回的 TTS 一定是 TTSCache 包装
- 火山模式缺凭据 → ValueError(早 fail,比 500 友好)
- STT 不走 cache:base64 音频每次都不同,缓存几乎不命中
"""
from __future__ import annotations

import logging

from app.config import Settings
from app.voice.base import STTProvider, TTSProvider
from app.voice.cache import TTSCache
from app.voice.mock import MockSTTProvider, MockTTSProvider
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


def get_tts_provider(settings: Settings) -> TTSProvider:
    """根据 settings.tts_provider 选 TTS 实现,默认包 TTSCache

    Returns:
        TTSCache(MockTTSProvider) 或 TTSCache(VolcengineTTSProvider)

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

    return TTSCache(raw, max_size=settings.tts_cache_max_size)


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