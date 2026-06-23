"""TTS 缓存 — Loop 5b

为什么需要:
- TTS 是字符人聊天最贵的外部调用(每轮都调)
- 同一段话经常复现("你好"、"再见"、固定开场白)
- 缓存可降 50%+ 成本(Phase 2 plan 估算)

设计:
- 进程内 dict 缓存(Loop 5b 范围)
- Loop 5c API 层接 LRU 淘汰 + Redis(可选)
- Key = (text, voice_id, format)
- Value = bytes
- Provider 抛错时不写缓存(下次还能重试)
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Protocol

from app.voice.base import AudioFormat

logger = logging.getLogger(__name__)


class _CacheableTTS(Protocol):
    """TTSCache 依赖的最小接口 — 不强制实现 TTSProvider 完整协议"""

    async def synthesize(
        self,
        text: str,
        voice_id: str | None = None,
        format: AudioFormat = AudioFormat.MP3,
    ) -> bytes: ...


class TTSCache:
    """TTS 缓存包装器

    用法:
        provider = VolcengineTTSProvider(config)
        cached = TTSCache(provider, max_size=128)

        # cached.synthesize(...) 走缓存
        # provider.synthesize(...) 永远调 API

    LRU: 用 OrderedDict 实现,max_size 满了踢最久没用。
    """

    def __init__(self, provider: _CacheableTTS, max_size: int = 128) -> None:
        self._provider = provider
        self._max_size = max_size
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self._hits = 0
        self._misses = 0

    @staticmethod
    def _make_key(
        text: str,
        voice_id: str | None,
        format: AudioFormat,
    ) -> str:
        """缓存 key: 三元组决定唯一性"""
        return f"{voice_id or ''}|{format.value}|{text}"

    async def synthesize(
        self,
        text: str,
        voice_id: str | None = None,
        format: AudioFormat = AudioFormat.MP3,
    ) -> bytes:
        key = self._make_key(text, voice_id, format)

        if key in self._cache:
            # LRU: 移到末尾(标记最近使用)
            self._cache.move_to_end(key)
            self._hits += 1
            logger.debug("TTS cache hit: %r", key[:50])
            return self._cache[key]

        self._misses += 1
        audio = await self._provider.synthesize(text, voice_id, format)

        # Provider 抛错时 audio 永远不会到这里(异常已向上传)
        # 所以这里不用 try/except

        # 写缓存前先检查 size
        if len(self._cache) >= self._max_size:
            # 淘汰最久没用(OrderedDict 头)
            self._cache.popitem(last=False)

        self._cache[key] = audio
        logger.debug("TTS cache miss → stored: %r", key[:50])
        return audio

    def size(self) -> int:
        return len(self._cache)

    def clear(self) -> None:
        self._cache.clear()

    def stats(self) -> dict[str, int]:
        """缓存命中率统计"""
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "size": self.size(),
            "hit_rate": round(self._hits / total, 3) if total > 0 else 0.0,
        }