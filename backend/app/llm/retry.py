"""LLM 调用重试与超时 — Day 6

设计原则：
1. **区分可重试 vs 不可重试**：4xx 大多不可重试（400/401/403/404/422），网络/限流/过载可重试
2. **指数退避 + 抖动**：1s → 2s → 4s（封顶 10s），避免雪崩
3. **流式分块超时**：流不能套一个全局 asyncio.timeout——一旦超时中途数据全丢。
   改用「空闲超时」（idle timeout）：超过 N 秒没新事件才放弃。
4. **不吞 CancellationError**：ctrl-C / 任务取消必须能传透
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Awaitable, Callable, TypeVar

from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    RateLimitError,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

# 哪些异常类 + 状态码属于"可重试"
# 注意：ServiceUnavailableError 走 503 状态码路径（不再单独 import）
RETRYABLE_EXC_TYPES = (APIConnectionError, APITimeoutError, RateLimitError)
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504, 529}


def is_retryable(exc: BaseException) -> bool:
    """判断异常是否可重试"""
    if isinstance(exc, asyncio.CancelledError):
        return False  # 取消永远不重试
    if isinstance(exc, RETRYABLE_EXC_TYPES):
        return True
    if isinstance(exc, APIStatusError) and exc.status_code in RETRYABLE_STATUS_CODES:
        return True
    return False


async def call_with_retry(
    factory: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 10.0,
    op_name: str = "llm_call",
) -> T:
    """对一次异步调用做指数退避重试

    Args:
        factory: 一个零参 callable，每次重试都重新调用以拿到新 coroutine
        max_attempts: 总尝试次数（含首次）
        base_delay: 首次重试前的等待（秒）
        max_delay: 单次等待上限（秒）
        op_name: 日志标签
    """
    if max_attempts < 1:
        raise ValueError("max_attempts 必须 >= 1")

    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await factory()
        except Exception as e:
            if not is_retryable(e) or attempt >= max_attempts:
                raise
            last_exc = e
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            logger.warning(
                "%s retry %d/%d after %.1fs: %s: %s",
                op_name,
                attempt,
                max_attempts,
                delay,
                type(e).__name__,
                e,
            )
            await asyncio.sleep(delay)

    # 理论不会到这里（最后一次失败会 raise）
    assert last_exc is not None
    raise last_exc


async def aiter_with_idle_timeout(
    source: AsyncIterator[T],
    *,
    idle_timeout: float,
    op_name: str = "llm_stream",
) -> AsyncIterator[T]:
    """给异步迭代器加"空闲超时"

    每取一个事件都重置计时：只要 `idle_timeout` 秒内能拿到下一个就继续；
    拿不到则抛 TimeoutError 给调用方。

    关键：不能用单个 asyncio.timeout 套住整个 async for——
    那会丢掉所有已 yield 的数据。流式场景必须分块判断。
    """
    while True:
        try:
            item = await asyncio.wait_for(source.__anext__(), timeout=idle_timeout)
        except StopAsyncIteration:
            return
        except asyncio.TimeoutError:
            logger.error("%s idle timeout after %.1fs (no new event)", op_name, idle_timeout)
            raise
        yield item
