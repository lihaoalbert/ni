"""Redis TTS 缓存测试 — Loop 5d

为什么需要:
- 进程内 LRU 跨 uvicorn worker 不共享 → 多 worker 命中率低
- Redis 是多 worker 共享的事实标准
- 同样 key 在任一 worker 命中,降本更猛

设计:
- RedisTTSCache 与 TTSCache 同接口(都实现 synthesize)
- Key: SHA1(text|voice_id|format) → 40 hex 字符(短 + 唯一)
- Value: 原始 bytes,SET ... EX <ttl>
- TTL 默认 7 天(TTS 输出确定性,不会变)
- Redis 不可用 → 降级直传 provider(不报错,只记日志)
- 注入式 redis client(测试用 fakeredis,生产用 redis.asyncio)
"""
from __future__ import annotations

import hashlib

import fakeredis.aioredis
import pytest

from app.config import Settings
from app.voice.cache import TTSCache
from app.voice.factory import get_tts_provider
from app.voice.mock import MockTTSProvider
from app.voice.redis_cache import RedisTTSCache


def _make_fake_redis() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis(decode_responses=False)


# ===== Key derivation =====


def test_redis_cache_key_is_sha1_hex() -> None:
    """key = sha1(text|voice_id|format).hexdigest()"""
    cache = RedisTTSCache(MockTTSProvider(), _make_fake_redis())
    key = cache._make_key("你好", "voice_a", "mp3")
    assert len(key) == 40
    assert all(c in "0123456789abcdef" for c in key)
    # 同样输入 → 同样 key
    key2 = cache._make_key("你好", "voice_a", "mp3")
    assert key == key2


def test_redis_cache_key_different_for_different_inputs() -> None:
    """text / voice / format 任一不同 → key 不同"""
    cache = RedisTTSCache(MockTTSProvider(), _make_fake_redis())
    k1 = cache._make_key("a", None, "mp3")
    k2 = cache._make_key("b", None, "mp3")
    k3 = cache._make_key("a", "v1", "mp3")
    k4 = cache._make_key("a", None, "wav")
    assert len({k1, k2, k3, k4}) == 4


def test_redis_cache_key_matches_expected_sha1() -> None:
    """key 跟标准 sha1 对齐(防未来手抖改 hash 算法)"""
    cache = RedisTTSCache(MockTTSProvider(), _make_fake_redis())
    text = "你好世界"
    voice = "voice_x"
    fmt = "opus"
    expected = hashlib.sha1(
        f"{voice}|{fmt}|{text}".encode("utf-8")
    ).hexdigest()
    assert cache._make_key(text, voice, fmt) == expected


def test_redis_cache_key_has_prefix() -> None:
    """实际存到 Redis 的 key 带前缀(防命名空间冲突)"""
    cache = RedisTTSCache(
        MockTTSProvider(), _make_fake_redis(), key_prefix="myapp:tts:"
    )
    full_key = cache._full_key("hello", "v1", "mp3")
    assert full_key.startswith("myapp:tts:")
    assert len(full_key) > len("myapp:tts:")


# ===== Synthesize flow =====


@pytest.mark.asyncio
async def test_redis_cache_miss_calls_provider() -> None:
    """首次:调 provider,写入 Redis"""
    call_count = 0

    class CountingProvider:
        async def synthesize(self, text, voice_id=None, format=None) -> bytes:
            nonlocal call_count
            call_count += 1
            return f"audio_{text}".encode()

    redis = _make_fake_redis()
    cache = RedisTTSCache(CountingProvider(), redis)

    a = await cache.synthesize("hello")

    assert a == b"audio_hello"
    assert call_count == 1
    # 确认写入了 Redis
    assert await cache._redis.exists(cache._full_key("hello", None, "mp3")) == 1


@pytest.mark.asyncio
async def test_redis_cache_hit_skips_provider() -> None:
    """二次同 key:走 Redis,不再调 provider"""
    call_count = 0

    class CountingProvider:
        async def synthesize(self, text, voice_id=None, format=None) -> bytes:
            nonlocal call_count
            call_count += 1
            return f"audio_{text}".encode()

    redis = _make_fake_redis()
    cache = RedisTTSCache(CountingProvider(), redis)

    a1 = await cache.synthesize("hello")
    a2 = await cache.synthesize("hello")
    a3 = await cache.synthesize("hello")

    assert a1 == a2 == a3 == b"audio_hello"
    assert call_count == 1


@pytest.mark.asyncio
async def test_redis_cache_different_texts_each_miss() -> None:
    """不同 text 各自调一次 provider"""
    call_count = 0

    class CountingProvider:
        async def synthesize(self, text, voice_id=None, format=None) -> bytes:
            nonlocal call_count
            call_count += 1
            return f"audio_{text}".encode()

    cache = RedisTTSCache(CountingProvider(), _make_fake_redis())

    await cache.synthesize("a")
    await cache.synthesize("b")
    await cache.synthesize("c")
    await cache.synthesize("a")  # cache hit

    assert call_count == 3


@pytest.mark.asyncio
async def test_redis_cache_provider_failure_not_cached() -> None:
    """Provider 抛错 → 不写 Redis(下次能重试)"""
    class FailingProvider:
        call_count = 0

        async def synthesize(self, text, voice_id=None, format=None) -> bytes:
            FailingProvider.call_count += 1
            if FailingProvider.call_count == 1:
                raise RuntimeError("first call fails")
            return b"success"

    redis = _make_fake_redis()
    cache = RedisTTSCache(FailingProvider(), redis)

    with pytest.raises(RuntimeError):
        await cache.synthesize("hello")

    # 第二次成功(没缓存)
    result = await cache.synthesize("hello")
    assert result == b"success"
    # 确认写入了
    assert FailingProvider.call_count == 2


# ===== TTL =====


@pytest.mark.asyncio
async def test_redis_cache_respects_ttl() -> None:
    """写入时设 EX,Redis 实际 key 有 TTL"""
    redis = _make_fake_redis()
    cache = RedisTTSCache(
        MockTTSProvider(), redis, ttl_seconds=60
    )
    await cache.synthesize("hi")
    full_key = cache._full_key("hi", None, "mp3")
    ttl = await redis.ttl(full_key)
    # ttl 应在 (0, 60] 范围
    assert 0 < ttl <= 60


@pytest.mark.asyncio
async def test_redis_cache_expired_key_triggers_provider() -> None:
    """key 过期后 → 再次调 provider"""
    call_count = 0

    class CountingProvider:
        async def synthesize(self, text, voice_id=None, format=None) -> bytes:
            nonlocal call_count
            call_count += 1
            return f"audio_{text}".encode()

    redis = _make_fake_redis()
    cache = RedisTTSCache(CountingProvider(), redis, ttl_seconds=1)

    await cache.synthesize("hi")
    assert call_count == 1

    # 手动让 key 过期(fakeredis 不支持真等 1 秒,直接删)
    await redis.delete(cache._full_key("hi", None, "mp3"))

    await cache.synthesize("hi")
    assert call_count == 2


# ===== 多 client 共享(模拟多 worker)=====


@pytest.mark.asyncio
async def test_redis_cache_shared_across_clients() -> None:
    """两个 cache 实例共用同一 Redis → 互相能命中"""
    shared_redis = _make_fake_redis()

    class CountingProvider:
        def __init__(self, name):
            self.name = name
            self.call_count = 0

        async def synthesize(self, text, voice_id=None, format=None) -> bytes:
            self.call_count += 1
            return f"{self.name}_audio_{text}".encode()

    p1 = CountingProvider("worker1")
    p2 = CountingProvider("worker2")

    cache1 = RedisTTSCache(p1, shared_redis)
    cache2 = RedisTTSCache(p2, shared_redis)

    # worker1 先调
    a1 = await cache1.synthesize("hello")
    # worker2 同 key 应命中(不调 provider)
    a2 = await cache2.synthesize("hello")

    assert a1 == a2 == b"worker1_audio_hello"
    assert p1.call_count == 1
    assert p2.call_count == 0  # 没调过


# ===== 容错:Redis 挂了 =====


@pytest.mark.asyncio
async def test_redis_cache_falls_back_when_redis_down() -> None:
    """Redis 不可用 → 降级直传 provider,不报错"""
    call_count = 0

    class CountingProvider:
        async def synthesize(self, text, voice_id=None, format=None) -> bytes:
            nonlocal call_count
            call_count += 1
            return f"audio_{text}".encode()

    # 模拟 Redis 永远抛错
    class BrokenRedis:
        async def get(self, *args, **kwargs):
            raise ConnectionError("redis is down")

        async def set(self, *args, **kwargs):
            raise ConnectionError("redis is down")

        async def ttl(self, *args, **kwargs):
            raise ConnectionError("redis is down")

        async def exists(self, *args, **kwargs):
            raise ConnectionError("redis is down")

        async def delete(self, *args, **kwargs):
            raise ConnectionError("redis is down")

    cache = RedisTTSCache(CountingProvider(), BrokenRedis())  # type: ignore[arg-type]

    # 不应抛错,直接调 provider
    a = await cache.synthesize("hello")
    assert a == b"audio_hello"
    assert call_count == 1

    # 第二次依然直传(cache 失效,但不报错)
    a2 = await cache.synthesize("hello")
    assert a2 == b"audio_hello"
    assert call_count == 2


# ===== Stats =====


@pytest.mark.asyncio
async def test_redis_cache_stats() -> None:
    """stats() 返回 hits / misses / 命中率"""
    cache = RedisTTSCache(MockTTSProvider(), _make_fake_redis())

    await cache.synthesize("a")  # miss
    await cache.synthesize("a")  # hit
    await cache.synthesize("b")  # miss
    await cache.synthesize("a")  # hit

    stats = cache.stats()
    assert stats["hits"] == 2
    assert stats["misses"] == 2
    assert stats["hit_rate"] == 0.5


# ===== Factory 切换 =====


def test_factory_uses_memory_cache_by_default() -> None:
    """默认 tts_cache_backend='memory' → TTSCache (进程内 LRU)"""
    settings = Settings(tts_provider="mock")  # default tts_cache_backend='memory'
    provider = get_tts_provider(settings)
    assert isinstance(provider, TTSCache)
    assert not isinstance(provider, RedisTTSCache)


def test_factory_uses_redis_cache_when_backend_redis(monkeypatch) -> None:
    """tts_cache_backend='redis' → RedisTTSCache

    不连真 Redis(测试用 fakeredis 替换 client 工厂)
    """
    fake_redis = _make_fake_redis()
    monkeypatch.setattr(
        "app.voice.redis_cache._create_redis_client",
        lambda url: fake_redis,
    )
    settings = Settings(
        tts_provider="mock",
        tts_cache_backend="redis",
        redis_url="redis://localhost:6379/0",
    )
    provider = get_tts_provider(settings)
    assert isinstance(provider, RedisTTSCache)
    assert isinstance(provider._provider, MockTTSProvider)