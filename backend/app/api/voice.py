"""/tts/synthesize 和 /stt/transcribe 端点 — Loop 5c

TTS:
- Request: { text, voice_id?, format? }
- Response: 音频 bytes (audio/mpeg for mp3, audio/ogg for opus, audio/wav for wav)

STT:
- Request: { audio: base64, format?, language? }
- Response: { text, language }
"""
from __future__ import annotations

import base64
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.errors import to_http_exception
from app.config import Settings, get_settings
from app.voice.base import AudioFormat, TTSProvider
from app.voice.factory import get_stt_provider, get_tts_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"])


# ===== Request / Response Schemas =====


class TTSSynthesizeRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=1000, description="要合成的文本")
    voice_id: str | None = Field(None, description="音色 ID,None 用默认")
    format: AudioFormat = Field(AudioFormat.MP3, description="输出音频格式")


class STTTranscribeRequest(BaseModel):
    audio: str = Field(..., min_length=1, description="base64 编码的音频 bytes")
    format: AudioFormat = Field(AudioFormat.MP3, description="输入音频格式")
    language: str = Field("zh-CN", description="语言代码")


class STTTranscribeResponse(BaseModel):
    text: str
    language: str


# ===== Format → MIME 映射 =====


_FORMAT_MIME = {
    AudioFormat.MP3: "audio/mpeg",
    AudioFormat.WAV: "audio/wav",
    AudioFormat.OPUS: "audio/ogg",
}


# ===== Dependencies =====


def get_tts(
    settings: Settings = Depends(get_settings),
) -> TTSProvider:
    """FastAPI 依赖:返回带缓存的 TTS provider(单例)"""
    return get_tts_provider(settings)


# ===== Endpoints =====


@router.post("/tts/synthesize")
async def tts_synthesize(
    req: TTSSynthesizeRequest,
    tts: TTSProvider = Depends(get_tts),
) -> Response:
    """文本 → 音频 bytes

    Returns:
        音频二进制流(Content-Type 由 format 决定)
    """
    try:
        audio = await tts.synthesize(
            req.text,
            voice_id=req.voice_id,
            format=req.format,
        )
    except ValueError as e:
        # 业务校验错误 → 422
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("TTS 合成失败")
        raise to_http_exception(e)

    return Response(
        content=audio,
        media_type=_FORMAT_MIME[req.format],
        headers={
            "Content-Length": str(len(audio)),
            "X-Audio-Format": req.format.value,
        },
    )


@router.post("/stt/transcribe")
async def stt_transcribe(
    req: STTTranscribeRequest,
    settings: Settings = Depends(get_settings),
) -> STTTranscribeResponse:
    """音频 → 文本

    音频以 base64 字符串传入(避免 JSON 装二进制出错)。
    """
    try:
        audio_bytes = base64.b64decode(req.audio)
    except Exception as e:
        raise HTTPException(
            status_code=422, detail=f"audio 字段不是合法 base64: {e}"
        )

    if not audio_bytes:
        raise HTTPException(status_code=422, detail="audio 解码后为空")

    provider = get_stt_provider(settings)
    try:
        text = await provider.transcribe(
            audio_bytes,
            format=req.format,
            language=req.language,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("STT 转写失败")
        raise to_http_exception(e)

    return STTTranscribeResponse(text=text, language=req.language)