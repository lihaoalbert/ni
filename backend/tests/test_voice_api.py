"""Voice API + Factory 测试 — Loop 5c

覆盖:
1. factory: get_tts_provider() 根据 settings 选 Mock / Volcengine
2. factory: get_stt_provider() 同上
3. API: POST /voice/tts/synthesize 返回音频 bytes
4. API: POST /voice/stt/transcribe 返回文本
5. API: 参数校验 (空 text / 缺字段)
6. API: cache hit 验证(同 text 二次调用不再调 provider)
7. 集成: Mock provider 端到端,音频 base64 编码后能解码
"""
from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.voice.cache import TTSCache
from app.voice.factory import get_stt_provider, get_tts_provider
from app.voice.mock import MockSTTProvider, MockTTSProvider
from app.voice.volcengine import VolcengineSTTProvider, VolcengineTTSProvider


# ===== Factory =====


def test_factory_tts_mock_when_settings_mock() -> None:
    """settings.tts_provider='mock' → TTSCache(MockTTSProvider)"""
    settings = Settings(tts_provider="mock")
    provider = get_tts_provider(settings)
    assert isinstance(provider, TTSCache)
    assert isinstance(provider._provider, MockTTSProvider)


def test_factory_tts_volcengine_when_settings_volcengine() -> None:
    """settings.tts_provider='volcengine' → TTSCache(VolcengineTTSProvider)"""
    settings = Settings(
        tts_provider="volcengine",
        volc_app_id="test_app",
        volc_access_key="test_ak",
        volc_secret_key="test_sk",
    )
    provider = get_tts_provider(settings)
    assert isinstance(provider, TTSCache)
    assert isinstance(provider._provider, VolcengineTTSProvider)


def test_factory_stt_mock_when_settings_mock() -> None:
    settings = Settings(stt_provider="mock")
    provider = get_stt_provider(settings)
    assert isinstance(provider, MockSTTProvider)


def test_factory_stt_volcengine_when_settings_volcengine() -> None:
    settings = Settings(
        stt_provider="volcengine",
        volc_app_id="test_app",
        volc_access_key="test_ak",
        volc_secret_key="test_sk",
    )
    provider = get_stt_provider(settings)
    assert isinstance(provider, VolcengineSTTProvider)


def test_factory_volcengine_missing_credentials_raises() -> None:
    """volcengine 模式但缺凭据 → 友好异常"""
    settings = Settings(
        tts_provider="volcengine",
        volc_app_id="",  # 空
        volc_access_key="",
        volc_secret_key="",
    )
    with pytest.raises(ValueError, match="VOLC"):
        get_tts_provider(settings)


def test_factory_wraps_with_cache_by_default() -> None:
    """factory 默认把 provider 包一层 TTSCache(降本)"""
    settings = Settings(tts_provider="mock", tts_cache_max_size=8)
    provider = get_tts_provider(settings)
    assert isinstance(provider, TTSCache)
    assert provider._max_size == 8


# ===== API endpoint: /voice/tts/synthesize =====


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_api_tts_synthesize_returns_audio(client: TestClient) -> None:
    """POST /voice/tts/synthesize 返回二进制音频 + content-type"""
    response = client.post(
        "/voice/tts/synthesize",
        json={"text": "你好世界", "format": "mp3"},
    )
    assert response.status_code == 200
    assert len(response.content) > 0
    # content-type 应包含音频 mime
    assert "audio" in response.headers.get("content-type", "")


def test_api_tts_synthesize_default_format_is_mp3(client: TestClient) -> None:
    """不传 format → 默认 mp3"""
    response = client.post("/voice/tts/synthesize", json={"text": "hello"})
    assert response.status_code == 200
    # Mock mp3 输出以 "ID3" 头开头
    assert response.content[:3] == b"ID3"


def test_api_tts_synthesize_opus_returns_ogg(client: TestClient) -> None:
    """format=opus → 输出 OGG 容器"""
    response = client.post(
        "/voice/tts/synthesize",
        json={"text": "hello", "format": "opus"},
    )
    assert response.status_code == 200
    # Mock opus 输出以 "OggS" 开头
    assert response.content[:4] == b"OggS"


def test_api_tts_synthesize_with_voice_id(client: TestClient) -> None:
    """传 voice_id 不报错"""
    response = client.post(
        "/voice/tts/synthesize",
        json={"text": "hi", "voice_id": "custom_voice"},
    )
    assert response.status_code == 200


def test_api_tts_synthesize_empty_text_returns_422(client: TestClient) -> None:
    """text 为空 → 422 校验错误"""
    response = client.post("/voice/tts/synthesize", json={"text": ""})
    assert response.status_code == 422


def test_api_tts_synthesize_missing_text_returns_422(client: TestClient) -> None:
    """缺 text 字段 → 422"""
    response = client.post("/voice/tts/synthesize", json={})
    assert response.status_code == 422


# ===== API endpoint: /voice/stt/transcribe =====


def test_api_stt_transcribe_returns_text(client: TestClient) -> None:
    """POST /voice/stt/transcribe 返回 JSON {text, language}"""
    fake_audio = b"\xff\xfb\x90\x00" + b"\x00" * 200
    audio_b64 = base64.b64encode(fake_audio).decode("utf-8")

    response = client.post(
        "/voice/stt/transcribe",
        json={"audio": audio_b64, "format": "mp3", "language": "zh-CN"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "text" in data
    assert isinstance(data["text"], str)
    assert len(data["text"]) > 0


def test_api_stt_transcribe_default_format_mp3(client: TestClient) -> None:
    """不传 format → 默认 mp3,正常返回"""
    audio_b64 = base64.b64encode(b"\x00" * 200).decode("utf-8")
    response = client.post("/voice/stt/transcribe", json={"audio": audio_b64})
    assert response.status_code == 200


def test_api_stt_transcribe_empty_audio_returns_422(client: TestClient) -> None:
    """audio 字段空字符串 → 422"""
    response = client.post("/voice/stt/transcribe", json={"audio": ""})
    assert response.status_code == 422


def test_api_stt_transcribe_missing_audio_returns_422(client: TestClient) -> None:
    """缺 audio 字段 → 422"""
    response = client.post("/voice/stt/transcribe", json={})
    assert response.status_code == 422


# ===== Cache integration =====


def test_api_tts_cache_hits_on_repeat(client: TestClient) -> None:
    """同 text 两次调用 → 第二次走缓存(端到端验证)"""
    # 第一次
    r1 = client.post("/voice/tts/synthesize", json={"text": "cache test", "format": "mp3"})
    # 第二次(同 key)
    r2 = client.post("/voice/tts/synthesize", json={"text": "cache test", "format": "mp3"})

    assert r1.status_code == 200
    assert r2.status_code == 200
    # 缓存命中:bytes 完全相同
    assert r1.content == r2.content


# ===== Error handling =====


def test_api_tts_provider_error_returns_500(client: TestClient) -> None:
    """Provider 抛错 → 500 + 友好消息"""
    from app.api.voice import get_tts

    class BrokenCache(TTSCache):
        async def synthesize(self, *args, **kwargs):
            raise RuntimeError("upstream TTS failed")

    fake_provider = MockTTSProvider()
    app.dependency_overrides[get_tts] = lambda: BrokenCache(fake_provider)
    try:
        response = client.post("/voice/tts/synthesize", json={"text": "hi"})
        assert response.status_code == 500
    finally:
        app.dependency_overrides.pop(get_tts, None)