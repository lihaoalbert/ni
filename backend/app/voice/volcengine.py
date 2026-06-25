"""火山引擎 TTS / STT Provider — Loop 5b

火山引擎 TTS API (https://openspeech.bytedance.com):
- POST /api/v1/tts
- Bearer token (Base64-encoded access_key:secret_key)
- 请求体: app/user/audio/request
- 响应: 二进制音频 (MP3 默认)

火山引擎 STT API:
- POST /api/v1/asr
- 同样的鉴权方式
- 响应: JSON { code, message, result: { text } }

Opus 转换:
- 火山只返回 MP3,前端要 Opus 时用 ffmpeg 转
- 用 subprocess.run 起 ffmpeg 子进程
"""
from __future__ import annotations

import base64
import json
import logging
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass

import httpx

from app.voice.base import AudioFormat

logger = logging.getLogger(__name__)


# ===== Config =====


@dataclass
class VolcengineConfig:
    """火山引擎语音服务配置

    所有字段从环境变量读取(.env → settings → 注入)。
    app_id / access_key / secret_key 必填(在火山控制台申请)。
    """

    app_id: str
    access_key: str
    secret_key: str
    tts_endpoint: str = "https://openspeech.bytedance.com/api/v1/tts"
    stt_endpoint: str = "https://openspeech.bytedance.com/api/v1/asr"
    cluster: str = "volcengine_tts"
    default_voice: str = "zh_female_qingxin"

    def authorization_header(self) -> str:
        """Bearer token = base64(access_key:secret_key)"""
        token = base64.b64encode(
            f"{self.access_key}:{self.secret_key}".encode("utf-8")
        ).decode("utf-8")
        return f"Bearer; {token}"


# ===== ffmpeg helpers =====


def is_ffmpeg_available() -> bool:
    """检查系统是否安装了 ffmpeg(用于 MP3 → Opus 转换)"""
    return shutil.which("ffmpeg") is not None


def convert_mp3_to_opus(mp3_bytes: bytes) -> bytes:
    """用 ffmpeg 把 MP3 转成 Opus (OGG 容器)

    Args:
        mp3_bytes: MP3 音频 bytes

    Returns:
        OGG/Opus 音频 bytes

    Raises:
        RuntimeError: ffmpeg 不可用或转换失败
    """
    if not is_ffmpeg_available():
        raise RuntimeError(
            "ffmpeg 不可用,无法转换 MP3 → Opus。"
            "请安装 ffmpeg 或改用 MP3 格式。"
        )

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as mp3_in:
        mp3_in.write(mp3_bytes)
        mp3_path = mp3_in.name

    opus_path = mp3_path.replace(".mp3", ".opus")

    try:
        # ffmpeg -y: 覆盖输出
        # -i input: 输入文件
        # -c:a libopus: 用 Opus 编码器
        # -b:a 32k: 比特率 32kbps(语音足够)
        # -vbr on -f opus: 启用 VBR,输出 OGG/Opus
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", mp3_path,
                "-c:a", "libopus",
                "-b:a", "32k",
                "-vbr", "on",
                "-f", "opus",
                opus_path,
            ],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(f"ffmpeg 转换失败: {stderr}")

        with open(opus_path, "rb") as f:
            opus_bytes = f.read()

        logger.debug(
            "MP3 → Opus 转换: %d bytes → %d bytes",
            len(mp3_bytes), len(opus_bytes),
        )
        return opus_bytes
    finally:
        # 清理临时文件
        try:
            import os
            os.unlink(mp3_path)
            if os.path.exists(opus_path):
                os.unlink(opus_path)
        except OSError:
            pass


# ===== TTS Provider =====


class VolcengineTTSProvider:
    """火山引擎 TTS 实现

    synthesize 流程:
    1. 组装请求体
    2. POST 到 TTS 端点
    3. 拿到 MP3 bytes
    4. 如果要 Opus 格式,本地 ffmpeg 转

    Raises:
        ValueError: text 为空
        RuntimeError: 网络/API/编码错误
    """

    def __init__(self, config: VolcengineConfig, timeout: float = 30.0) -> None:
        self.config = config
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def synthesize(
        self,
        text: str,
        voice_id: str | None = None,
        format: AudioFormat = AudioFormat.MP3,
    ) -> bytes:
        if not text or not text.strip():
            raise ValueError("text 不能为空")

        voice = voice_id or self.config.default_voice

        # 火山 TTS 支持 MP3 / wav / pcm,Opus 走本地转
        if format == AudioFormat.OPUS:
            mp3_bytes = await self._synthesize_raw(text, voice, "mp3")
            return convert_mp3_to_opus(mp3_bytes)
        elif format == AudioFormat.WAV:
            return await self._synthesize_raw(text, voice, "wav")
        else:
            return await self._synthesize_raw(text, voice, "mp3")

    async def _synthesize_raw(
        self,
        text: str,
        voice_id: str,
        audio_format: str,
    ) -> bytes:
        """直接调火山 TTS API 拿音频"""
        # 火山 TTS 异步 API:audio_params 是 JSON-encoded 字符串(实测 500 if nested object)
        # speech_rate: -50 ~ 50 (字面 0 = 正常速度)
        audio_params = json.dumps({
            "format": audio_format,
            "sample_rate": 16000,
            "speech_rate": 0,
        })
        body = {
            "app": {
                "appid": self.config.app_id,
                "cluster": self.config.cluster,
                "token": self.config.authorization_header(),
            },
            "user": {"uid": "default_user"},
            "audio": {
                "voice_type": voice_id,
                "encoding": audio_format,
                "speed_ratio": 1.0,
                "volume_ratio": 1.0,
                "pitch_ratio": 1.0,
            },
            "request": {
                "reqid": str(uuid.uuid4()),
                "text": text,
                "text_type": "plain",
                "operation": "query",
                "with_frontend": 1,
                "frontend_type": "unitTson",
                "audio_params": audio_params,
            },
        }

        headers = {
            "Authorization": self.config.authorization_header(),
            "Content-Type": "application/json",
        }

        client = await self._get_client()
        try:
            response = await client.post(
                self.config.tts_endpoint, json=body, headers=headers,
            )
        except httpx.HTTPError as e:
            raise RuntimeError(f"TTS 网络错误: {e}") from e

        if response.status_code != 200:
            # 火山可能返回 JSON 错误或纯文本
            try:
                err_body = response.json()
                err_msg = err_body.get("message", str(err_body))
            except Exception:
                err_msg = response.text[:200]
            raise RuntimeError(
                f"TTS API 错误 (HTTP {response.status_code}): {err_msg}"
            )

        return response.content

    @property
    def provider_name(self) -> str:
        return "volcengine"


# ===== STT Provider =====


class VolcengineSTTProvider:
    """火山引擎 STT 实现

    transcribe 流程:
    1. 把音频 bytes base64 编码(火山 API 要求)
    2. POST 到 ASR 端点
    3. 解析 JSON 拿到 text

    Raises:
        ValueError: audio 为空
        RuntimeError: 网络/API 错误
    """

    def __init__(self, config: VolcengineConfig, timeout: float = 30.0) -> None:
        self.config = config
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def transcribe(
        self,
        audio: bytes,
        format: AudioFormat = AudioFormat.MP3,
        language: str = "zh-CN",
    ) -> str:
        if not audio:
            raise ValueError("audio bytes 不能为空")

        # 火山 ASR 的格式字段名
        format_map = {
            AudioFormat.MP3: "mp3",
            AudioFormat.WAV: "wav",
            AudioFormat.OPUS: "opus",
        }
        audio_format = format_map.get(format, "mp3")

        audio_base64 = base64.b64encode(audio).decode("utf-8")

        body = {
            "app": {
                "appid": self.config.app_id,
                "cluster": self.config.cluster,
            },
            "user": {"uid": "default_user"},
            "audio": {
                "data": audio_base64,
                "format": audio_format,
                "sample_rate": 16000,
                "language": language,
                "bits": 16,
                "channel": 1,
            },
            "request": {
                "model_name": "bigmodel",
            },
        }

        headers = {
            "Authorization": self.config.authorization_header(),
            "Content-Type": "application/json",
        }

        client = await self._get_client()
        try:
            response = await client.post(
                self.config.stt_endpoint, json=body, headers=headers,
            )
        except httpx.HTTPError as e:
            raise RuntimeError(f"STT 网络错误: {e}") from e

        if response.status_code != 200:
            try:
                err_body = response.json()
                err_msg = err_body.get("message", str(err_body))
            except Exception:
                err_msg = response.text[:200]
            raise RuntimeError(
                f"STT API 错误 (HTTP {response.status_code}): {err_msg}"
            )

        data = response.json()
        result = data.get("result") or {}
        text = result.get("text", "")
        return text.strip()

    @property
    def provider_name(self) -> str:
        return "volcengine"