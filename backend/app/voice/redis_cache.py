"""Redis TTS 缓存 — Loop 5d

跟 TTSCache (进程内 LRU) 同接口,但 value 存 Redis,多 worker 共享。

设计:
- Key: SHA1(text|voice_id|format) → 40 hex 字符(短 + 唯一 + 跨语言稳定)
- Value: 原始 bytes
- TTL: 默认 7 天(TTS 输出确定性,不会变)
- 注入式 redis client(测试可换 fakeredis,生产用 redis.asyncio)
- Redis 不可用 → 降级直传 provider(只记日志,不报错)
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Protocol

from app.voice.base import AudioFormat
from app.voice.cache import _CacheableTTS

logger = logging.getLogger(__name__)


class _AsyncRedisLike(Protocol):
    """RedisTTSCache 依赖的最小 redis 客户端接口

    redis.asyncio.Redis 满足这个 Protocol;fakeredis 也满足。
    """

    async def get(self, key: str) -> bytes | None: ...
    async def set(self, key: str, value: bytes, ex: int | None = None) -> Any: ...
    async def ttl(self, key: str) -> int: ...
    async def exists(self, key: str) -> int: ...
    async def delete(self, key: str) -> int: ...


def _create_redis_client(url: str) -> _AsyncRedisLike:
    """从 URL 创建一个 async Redis 客户端(测试可 monkeypatch)

    redis.asyncio.from_url 本身是 sync 的(返回 client,不连)。
    """
    from redis.asyncio import from_url  # 延迟导入,测试可不装真 redis

    return from_url(url, decode_responses=False)  # type: ignore[return-value]


class RedisTTSCache:
    """Redis 版本的 TTS 缓存

    跟 TTSCache 行为一致,只是状态在 Redis 里。
    多 uvicorn worker 共用同一 Redis → 命中率更高。
    """

    def __init__(
        self,
        provider: _CacheableTTS,
        redis: _AsyncRedisLike | None = None,
        ttl_seconds: int = 7 * 24 * 3600,
        key_prefix: str = "tts:",
    ) -> None:
        self._provider = provider
        self._redis = redis
        self._ttl = ttl_seconds
        self._prefix = key_prefix
        self._hits = 0
        self._misses = 0

    def _make_key(
        self,
        text: str,
        voice_id: str | None,
        format: AudioFormat | str,
    ) -> str:
        """缓存 key 的 hash 部分(SHA1 hex,40 字符)

        接受 enum 或 str(测试可能传 str)
        """
        fmt_str = format.value if isinstance(format, AudioFormat) else format
        return hashlib.sha1(
            f"{voice_id or ''}|{fmt_str}|{text}".encode("utf-8")
        ).hexdigest()

    def _full_key(
        self,
        text: str,
        voice_id: str | None,
        format: AudioFormat,
    ) -> str:
        """实际存到 Redis 的 key:prefix + sha1"""
        return self._prefix + self._make_key(text, voice_id, format)

    async def synthesize(
        self,
        text: str,
        voice_id: str | None = None,
        format: AudioFormat = AudioFormat.MP3,
    ) -> bytes:
        full_key = self._full_key(text, voice_id, format)

        # 1. 查 Redis
        try:
            if self._redis is not None:
                cached = await self._redis.get(full_key)
                if cached is not None:
                    self._hits += 1
                    logger.debug("Redis TTS cache hit: %s", full_key)
                    return cached
        except Exception as e:
            # Redis 挂了 → 降级直传(只记日志,不报错)
            logger.warning(
                "Redis TTS cache get 失败,降级直传 provider: %s", e
            )

        # 2. miss → 调 provider
        self._misses += 1
        audio = await self._provider.synthesize(text, voice_id, format)

        # 3. 写回 Redis(失败也不影响返回)
        try:
            if self._redis is not None:
                await self._redis.set(full_key, audio, ex=self._ttl)
                logger.debug("Redis TTS cache miss → stored: %s", full_key)
        except Exception as e:
            logger.warning("Redis TTS cache set 失败: %s", e)

        return audio

    def stats(self) -> dict[str, float | int]:
        """缓存命中率统计"""
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "ttl_seconds": self._ttl,
            "key_prefix": self._prefix,
            "hit_rate": round(self._hits / total, 3) if total > 0 else 0.0,
        }