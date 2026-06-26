"""火山引擎 TTS / STT Provider — Loop 5b + Loop 10.3+

TTS(新 豆包语音 V3 大模型):
- 端点:https://openspeech.bytedance.com/api/v3/tts/unidirectional (HTTP Chunked 单向流式)
- 鉴权:极简 — header `X-Api-Key` + header `X-Api-Resource-Id` (model selector)
- 请求体: { req_params: { text, speaker, audio_params: { format, sample_rate } } }
- 响应:流式二进制音频 (MP3 / WAV / OGG_OPUS / PCM)
- 在 https://console.volcengine.com/speech/new/setting/apikeys 拿 API Key (UUID 格式)
- 在 https://console.volcengine.com/speech/new/setting/activate 开通模型(seed-tts-2.0 等)

STT(仍用旧版 openspeech V1):
- 端点:https://openspeech.bytedance.com/api/v1/asr
- 鉴权:Authorization: Bearer; <base64(access_key:secret_key)>
- 请求体: { app: { appid, cluster }, user, audio: { data, format, ... }, request }
- AppID/AccessKey/SecretKey 在旧版控制台 https://console.volcengine.com/speech/service/10035 拿

Opus 转换:
- 火山只输出 mp3/wav/ogg_opus/pcm,iOS 要 Opus-in-OGG 直接拿 ogg_opus 即可;
  旧 MP3 → Opus 路径仍保留(虽然基本用不到)
"""
from __future__ import annotations

import base64
import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

import httpx

from app.voice.base import AudioFormat

logger = logging.getLogger(__name__)


# ===== Config =====


@dataclass
class VolcengineConfig:
    """火山引擎语音服务配置

    TTS 用 openspeech V3:api_key + resource_id + default_voice
    STT 用 openspeech V1:app_id + access_key + secret_key

    所有字段从环境变量读取(.env → settings → 注入)。
    """

    # ===== TTS(openspeech V3 简化鉴权)=====
    api_key: str = ""
    resource_id: str = "seed-tts-2.0"  # X-Api-Resource-Id,模型选择器
    tts_endpoint: str = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
    default_voice: str = "BV005_streaming"  # V3 API 接受老 BV*_streaming 和新 bigtts 音色

    # ===== STT(openspeech V1 旧版鉴权)=====
    app_id: str = ""
    access_key: str = ""
    secret_key: str = ""
    stt_endpoint: str = "https://openspeech.bytedance.com/api/v1/asr"
    cluster: str = "volcano_tts"  # STT V1 body 仍需 cluster

    def authorization_header(self) -> str:
        """STT V1 鉴权头(base64 编码 ak:sk,带分号)"""
        token = base64.b64encode(
            f"{self.access_key}:{self.secret_key}".encode("utf-8")
        ).decode("utf-8")
        return f"Bearer; {token}"


# ===== ffmpeg helpers =====


def is_ffmpeg_available() -> bool:
    """检查系统是否安装了 ffmpeg(用于 MP3 → Opus 转换,V3 时代基本用不到)"""
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
        try:
            import os
            os.unlink(mp3_path)
            if os.path.exists(opus_path):
                os.unlink(opus_path)
        except OSError:
            pass


# ===== TTS Provider(openspeech V3)=====


class VolcengineTTSProvider:
    """火山引擎 TTS(openspeech V3 大模型 HTTP Chunked 单向流式)

    synthesize 流程:
    1. 校验 api_key / resource_id / text
    2. 组装 header { X-Api-Key, X-Api-Resource-Id, Content-Type }
       + body { req_params: { text, speaker, audio_params: { format, sample_rate } } }
    3. POST 到 openspeech V3 端点
    4. 读 chunked 流式响应,拼接 bytes

    Raises:
        ValueError: text 为空 / api_key 或 resource_id 缺失
        RuntimeError: 火山 API 错误(非 2xx)
    """

    # AudioFormat → openspeech V3 audio_params.format
    _FORMAT_MAP = {
        AudioFormat.MP3: "mp3",
        AudioFormat.WAV: "wav",
        AudioFormat.OPUS: "ogg_opus",
    }

    def __init__(self, config: VolcengineConfig, timeout: float = 30.0) -> None:
        self.config = config
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    def _validate(self) -> None:
        if not self.config.api_key:
            raise ValueError(
                "火山 TTS 未配置:需要在 .env 设置 VOLC_API_KEY "
                "(在 https://console.volcengine.com/speech/new/setting/apikeys 创建)"
            )
        if not self.config.resource_id:
            raise ValueError(
                "火山 TTS 未配置:需要在 .env 设置 VOLC_RESOURCE_ID (默认 seed-tts-2.0)"
            )

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
        self._validate()

        voice = voice_id or self.config.default_voice
        response_format = self._FORMAT_MAP.get(format, "mp3")

        return await self._synthesize_raw(text, voice, response_format)

    async def _synthesize_raw(
        self,
        text: str,
        voice_id: str,
        response_format: str,
    ) -> bytes:
        """调 openspeech V3 端点,收集 chunked 流式响应 bytes

        V3 文档:
        POST {endpoint}
        headers: X-Api-Key, X-Api-Resource-Id, Content-Type
        body: { req_params: { text, speaker, audio_params: { format, sample_rate } } }
        resp: HTTP/1.1 Chunked,二进制音频流
        """
        url = self.config.tts_endpoint
        headers = {
            "X-Api-Key": self.config.api_key,
            "X-Api-Resource-Id": self.config.resource_id,
            "Content-Type": "application/json",
        }
        body = {
            "req_params": {
                "text": text,
                "speaker": voice_id,
                "audio_params": {
                    "format": response_format,
                    "sample_rate": 24000,
                },
            }
        }

        client = await self._get_client()
        try:
            # stream=True 让 httpx 按 chunk 读,避免一次性把整段响应加载到内存
            async with client.stream(
                "POST", url, headers=headers, content=json.dumps(body),
            ) as response:
                if response.status_code != 200:
                    # 错误响应是 JSON,不是 chunked 流
                    try:
                        err_body = await response.aread()
                        err_text = err_body.decode("utf-8", errors="replace")
                        try:
                            err_json = json.loads(err_text)
                            err_msg = err_json.get("message") or err_json.get("error") or err_text[:300]
                        except json.JSONDecodeError:
                            err_msg = err_text[:300]
                    except Exception:
                        err_msg = f"HTTP {response.status_code}"
                    raise RuntimeError(
                        f"火山 TTS API 错误 (HTTP {response.status_code}): {err_msg}"
                    )

                chunks: list[bytes] = []
                async for chunk in response.aiter_bytes():
                    chunks.append(chunk)
                return b"".join(chunks)
        except httpx.HTTPError as e:
            raise RuntimeError(f"火山 TTS 网络错误: {e}") from e

    @property
    def provider_name(self) -> str:
        return "volcengine"


# ===== STT Provider(openspeech V1)=====


class VolcengineSTTProvider:
    """火山引擎 STT(openspeech V1 大模型 ASR)

    transcribe 流程:
    1. 把音频 bytes base64 编码(火山 API 要求)
    2. POST 到 ASR 端点,header `Authorization: Bearer; <base64(ak:sk)>`
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