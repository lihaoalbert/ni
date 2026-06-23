"""Voice 模块基础 — Loop 5a

定义 TTSProvider / STTProvider Protocol 和 AudioFormat 枚举。

设计:
- 跟 LLMProvider / EmbeddingProvider 同样的 Protocol 模式
- Mock 实现见 mock.py
- 火山引擎实现见 volcengine.py (Loop 5b)
- API 端点见 api/voice.py (Loop 5c)
"""
from __future__ import annotations

from enum import Enum
from typing import Protocol


class AudioFormat(str, Enum):
    """音频格式

    - MP3: 通用,文件小,移动端直接播放
    - WAV: 无损,大,适合调试
    - OPUS: 高压缩比 + 流式友好,适合实时语音
    """

    MP3 = "mp3"
    WAV = "wav"
    OPUS = "opus"


class TTSProvider(Protocol):
    """TTS (Text-to-Speech) Provider 接口

    所有实现必须:
    - async synthesize(text) → bytes
    - 支持可选 voice_id (不同音色)
    - 支持 format 参数 (MP3/WAV/OPUS)
    """

    async def synthesize(
        self,
        text: str,
        voice_id: str | None = None,
        format: AudioFormat = AudioFormat.MP3,
    ) -> bytes:
        """合成语音

        Args:
            text: 要转语音的文本(中文为主)
            voice_id: 音色 ID,None 用默认
            format: 输出音频格式

        Returns:
            音频 bytes(对应 format 的编码)

        Raises:
            ValueError: text 为空
        """
        ...


class STTProvider(Protocol):
    """STT (Speech-to-Text) Provider 接口

    所有实现必须:
    - async transcribe(audio) → str
    - 支持 format 参数 (输入音频格式)
    """

    async def transcribe(
        self,
        audio: bytes,
        format: AudioFormat = AudioFormat.MP3,
        language: str = "zh-CN",
    ) -> str:
        """转写语音为文字

        Args:
            audio: 音频 bytes
            format: 输入音频格式
            language: 语言代码(默认 zh-CN)

        Returns:
            转写出的文字

        Raises:
            ValueError: audio 为空
        """
        ...