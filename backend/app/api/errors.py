"""HTTP 错误映射 — Day 6

把 Provider / 内部异常翻译成：
1. HTTPException（带合适的 status_code + 中文友好 detail）
2. SSE 错误事件 payload（用于 /chat/stream）
3. 用户可理解的 error_kind（用于监控/告警）
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)
from fastapi import HTTPException

logger = logging.getLogger(__name__)


@dataclass
class MappedError:
    """统一的错误表达 — 端点用它构造 HTTP 响应 / SSE 事件"""

    status_code: int
    kind: str  # 错误类型短码：upstream_4xx / upstream_429 / upstream_5xx / timeout / network / internal
    message: str  # 面向用户的友好提示
    upstream_status: int | None = None  # LLM 原始状态码（如有）
    detail: str = ""  # 调试细节（不入用户响应）


# 用户友好的中文错误模板
_USER_FRIENDLY: dict[str, str] = {
    "auth": "AI 服务鉴权失败，请联系管理员检查 ANTHROPIC_API_KEY",
    "permission": "AI 服务权限不足，请检查 API Key 权限",
    "not_found": "请求的 AI 模型不存在或不可用",
    "bad_request": "请求格式被 AI 服务拒绝（提示词可能太长）",
    "rate_limit": "AI 服务限流，请稍后重试",
    "overloaded": "AI 服务过载，请稍后重试",
    "server": "AI 服务异常，请稍后重试",
    "timeout": "AI 响应超时，请稍后重试",
    "network": "无法连接 AI 服务，请稍后重试",
    "internal": "服务内部错误，请稍后重试",
}


def map_exception(exc: BaseException) -> MappedError:
    """把异常翻译成统一 MappedError

    设计：
    - 401/403/404/400 → 502 Bad Gateway（我们这边的鉴权问题，5xx 让用户知道是上游）
    - 429 → 503（限流是临时的）
    - 5xx/529 → 502（上游错误）
    - 网络/超时 → 504 Gateway Timeout
    - 其他 → 500
    """
    if isinstance(exc, APITimeoutError):
        return MappedError(504, "timeout", _USER_FRIENDLY["timeout"], detail=str(exc))

    if isinstance(exc, APIConnectionError):
        return MappedError(502, "network", _USER_FRIENDLY["network"], detail=str(exc))

    if isinstance(exc, RateLimitError):
        # 429 — 我们已重试 N 次还是限流，告诉客户端暂时过载
        return MappedError(
            503, "upstream_429", _USER_FRIENDLY["rate_limit"],
            upstream_status=429, detail=str(exc),
        )

    if isinstance(exc, AuthenticationError):
        return MappedError(502, "auth", _USER_FRIENDLY["auth"],
                           upstream_status=401, detail=str(exc))
    if isinstance(exc, PermissionDeniedError):
        return MappedError(502, "permission", _USER_FRIENDLY["permission"],
                           upstream_status=403, detail=str(exc))
    if isinstance(exc, NotFoundError):
        return MappedError(502, "not_found", _USER_FRIENDLY["not_found"],
                           upstream_status=404, detail=str(exc))
    if isinstance(exc, BadRequestError):
        return MappedError(502, "bad_request", _USER_FRIENDLY["bad_request"],
                           upstream_status=400, detail=str(exc))

    if isinstance(exc, APIStatusError):
        # 其他 status（5xx、529 等）
        code = exc.status_code
        if code == 529:
            kind, msg = "overloaded", _USER_FRIENDLY["overloaded"]
        elif code >= 500:
            kind, msg = "upstream_5xx", _USER_FRIENDLY["server"]
        else:
            kind, msg = "upstream_4xx", _USER_FRIENDLY["bad_request"]
        return MappedError(502, kind, msg, upstream_status=code, detail=str(exc))

    if isinstance(exc, asyncio.TimeoutError):
        return MappedError(504, "timeout", _USER_FRIENDLY["timeout"], detail="asyncio.TimeoutError")

    # 未识别
    return MappedError(500, "internal", _USER_FRIENDLY["internal"],
                       detail=f"{type(exc).__name__}: {exc!s}")


def to_http_exception(exc: BaseException) -> HTTPException:
    """映射异常 → FastAPI HTTPException

    detail 字段包含：友好 message + 上游状态码（若有）
    """
    mapped = map_exception(exc)
    detail: dict[str, Any] = {
        "kind": mapped.kind,
        "message": mapped.message,
    }
    if mapped.upstream_status:
        detail["upstream_status"] = mapped.upstream_status
    if mapped.detail and logger.isEnabledFor(logging.DEBUG):
        # DEBUG 模式才暴露底层错误（避免泄露内部信息）
        detail["debug"] = mapped.detail

    # 记录到日志
    logger.warning(
        "api_error kind=%s status=%d upstream=%s detail=%s",
        mapped.kind, mapped.status_code, mapped.upstream_status, mapped.detail,
    )

    return HTTPException(status_code=mapped.status_code, detail=detail)


def to_sse_error_event(exc: BaseException) -> dict:
    """映射异常 → SSE 错误事件 payload

    SSE 客户端要的是结构化信息：type=error + 用户友好 message + kind
    """
    mapped = map_exception(exc)
    out: dict = {
        "type": "error",
        "kind": mapped.kind,
        "error": mapped.message,
    }
    if mapped.upstream_status:
        out["upstream_status"] = mapped.upstream_status
    return out
