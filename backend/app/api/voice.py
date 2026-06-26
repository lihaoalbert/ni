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


class TTSInfoResponse(BaseModel):
    """TTS provider 状态 — iOS ChatView toolbar 用来显示当前是 mock / volcengine / 未配置

    不实际调 TTS,只是 introspection,所以缓存 / 网络抖动不影响。
    """
    provider: str = Field(..., description="mock | volcengine")
    configured: bool = Field(..., description="凭据是否齐全(对 volcengine 才有意义)")
    default_voice: str = Field(..., description="默认音色 ID")
    endpoint: str = Field(..., description="TTS HTTP 端点(host 路径)")
    cache_backend: str = Field(..., description="memory | redis")
    cache_max_size: int


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


@router.get("/tts/info", response_model=TTSInfoResponse)
async def tts_info(
    settings: Settings = Depends(get_settings),
) -> TTSInfoResponse:
    """TTS provider 状态 introspection — iOS toolbar 显示

    不实际调 TTS,只是读 settings。火山凭据缺失时不抛错,configured=false
    让前端知道"火山未配,听的是 mock 或 fallback"。
    """
    provider = settings.tts_provider
    # TTS 用 openspeech V3 — 只需 api_key(默认 seed-tts-2.0 资源 ID)
    configured = bool(settings.volc_api_key)
    # endpoint host 部分,避免泄露 token / 完整 URL
    endpoint_host = settings.volc_tts_endpoint.split("//", 1)[-1].split("/", 1)[0]
    return TTSInfoResponse(
        provider=provider,
        configured=configured,
        default_voice=settings.volc_default_voice,
        endpoint=endpoint_host,
        cache_backend=settings.tts_cache_backend,
        cache_max_size=settings.tts_cache_max_size,
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