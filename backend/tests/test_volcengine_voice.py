"""火山引擎 TTS / STT Provider 测试 — Loop 5b

覆盖:
1. TTS 请求格式正确(URL, headers, body)
2. STT 请求格式正确
3. TTS 缓存生效(同 text → 只调一次 API)
4. Opus 转换(MP3 → Opus via ffmpeg subprocess)
5. 网络错误 / API 错误 友好异常
6. TTSCache 行为
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from unittest.mock import patch

import httpx
import pytest
import respx

from app.voice.cache import TTSCache
from app.voice.volcengine import (
    VolcengineConfig,
    VolcengineSTTProvider,
    VolcengineTTSProvider,
    convert_mp3_to_opus,
    is_ffmpeg_available,
)


# ===== Config =====


def test_volcengine_config_defaults() -> None:
    """Config 默认值"""
    config = VolcengineConfig(
        app_id="test_app",
        access_key="test_ak",
        secret_key="test_sk",
    )
    assert config.tts_endpoint == "https://openspeech.bytedance.com/api/v1/tts"
    assert config.stt_endpoint == "https://openspeech.bytedance.com/api/v1/asr"
    assert config.cluster == "volcano_tts"
    assert config.default_voice == "zh_female_qingxin"


def test_volcengine_config_custom() -> None:
    """Config 可覆盖默认值"""
    config = VolcengineConfig(
        app_id="custom",
        access_key="ak",
        secret_key="sk",
        tts_endpoint="https://custom-tts.example.com/api",
        cluster="my_cluster",
        default_voice="zh_male_zhongxin",
    )
    assert config.tts_endpoint == "https://custom-tts.example.com/api"
    assert config.cluster == "my_cluster"


# ===== Opus conversion helper =====


def test_ffmpeg_available_check() -> None:
    """is_ffmpeg_available() 不抛错,返回 bool"""
    result = is_ffmpeg_available()
    assert isinstance(result, bool)


def test_convert_mp3_to_opus_no_ffmpeg(monkeypatch) -> None:
    """如果 ffmpeg 不可用,convert 抛 RuntimeError"""
    monkeypatch.setattr(
        "app.voice.volcengine.is_ffmpeg_available", lambda: False
    )
    with pytest.raises(RuntimeError, match="ffmpeg"):
        convert_mp3_to_opus(b"fake_mp3_data")


@pytest.mark.skipif(
    not is_ffmpeg_available(), reason="ffmpeg not installed"
)
def test_convert_mp3_to_opus_real_ffmpeg() -> None:
    """真 ffmpeg 转换:输入 MP3,输出 Opus OGG"""
    # 用 ffmpeg 自己生成一段 0.5s 静音 MP3,再转 Opus
    mp3_bytes = subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
         "-t", "0.5", "-q:a", "9", "-acodec", "libmp3lame", "-f", "mp3", "-"],
        capture_output=True, check=True,
    ).stdout

    opus_bytes = convert_mp3_to_opus(mp3_bytes)

    # Opus 应以 "OggS" 开头(OGG 容器)
    assert opus_bytes[:4] == b"OggS", f"Opus should start with OggS, got {opus_bytes[:20]!r}"
    assert len(opus_bytes) > 0


# ===== TTS Provider =====


@pytest.mark.asyncio
async def test_volcengine_tts_sends_correct_request() -> None:
    """TTS 请求: URL, headers, body 都对"""
    config = VolcengineConfig(
        app_id="my_app",
        access_key="my_ak",
        secret_key="my_sk",
    )
    provider = VolcengineTTSProvider(config)

    # mock 火山 API
    expected_audio = b"\xff\xfb\x90\x00" + b"\x00" * 100  # fake MP3 frame
    with respx.mock(base_url="https://openspeech.bytedance.com") as mock:
        route = mock.post("/api/v1/tts").respond(
            200, content=expected_audio,
            headers={"content-type": "audio/mpeg"},
        )

        audio = await provider.synthesize("你好世界")

    # 验证响应被正确读取
    assert audio == expected_audio
    # 验证请求格式
    assert route.called
    request = route.calls.last.request
    assert "Authorization" in request.headers
    body_str = request.content.decode("utf-8")
    body = json.loads(body_str)
    assert body["app"]["appid"] == "my_app"
    assert body["app"]["cluster"] == "volcano_tts"
    assert body["user"]["uid"] == "default_user"
    assert body["request"]["text"] == "你好世界"


@pytest.mark.asyncio
async def test_volcengine_tts_uses_specified_voice() -> None:
    """TTS voice_id 参数传到 API"""
    config = VolcengineConfig(
        app_id="a", access_key="k", secret_key="k",
        default_voice="zh_female_qingxin",
    )
    provider = VolcengineTTSProvider(config)

    with respx.mock(base_url="https://openspeech.bytedance.com") as mock:
        route = mock.post("/api/v1/tts").respond(
            200, content=b"\x00" * 50,
        )
        await provider.synthesize("hi", voice_id="zh_male_zhongxin")

    body = json.loads(route.calls.last.request.content)
    assert body["request"]["voice_type"] == "zh_male_zhongxin"


@pytest.mark.asyncio
async def test_volcengine_tts_handles_network_error() -> None:
    """TTS 网络错误 → 友好异常"""
    config = VolcengineConfig(app_id="a", access_key="k", secret_key="k")
    provider = VolcengineTTSProvider(config)

    with respx.mock(base_url="https://openspeech.bytedance.com") as mock:
        mock.post("/api/v1/tts").mock(side_effect=httpx.ConnectError("network down"))
        with pytest.raises(RuntimeError, match="TTS"):
            await provider.synthesize("test")


@pytest.mark.asyncio
async def test_volcengine_tts_handles_api_error() -> None:
    """TTS API 4xx/5xx → 友好异常"""
    config = VolcengineConfig(app_id="a", access_key="k", secret_key="k")
    provider = VolcengineTTSProvider(config)

    with respx.mock(base_url="https://openspeech.bytedance.com") as mock:
        mock.post("/api/v1/tts").respond(
            401,
            json={"code": 401, "message": "unauthorized"},
        )
        with pytest.raises(RuntimeError, match="TTS"):
            await provider.synthesize("test")


@pytest.mark.asyncio
async def test_volcengine_tts_empty_text_raises() -> None:
    """TTS 空 text 直接抛 ValueError(不发请求)"""
    config = VolcengineConfig(app_id="a", access_key="k", secret_key="k")
    provider = VolcengineTTSProvider(config)

    with pytest.raises(ValueError):
        await provider.synthesize("")


# ===== STT Provider =====


@pytest.mark.asyncio
async def test_volcengine_stt_sends_correct_request() -> None:
    """STT 请求格式正确"""
    config = VolcengineConfig(app_id="a", access_key="k", secret_key="k")
    provider = VolcengineSTTProvider(config)

    fake_audio = b"\xff\xfb\x90\x00" + b"\x00" * 100
    expected_response = {
        "code": 1000,
        "message": "success",
        "result": {
            "text": "你好世界",
        },
    }

    with respx.mock(base_url="https://openspeech.bytedance.com") as mock:
        route = mock.post("/api/v1/asr").respond(200, json=expected_response)
        text = await provider.transcribe(fake_audio)

    assert text == "你好世界"
    assert route.called


@pytest.mark.asyncio
async def test_volcengine_stt_handles_empty_result() -> None:
    """STT 返回空 result → 友好处理"""
    config = VolcengineConfig(app_id="a", access_key="k", secret_key="k")
    provider = VolcengineSTTProvider(config)

    with respx.mock(base_url="https://openspeech.bytedance.com") as mock:
        mock.post("/api/v1/asr").respond(
            200, json={"code": 1000, "message": "success", "result": {}}
        )
        text = await provider.transcribe(b"\x00" * 100)

    assert text == ""


@pytest.mark.asyncio
async def test_volcengine_stt_handles_api_error() -> None:
    """STT API 错误 → 友好异常"""
    config = VolcengineConfig(app_id="a", access_key="k", secret_key="k")
    provider = VolcengineSTTProvider(config)

    with respx.mock(base_url="https://openspeech.bytedance.com") as mock:
        mock.post("/api/v1/asr").respond(
            500, json={"code": 500, "message": "internal error"}
        )
        with pytest.raises(RuntimeError, match="STT"):
            await provider.transcribe(b"\x00" * 100)


@pytest.mark.asyncio
async def test_volcengine_stt_empty_audio_raises() -> None:
    """STT 空 audio 直接抛 ValueError"""
    config = VolcengineConfig(app_id="a", access_key="k", secret_key="k")
    provider = VolcengineSTTProvider(config)
    with pytest.raises(ValueError):
        await provider.transcribe(b"")


# ===== TTS Cache =====


@pytest.mark.asyncio
async def test_tts_cache_hits() -> None:
    """TTSCache: 第二次同 key → 返回缓存,不再调 API"""
    call_count = 0

    class CountingProvider:
        async def synthesize(self, text, voice_id=None, format=None) -> bytes:
            nonlocal call_count
            call_count += 1
            return f"audio_{text}".encode()

    provider = CountingProvider()
    cache = TTSCache(provider, max_size=10)

    a1 = await cache.synthesize("hello")
    a2 = await cache.synthesize("hello")
    a3 = await cache.synthesize("hello")

    # 只调一次
    assert call_count == 1
    assert a1 == a2 == a3 == b"audio_hello"


@pytest.mark.asyncio
async def test_tts_cache_different_keys_miss() -> None:
    """不同 text/voice/format → 不同 key,各自调一次"""
    call_count = 0

    class CountingProvider:
        async def synthesize(self, text, voice_id=None, format=None) -> bytes:
            nonlocal call_count
            call_count += 1
            return f"audio_{text}_{voice_id}".encode()

    cache = TTSCache(CountingProvider())

    await cache.synthesize("a")
    await cache.synthesize("b")
    await cache.synthesize("a", voice_id="v2")
    await cache.synthesize("a", voice_id="v2")  # cache hit

    assert call_count == 3


@pytest.mark.asyncio
async def test_tts_cache_evicts_lru() -> None:
    """TTSCache: max_size 限制 + LRU 淘汰"""
    class SimpleProvider:
        async def synthesize(self, text, voice_id=None, format=None) -> bytes:
            return f"audio_{text}".encode()

    cache = TTSCache(SimpleProvider(), max_size=2)

    await cache.synthesize("a")
    await cache.synthesize("b")
    await cache.synthesize("c")  # 触发淘汰,a 被踢

    assert cache.size() == 2


@pytest.mark.asyncio
async def test_tts_cache_provider_failure_not_cached() -> None:
    """Provider 抛错时,缓存不写入(下次还能重试)"""
    class FailingProvider:
        call_count = 0

        async def synthesize(self, text, voice_id=None, format=None) -> bytes:
            FailingProvider.call_count += 1
            if FailingProvider.call_count == 1:
                raise RuntimeError("first call fails")
            return b"success"

    provider = FailingProvider()
    cache = TTSCache(provider)

    # 第一次失败
    with pytest.raises(RuntimeError):
        await cache.synthesize("hello")

    # 第二次成功(没缓存)
    result = await cache.synthesize("hello")
    assert result == b"success"


# ===== TTS Opus conversion integration =====


@pytest.mark.asyncio
async def test_volcengine_tts_returns_opus_via_ffmpeg(monkeypatch) -> None:
    """TTS 合成 Opus:API 拿 MP3 + 本地转 Opus"""
    config = VolcengineConfig(app_id="a", access_key="k", secret_key="k")
    provider = VolcengineTTSProvider(config)

    # 生成真 MP3
    if not is_ffmpeg_available():
        pytest.skip("ffmpeg not installed")
    mp3_bytes = subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
         "-t", "0.3", "-q:a", "9", "-acodec", "libmp3lame", "-f", "mp3", "-"],
        capture_output=True, check=True,
    ).stdout

    from app.voice.base import AudioFormat

    with respx.mock(base_url="https://openspeech.bytedance.com") as mock:
        mock.post("/api/v1/tts").respond(200, content=mp3_bytes)
        audio = await provider.synthesize("hi", format=AudioFormat.OPUS)

    # 转换后应是 OGG 容器
    assert audio[:4] == b"OggS"