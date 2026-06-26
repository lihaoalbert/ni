"""火山引擎 TTS / STT Provider — Loop 5b + Loop 10.3+

TTS(新 豆包语音 V3 大模型):
- 端点:https://openspeech.bytedance.com/api/v3/tts/unidirectional
- 鉴权:极简 — header `X-Api-Key` + header `X-Api-Resource-Id` (model selector)
- 请求体: { req_params: { text, speaker, audio_params: { format, sample_rate } } }
- 响应:application/json — { code:0, message:"", data:"<base64 音频>" }
- 在 https://console.volcengine.com/speech/new/setting/apikeys 拿 API Key (UUID 格式)
- 在 https://console.volcengine.com/speech/new/setting/activate 开通模型(seed-tts-2.0 等)

注意:V3 大模型 (resource_id=seed-tts-2.0) 配的是新命名音色
(zh_female_vv_uranus_bigtss / saturn_zh_female_cancan_tob 等);
BV*_streaming 音色属于小模型 volcano_tts 集群,不能跟 seed-tts-2.0 混用。

STT(仍用旧版 openspeech V1):
- 端点:https://openspeech.bytedance.com/api/v1/asr
- 鉴权:Authorization: Bearer; <base64(access_key:secret_key)>
- 请求体: { app: { appid, cluster }, user, audio: { data, format, ... }, request }
- AppID/AccessKey/SecretKey 在旧版控制台 https://console.volcengine.com/speech/service/10035 拿

Opus 转换:
- V3 直出 ogg_opus(无需 ffmpeg 转换);旧 MP3 → Opus 路径保留兜底
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
    default_voice: str = "saturn_zh_female_cancan_tob"  # 知性灿灿(V3 大模型配对音色)

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
        """调 openspeech V3 端点,解析 NDJSON 响应,拼接 base64 解码 audio

        V3 unidirectional 端点实际响应(NDJSON,换行分隔多行 JSON):
        POST {endpoint}
        headers: X-Api-Key, X-Api-Resource-Id, Content-Type: application/json
        body: { req_params: { text, speaker, audio_params: { format, sample_rate } } }
        resp: text/plain; charset=utf-8
          {"code":0,"message":"","data":"<base64 音频片段1>"}
          {"code":0,"message":"","data":"<base64 音频片段2>"}
          ...
          {"code":0,"message":"","data":null,"sentence":{...}}    # 句末元数据
          {"code":20000000,"message":"OK","data":null}              # 结束标记
        错误时所有 chunk 都带非 0 code。
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
            response = await client.post(
                url, headers=headers, content=json.dumps(body),
            )
        except httpx.HTTPError as e:
            raise RuntimeError(f"火山 TTS 网络错误: {e}") from e

        if response.status_code != 200:
            err_text = response.text[:300]
            try:
                err_json = response.json()
                err_msg = err_json.get("message") or err_text
            except Exception:
                err_msg = err_text
            raise RuntimeError(
                f"火山 TTS API 错误 (HTTP {response.status_code}): {err_msg}"
            )

        # NDJSON:逐行解析,拼接所有非空 data 的 base64 解码结果
        # code 语义:0=音频 chunk OK, 20000000=结束标记 OK, 其他=错误
        chunks: list[bytes] = []
        error_code: int | None = None
        error_message: str = ""
        for line in response.text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            code = obj.get("code", 0)
            data_b64 = obj.get("data")
            if code not in (0, 20000000):
                # 非 OK 状态:记下错误(可能多行错误,但首条通常最有信息量)
                if error_code is None:
                    error_code = code
                    error_message = obj.get("message", "")
                continue
            if data_b64:
                try:
                    chunks.append(base64.b64decode(data_b64))
                except Exception as e:
                    raise RuntimeError(
                        f"火山 TTS base64 解码失败 (line code={code}): {e}"
                    ) from e

        if error_code is not None:
            raise RuntimeError(
                f"火山 TTS API 错误 (code={error_code}): {error_message}"
            )

        if not chunks:
            raise RuntimeError(
                f"火山 TTS 响应无音频数据: {response.text[:200]}"
            )

        return b"".join(chunks)

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