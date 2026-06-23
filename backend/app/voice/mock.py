"""Mock TTS / STT Provider — Loop 5a

让 CI 和单测在没有真实 API 的情况下能跑通:
- MockTTSProvider: 生成"看起来对"的音频 bytes
  - WAV: 真正的 WAV 头 + 静音 PCM (0.1s, 16kHz, 16-bit mono)
  - MP3: 带 "MP3MOCK" 标识的伪 MP3 frame
  - OPUS: OGG 容器 + 标识(测试验 "OggS" 前缀)
- MockSTTProvider: 返回固定中文文本(可配)

确定性: 同 text + voice_id + format → 同 bytes
可缓存: 任意实现都可以接 dict/Redis 缓存
"""
from __future__ import annotations

import hashlib
import logging
import struct

from app.voice.base import AudioFormat, STTProvider, TTSProvider

logger = logging.getLogger(__name__)


# ===== Mock TTS =====


class MockTTSProvider:
    """Mock TTS — 不用真 API,生成测试用音频 bytes

    真实听不了(静音/无意义内容),但格式头对,集成测试能验证 codec 调用链路。
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        duration_ms: int = 100,
    ) -> None:
        self.sample_rate = sample_rate
        self.duration_ms = duration_ms
        logger.debug(
            "MockTTSProvider init: sample_rate=%d duration_ms=%d",
            sample_rate, duration_ms,
        )

    async def synthesize(
        self,
        text: str,
        voice_id: str | None = None,
        format: AudioFormat = AudioFormat.MP3,
    ) -> bytes:
        if not text or not text.strip():
            raise ValueError("text 不能为空")

        # 确定性: 内容 hash 影响音频(模拟不同 text 不同声波)
        text_hash = hashlib.md5(
            f"{text}|{voice_id}|{format.value}".encode()
        ).digest()

        if format == AudioFormat.WAV:
            return _make_wav(self.sample_rate, self.duration_ms, text_hash)
        elif format == AudioFormat.OPUS:
            return _make_opus_ogg(self.sample_rate, self.duration_ms, text_hash)
        else:  # MP3 / default
            return _make_fake_mp3(text, voice_id, text_hash)

    @property
    def provider_name(self) -> str:
        return "mock"


# ===== Mock STT =====


class MockSTTProvider:
    """Mock STT — 不用真 API,返回固定中文文本

    行为:
    - 默认返回 self.default_text
    - 不同 audio 大小返回不同 fallback(让测试区分"啥也没说"和"说了 1 秒")
    """

    def __init__(self, default_text: str = "用户说了什么") -> None:
        self.default_text = default_text
        logger.debug("MockSTTProvider init: default_text=%r", default_text)

    async def transcribe(
        self,
        audio: bytes,
        format: AudioFormat = AudioFormat.MP3,
        language: str = "zh-CN",
    ) -> str:
        if not audio:
            raise ValueError("audio bytes 不能为空")

        # 确定性: 同 audio bytes → 同 text(用 hash 后取部分)
        audio_hash = hashlib.md5(audio).hexdigest()[:8]
        # 模拟不同长度音频的不同"识别结果"
        if len(audio) < 100:
            return f"{self.default_text}(短音频 {audio_hash})"
        elif len(audio) < 10000:
            return f"{self.default_text}(中等长度音频 {audio_hash})"
        else:
            return f"{self.default_text}(长音频 {audio_hash})"

    @property
    def provider_name(self) -> str:
        return "mock"


# ===== 音频生成工具 =====


def _make_wav(sample_rate: int, duration_ms: int, content_seed: bytes) -> bytes:
    """生成 WAV bytes — RIFF 头 + 静音 PCM

    16-bit mono, sample_rate, duration_ms 长度。
    content_seed 用来"伪随机化"采样,同 seed → 同 bytes。
    """
    num_samples = sample_rate * duration_ms // 1000
    data_size = num_samples * 2  # 16-bit
    file_size = 36 + data_size

    # RIFF header
    header = b"RIFF"
    header += struct.pack("<I", file_size)
    header += b"WAVE"

    # fmt subchunk
    header += b"fmt "
    header += struct.pack("<I", 16)  # subchunk size
    header += struct.pack("<H", 1)  # PCM format
    header += struct.pack("<H", 1)  # mono
    header += struct.pack("<I", sample_rate)
    header += struct.pack("<I", sample_rate * 2)  # byte rate
    header += struct.pack("<H", 2)  # block align
    header += struct.pack("<H", 16)  # bits per sample

    # data subchunk
    header += b"data"
    header += struct.pack("<I", data_size)

    # 静音 PCM(全 0)— 但用 seed 生成伪随机小幅度变化,体现"不同 text 不同"
    samples = bytearray(data_size)
    for i in range(0, data_size, 2):
        # 每 100 样本用 seed 生成一个 16-bit 值
        seed_idx = (i // 200) % len(content_seed)
        amp = int(content_seed[seed_idx]) - 128  # -128..127
        amp = amp * 16  # 缩小幅度,让听感"软"
        struct.pack_into("<h", samples, i, max(-32768, min(32767, amp)))

    return header + bytes(samples)


def _make_opus_ogg(sample_rate: int, duration_ms: int, content_seed: bytes) -> bytes:
    """生成伪 Opus 包装在 OGG 容器里

    真正的 Opus 编码需要 libopus,pydub,ffmpeg 等。这里只生成 OGG 头,
    让测试能验 "OggS" 前缀,真编码在 Loop 5b 接火山时再做。
    """
    # OGG page header (capture pattern + version + flags + ...)
    header = b"OggS"  # 0: capture pattern
    header += b"\x00"  # version
    header += b"\x02"  # header type (BOS)
    header += b"\x00" * 8  # granule
    header += b"\x00" * 4  # serial
    header += b"\x00" * 4  # page sequence
    header += b"\x00" * 4  # checksum
    header += b"\x01"  # segment count
    header += b"\x13"  # segment size (19 bytes OpusHead)

    # OpusHead identification
    header += b"OpusHead"
    header += b"\x01"  # version
    header += b"\x01"  # channel count
    header += struct.pack("<H", 480)  # pre-skip
    header += struct.pack("<I", sample_rate)
    header += struct.pack("<h", 0)  # output gain
    header += b"\x00"  # mapping family

    # Payload:伪 Opus packets,用 seed 做内容
    num_packets = max(1, duration_ms // 20)  # 20ms per packet
    payload = content_seed * ((num_packets // len(content_seed)) + 1)
    payload = payload[:num_packets * 4]

    return header + payload


def _make_fake_mp3(text: str, voice_id: str | None, content_seed: bytes) -> bytes:
    """生成伪 MP3 bytes(非真 MP3,只是有标识)

    真 MP3 编码需 lame / pydub。这里只生成带 ID3v2 头 + payload,
    让调用方知道"这是 MP3 格式"。Loop 5b 接火山时,火山返回真 MP3,
    这个 mock 函数会被替代。
    """
    # ID3v2 header
    header = b"ID3"
    header += b"\x04\x00"  # version 2.4.0
    header += b"\x00"  # flags
    header += b"\x00\x00\x00\x00"  # size (synchsafe int, 实际为 0)

    # 内容: 标记 + seed + text(让"看起来像")
    body = b"MP3MOCK:"
    body += content_seed[:16]
    body += text.encode("utf-8")[:200]
    if voice_id:
        body += b"|" + voice_id.encode("utf-8")[:50]

    return header + body