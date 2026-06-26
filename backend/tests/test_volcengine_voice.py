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
import base64
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
    """Config 默认值(Loop 10.3: TTS 切到 openspeech V3)"""
    config = VolcengineConfig(
        api_key="test_api_key",
        app_id="test_app",
        access_key="test_ak",
        secret_key="test_sk",
    )
    assert config.tts_endpoint == "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
    assert config.stt_endpoint == "https://openspeech.bytedance.com/api/v1/asr"
    assert config.resource_id == "seed-tts-2.0"
    assert config.default_voice == "saturn_zh_female_cancan_tob"
    assert config.cluster == "volcano_tts"


def test_volcengine_config_custom() -> None:
    """Config 可覆盖默认值"""
    config = VolcengineConfig(
        api_key="ak",
        resource_id="bigtts_2",
        app_id="custom",
        access_key="ak",
        secret_key="sk",
        tts_endpoint="https://custom-tts.example.com/api",
        cluster="my_cluster",
        default_voice="BV001_streaming",
    )
    assert config.tts_endpoint == "https://custom-tts.example.com/api"
    assert config.resource_id == "bigtts_2"
    assert config.default_voice == "BV001_streaming"
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
    """TTS 请求(Loop 10.3: openspeech V3 格式):
    - URL = /api/v3/tts/unidirectional
    - Headers: X-Api-Key, X-Api-Resource-Id
    - Body: { req_params: { text, speaker, audio_params: { format, sample_rate } } }
    - Resp: NDJSON 多行 { code:0, data:"<base64 chunk>" } → provider 拼接解码
    """
    config = VolcengineConfig(
        api_key="my_api_key",
        resource_id="seed-tts-2.0",
        app_id="unused", access_key="unused", secret_key="unused",
    )
    provider = VolcengineTTSProvider(config)

    # mock 火山 V3 NDJSON 响应:两段 base64 音频 + 结束标记
    chunk1 = b"\xff\xfb\x90\x00" + b"\x00" * 50  # fake MP3 frame 1
    chunk2 = b"\x00" * 50  # fake MP3 frame 2
    expected_audio = chunk1 + chunk2
    ndjson_lines = [
        json.dumps({"code": 0, "message": "", "data": base64.b64encode(chunk1).decode()}),
        json.dumps({"code": 0, "message": "", "data": base64.b64encode(chunk2).decode()}),
        json.dumps({"code": 20000000, "message": "OK", "data": None}),
    ]
    resp_body = "\n".join(ndjson_lines)
    with respx.mock(base_url="https://openspeech.bytedance.com") as mock:
        route = mock.post("/api/v3/tts/unidirectional").respond(
            200, content=resp_body.encode(),
            headers={"content-type": "text/plain; charset=utf-8"},
        )

        audio = await provider.synthesize("你好世界")

    # 验证拼接+base64 解码正确
    assert audio == expected_audio
    # 验证请求格式
    assert route.called
    request = route.calls.last.request
    assert request.headers["X-Api-Key"] == "my_api_key"
    assert request.headers["X-Api-Resource-Id"] == "seed-tts-2.0"
    assert "Authorization" not in request.headers  # V3 不再用 Bearer
    body_str = request.content.decode("utf-8")
    body = json.loads(body_str)
    assert body["req_params"]["text"] == "你好世界"
    assert body["req_params"]["speaker"] == "saturn_zh_female_cancan_tob"  # V3 大模型默认
    assert body["req_params"]["audio_params"]["format"] == "mp3"
    assert body["req_params"]["audio_params"]["sample_rate"] == 24000


@pytest.mark.asyncio
async def test_volcengine_tts_uses_specified_voice() -> None:
    """TTS voice_id 参数传到 API(V3 speaker 字段)"""
    config = VolcengineConfig(
        api_key="ak", resource_id="seed-tts-2.0",
        default_voice="saturn_zh_female_cancan_tob",
    )
    provider = VolcengineTTSProvider(config)

    ndjson_lines = [
        json.dumps({"code": 0, "message": "", "data": base64.b64encode(b"\x00" * 50).decode()}),
        json.dumps({"code": 20000000, "message": "OK", "data": None}),
    ]
    with respx.mock(base_url="https://openspeech.bytedance.com") as mock:
        route = mock.post("/api/v3/tts/unidirectional").respond(
            200, content="\n".join(ndjson_lines).encode(),
            headers={"content-type": "text/plain; charset=utf-8"},
        )
        await provider.synthesize("hi", voice_id="zh_female_vv_uranus_bigtss")

    body = json.loads(route.calls.last.request.content)
    assert body["req_params"]["speaker"] == "zh_female_vv_uranus_bigtss"


@pytest.mark.asyncio
async def test_volcengine_tts_handles_network_error() -> None:
    """TTS 网络错误 → 友好异常"""
    config = VolcengineConfig(api_key="ak", resource_id="seed-tts-2.0")
    provider = VolcengineTTSProvider(config)

    with respx.mock(base_url="https://openspeech.bytedance.com") as mock:
        mock.post("/api/v3/tts/unidirectional").mock(side_effect=httpx.ConnectError("network down"))
        with pytest.raises(RuntimeError, match="TTS"):
            await provider.synthesize("test")


@pytest.mark.asyncio
async def test_volcengine_tts_handles_api_error() -> None:
    """TTS API 4xx/5xx → 友好异常"""
    config = VolcengineConfig(api_key="ak", resource_id="seed-tts-2.0")
    provider = VolcengineTTSProvider(config)

    with respx.mock(base_url="https://openspeech.bytedance.com") as mock:
        mock.post("/api/v3/tts/unidirectional").respond(
            401,
            json={"code": 401, "message": "unauthorized"},
        )
        with pytest.raises(RuntimeError, match="TTS"):
            await provider.synthesize("test")


@pytest.mark.asyncio
async def test_volcengine_tts_handles_json_error_code() -> None:
    """V3 NDJSON 响应里有非 0 code chunk → 友好异常(resource 不匹配 / 音色未授权等)"""
    config = VolcengineConfig(api_key="ak", resource_id="seed-tts-2.0")
    provider = VolcengineTTSProvider(config)

    ndjson_lines = [
        json.dumps({"code": 55000000, "message": "resource ID is mismatched with speaker related resource", "data": ""}),
        json.dumps({"code": 20000000, "message": "OK", "data": None}),
    ]
    with respx.mock(base_url="https://openspeech.bytedance.com") as mock:
        mock.post("/api/v3/tts/unidirectional").respond(
            200,
            content="\n".join(ndjson_lines).encode(),
            headers={"content-type": "text/plain; charset=utf-8"},
        )
        with pytest.raises(RuntimeError, match="code=55000000"):
            await provider.synthesize("test")


@pytest.mark.asyncio
async def test_volcengine_tts_missing_api_key_raises() -> None:
    """TTS 没配 api_key → 直接 ValueError(不发请求)"""
    config = VolcengineConfig(api_key="", resource_id="seed-tts-2.0")
    provider = VolcengineTTSProvider(config)

    with pytest.raises(ValueError, match="VOLC_API_KEY"):
        await provider.synthesize("test")


@pytest.mark.asyncio
async def test_volcengine_tts_empty_text_raises() -> None:
    """TTS 空 text 直接抛 ValueError(不发请求)"""
    config = VolcengineConfig(api_key="ak", resource_id="seed-tts-2.0")
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


# ===== TTS Opus 直出 (Loop 10.3: V3 JSON 包 base64,无需 ffmpeg) =====


@pytest.mark.asyncio
async def test_volcengine_tts_opus_direct_from_v3() -> None:
    """TTS format=OPUS:V3 返回 NDJSON+base64(无需本地 MP3→Opus 转换)"""
    config = VolcengineConfig(api_key="ak", resource_id="seed-tts-2.0")
    provider = VolcengineTTSProvider(config)

    # 真 ogg/opus:用 ffmpeg 生成一段 0.3s 静音 OGG/Opus
    if not is_ffmpeg_available():
        pytest.skip("ffmpeg not installed")
    opus_bytes = subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
         "-t", "0.3", "-c:a", "libopus", "-f", "ogg", "-"],
        capture_output=True, check=True,
    ).stdout

    from app.voice.base import AudioFormat

    ndjson_lines = [
        json.dumps({"code": 0, "message": "", "data": base64.b64encode(opus_bytes).decode()}),
        json.dumps({"code": 20000000, "message": "OK", "data": None}),
    ]
    with respx.mock(base_url="https://openspeech.bytedance.com") as mock:
        mock.post("/api/v3/tts/unidirectional").respond(
            200, content="\n".join(ndjson_lines).encode(),
            headers={"content-type": "text/plain; charset=utf-8"},
        )
        audio = await provider.synthesize("hi", format=AudioFormat.OPUS)

    # V3 直出 OGG 容器,客户端可直接喂 AVPlayer
    assert audio[:4] == b"OggS"


@pytest.mark.asyncio
async def test_volcengine_tts_opus_request_format_field() -> None:
    """TTS format=OPUS 时,V3 audio_params.format = ogg_opus"""
    config = VolcengineConfig(api_key="ak", resource_id="seed-tts-2.0")
    provider = VolcengineTTSProvider(config)

    from app.voice.base import AudioFormat

    ndjson_lines = [
        json.dumps({"code": 0, "message": "", "data": base64.b64encode(b"\x00" * 10).decode()}),
        json.dumps({"code": 20000000, "message": "OK", "data": None}),
    ]
    with respx.mock(base_url="https://openspeech.bytedance.com") as mock:
        route = mock.post("/api/v3/tts/unidirectional").respond(
            200, content="\n".join(ndjson_lines).encode(),
            headers={"content-type": "text/plain; charset=utf-8"},
        )
        await provider.synthesize("hi", format=AudioFormat.OPUS)

    body = json.loads(route.calls.last.request.content)
    assert body["req_params"]["audio_params"]["format"] == "ogg_opus"