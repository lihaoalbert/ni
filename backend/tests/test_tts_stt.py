"""TTS / STT Provider 测试 — Loop 5a 骨架

Phase A 目标: Provider Protocol + Mock 实现,可替换
Phase B (Loop 5b): 接火山引擎真实 API
Phase C (Loop 5c): API 端点 + 生产开关

测试策略:
- 默认 Mock,无外部依赖
- Protocol 契约验证(签名、类型)
- Mock 实现行为(确定性、可缓存、字节有效)
"""
from __future__ import annotations

import inspect
import struct

import pytest

from app.voice.base import AudioFormat, STTProvider, TTSProvider
from app.voice.mock import MockSTTProvider, MockTTSProvider


# ===== Protocol 契约 =====


def test_audio_format_enum() -> None:
    """AudioFormat 枚举: 至少包含 MP3 / WAV / OPUS"""
    assert AudioFormat.MP3.value == "mp3"
    assert AudioFormat.WAV.value == "wav"
    assert AudioFormat.OPUS.value == "opus"
    # 至少这 3 种
    members = {m.value for m in AudioFormat}
    assert "mp3" in members
    assert "wav" in members
    assert "opus" in members


def test_tts_provider_protocol_signature() -> None:
    """TTSProvider.synthesize 签名: text 必填,voice_id/format 可选"""
    sig = inspect.signature(TTSProvider.synthesize)
    params = sig.parameters
    assert "text" in params
    assert "voice_id" in params
    # 应该有 format 参数(默认 MP3)
    assert "format" in params


def test_stt_provider_protocol_signature() -> None:
    """STTProvider.transcribe 签名: audio 必填,format/language 可选"""
    sig = inspect.signature(STTProvider.transcribe)
    params = sig.parameters
    assert "audio" in params


def test_mock_tts_implements_protocol() -> None:
    """MockTTSProvider 满足 TTSProvider"""
    provider = MockTTSProvider()
    assert hasattr(provider, "synthesize")
    assert callable(provider.synthesize)


def test_mock_stt_implements_protocol() -> None:
    """MockSTTProvider 满足 STTProvider"""
    provider = MockSTTProvider()
    assert hasattr(provider, "transcribe")
    assert callable(provider.transcribe)


# ===== Mock TTS 行为 =====


@pytest.mark.asyncio
async def test_mock_tts_returns_bytes() -> None:
    """Mock TTS 接受 text,返回非空 bytes"""
    provider = MockTTSProvider()
    audio = await provider.synthesize("你好世界")
    assert isinstance(audio, bytes)
    assert len(audio) > 0


@pytest.mark.asyncio
async def test_mock_tts_is_deterministic_for_same_input() -> None:
    """同 text 同 voice_id 同 format → 同 bytes(支持缓存)"""
    provider = MockTTSProvider()
    a1 = await provider.synthesize("你好世界")
    a2 = await provider.synthesize("你好世界")
    assert a1 == a2


@pytest.mark.asyncio
async def test_mock_tts_different_text_different_output() -> None:
    """不同 text → 不同 bytes(至少大概率不同)"""
    provider = MockTTSProvider()
    a1 = await provider.synthesize("你好")
    a2 = await provider.synthesize("再见")
    assert a1 != a2


@pytest.mark.asyncio
async def test_mock_tts_different_voice_different_output() -> None:
    """不同 voice_id → 不同 bytes"""
    provider = MockTTSProvider()
    a1 = await provider.synthesize("你好", voice_id="voice_a")
    a2 = await provider.synthesize("你好", voice_id="voice_b")
    assert a1 != a2


@pytest.mark.asyncio
async def test_mock_tts_wav_has_valid_header() -> None:
    """Mock WAV 格式输出应有合法 RIFF/WAVE 头部"""
    provider = MockTTSProvider()
    audio = await provider.synthesize("你好", format=AudioFormat.WAV)
    # WAV 文件以 "RIFF" 开头
    assert audio[:4] == b"RIFF"
    # WAVE 标识在第 8-12 字节
    assert audio[8:12] == b"WAVE"


@pytest.mark.asyncio
async def test_mock_tts_supports_all_formats() -> None:
    """3 种格式都能合成"""
    provider = MockTTSProvider()
    for fmt in [AudioFormat.MP3, AudioFormat.WAV, AudioFormat.OPUS]:
        audio = await provider.synthesize("测试", format=fmt)
        assert len(audio) > 0, f"empty audio for format {fmt}"


@pytest.mark.asyncio
async def test_mock_tts_opus_marker() -> None:
    """Mock Opus 输出应有可识别的标记(让测试知道是 opus)"""
    provider = MockTTSProvider()
    audio = await provider.synthesize("你好", format=AudioFormat.OPUS)
    # 我们让 mock opus 输出 OGG 容器(opus 标准容器)
    # 简单做法:前 4 字节是 "OggS"
    assert audio[:4] == b"OggS", f"Opus should start with OGG marker, got {audio[:20]!r}"


@pytest.mark.asyncio
async def test_mock_tts_empty_text_raises() -> None:
    """空 text 应报错"""
    provider = MockTTSProvider()
    with pytest.raises(ValueError):
        await provider.synthesize("")


# ===== Mock STT 行为 =====


@pytest.mark.asyncio
async def test_mock_stt_returns_string() -> None:
    """Mock STT 接受 audio bytes,返回 str"""
    provider = MockSTTProvider()
    # 任何 bytes 都行(mock 不解析)
    text = await provider.transcribe(b"\x00\x01\x02\x03" * 100)
    assert isinstance(text, str)
    assert len(text) > 0


@pytest.mark.asyncio
async def test_mock_stt_deterministic() -> None:
    """Mock STT 同 input → 同 output"""
    provider = MockSTTProvider()
    audio = b"\x10\x20\x30" * 50
    t1 = await provider.transcribe(audio)
    t2 = await provider.transcribe(audio)
    assert t1 == t2


@pytest.mark.asyncio
async def test_mock_stt_returns_chinese() -> None:
    """Mock STT 返回中文(默认场景,数字人用户都说中文)"""
    provider = MockSTTProvider(default_text="用户说了什么")
    text = await provider.transcribe(b"\x00\x01")
    assert "用户" in text


@pytest.mark.asyncio
async def test_mock_stt_empty_audio_raises() -> None:
    """空 audio 应报错"""
    provider = MockSTTProvider()
    with pytest.raises(ValueError):
        await provider.transcribe(b"")


@pytest.mark.asyncio
async def test_mock_stt_accepts_format() -> None:
    """STT 接受 format 参数(用于不同编码)"""
    provider = MockSTTProvider()
    for fmt in [AudioFormat.MP3, AudioFormat.WAV, AudioFormat.OPUS]:
        text = await provider.transcribe(b"\x00" * 100, format=fmt)
        assert len(text) > 0