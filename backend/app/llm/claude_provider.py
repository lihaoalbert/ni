"""Claude Provider — Anthropic SDK 实现

Day 4: 自动加 cache_control 标记
Day 5: 实现流式响应 stream_chat
Day 6: 重试 + 超时 — chat 走 call_with_retry + asyncio.timeout；
     stream_chat 走连接级重试 + 事件级空闲超时
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator

from anthropic import AsyncAnthropic

from app.llm.base import ChatResponse, LLMProvider, StreamEvent, ToolCall
from app.llm.retry import aiter_with_idle_timeout, call_with_retry

logger = logging.getLogger(__name__)


class ClaudeProvider(LLMProvider):
    """基于 anthropic-sdk-python 的 Provider 实现

    Day 4 关键：自动给 system 和 tools 末尾加 cache_control: ephemeral。
    Day 5 关键：流式返回 — 用 client.messages.stream()。
    Day 6 关键：
      - chat() 失败自动重试（429/529/网络）— call_with_retry
      - chat() 整体超时 — asyncio.timeout
      - stream_chat() 连接级重试 — 第一次拿到 stream cm 时重试
      - stream_chat() 事件级空闲超时 — 两个事件间隔 > idle_timeout 抛错
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        cache_control: bool = True,
        timeout: float = 30.0,
        idle_timeout: float = 30.0,
        max_retries: int = 3,
        base_url: str = "",
    ):
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY 未配置")
        # base_url 非空时走代理(MiniMax / packycode 等 coding plan)
        client_kwargs: dict = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = AsyncAnthropic(**client_kwargs)
        self.model = model
        self.cache_control = cache_control
        self.timeout = timeout
        self.idle_timeout = idle_timeout
        self.max_retries = max_retries

    @staticmethod
    def _wrap_system_for_cache(system: str) -> list[dict]:
        return [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    @staticmethod
    def _wrap_tools_for_cache(tools: list[dict]) -> list[dict]:
        if not tools:
            return tools
        wrapped = list(tools)
        wrapped[-1] = {**wrapped[-1], "cache_control": {"type": "ephemeral"}}
        return wrapped

    def _build_kwargs(
        self,
        messages: list[dict],
        system: str | None,
        max_tokens: int,
        temperature: float | None,
        tools: list[dict] | None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = (
                self._wrap_system_for_cache(system) if self.cache_control else system
            )
        if tools:
            kwargs["tools"] = (
                self._wrap_tools_for_cache(tools) if self.cache_control else tools
            )
        if temperature is not None:
            kwargs["temperature"] = temperature
        return kwargs

    async def chat(
        self,
        messages: list[dict],
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float | None = None,
        tools: list[dict] | None = None,
    ) -> ChatResponse:
        """非流式调用 — Day 1-6 用，带重试 + 总超时"""
        kwargs = self._build_kwargs(messages, system, max_tokens, temperature, tools)

        async def _do_call() -> Any:
            # asyncio.timeout 自 Python 3.11 起是标准用法
            async with asyncio.timeout(self.timeout):
                return await self._client.messages.create(**kwargs)

        response = await call_with_retry(
            _do_call,
            max_attempts=self.max_retries,
            op_name="claude.chat",
        )

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, input=dict(block.input))
                )
            else:
                logger.warning("未知的 content block type: %s", block.type)

        usage = response.usage
        return ChatResponse(
            text="".join(text_parts),
            stop_reason=response.stop_reason or "end_turn",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            model=response.model,
            tool_calls=tool_calls,
            cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        )

    async def stream_chat(
        self,
        messages: list[dict],
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float | None = None,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """流式调用 — Day 5+6

        关键事件映射：
        - content_block_start (text)        → 不直接 yield，等 delta
        - content_block_delta (text_delta)   → yield text
        - content_block_start (tool_use)     → yield tool_use_start
        - content_block_delta (input_json)   → yield tool_use_input_delta
        - message_delta                      → 捕获 stop_reason 和 usage
        - message_stop                       → yield message_stop

        Day 6 错误处理：
        - 打开 stream 时（__aenter__）失败 → 重试 N 次
        - 拿下一个事件时超过 idle_timeout → yield error 事件，结束
        - 任何时刻外部 cancel → 让 CancelledError 透传
        """
        kwargs = self._build_kwargs(messages, system, max_tokens, temperature, tools)

        # Step 1: 拿到 stream — 这一步失败可重试
        try:
            stream, cm = await self._open_stream_with_retry(kwargs)
        except asyncio.TimeoutError:
            logger.error("claude.stream open timeout after %.1fs", self.timeout)
            yield StreamEvent(type="error", error=f"open_timeout: {self.timeout}s")
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("claude.stream open failed: %s: %s", type(e).__name__, e)
            yield StreamEvent(type="error", error=f"{type(e).__name__}: {e!s}")
            return

        # Step 2: 迭代 + 事件级空闲超时
        try:
            # 累积 message_delta 的 usage 和 stop_reason
            final_stop_reason = "end_turn"
            final_input_tokens = 0
            final_output_tokens = 0
            final_cache_creation = 0
            final_cache_read = 0
            final_model = self.model

            try:
                async for event in aiter_with_idle_timeout(
                    stream, idle_timeout=self.idle_timeout, op_name="claude.stream"
                ):
                    et = getattr(event, "type", "")

                    if et == "content_block_delta":
                        delta = event.delta
                        if delta.type == "text_delta":
                            yield StreamEvent(type="text", text=delta.text)
                        elif delta.type == "input_json_delta":
                            block_index = getattr(event, "index", 0)
                            yield StreamEvent(
                                type="tool_use_input_delta",
                                tool_id=str(block_index),
                                partial_json=delta.partial_json,
                            )

                    elif et == "content_block_start":
                        block = event.content_block
                        if block.type == "tool_use":
                            yield StreamEvent(
                                type="tool_use_start",
                                tool_id=block.id,
                                tool_name=block.name,
                            )

                    elif et == "message_delta":
                        delta = event.delta
                        if getattr(delta, "stop_reason", None):
                            final_stop_reason = delta.stop_reason
                        if getattr(event, "usage", None):
                            u = event.usage
                            final_output_tokens = getattr(u, "output_tokens", 0) or 0

                    elif et == "message_start":
                        msg = getattr(event, "message", None)
                        if msg:
                            final_model = getattr(msg, "model", self.model)
                            u = getattr(msg, "usage", None)
                            if u:
                                final_input_tokens = getattr(u, "input_tokens", 0) or 0
                                final_cache_creation = (
                                    getattr(u, "cache_creation_input_tokens", 0) or 0
                                )
                                final_cache_read = (
                                    getattr(u, "cache_read_input_tokens", 0) or 0
                                )

            except asyncio.TimeoutError:
                yield StreamEvent(
                    type="error", error=f"stream_idle_timeout: {self.idle_timeout}s"
                )
                return

            # 流结束 — yield 终止事件
            yield StreamEvent(
                type="message_stop",
                stop_reason=final_stop_reason,
                input_tokens=final_input_tokens,
                output_tokens=final_output_tokens,
                cache_creation_tokens=final_cache_creation,
                cache_read_tokens=final_cache_read,
                model=final_model,
            )

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("stream_chat unexpected error")
            yield StreamEvent(type="error", error=f"{type(e).__name__}: {e!s}")
        finally:
            # 确保底层 context manager 关闭 — 不论 yield 多少次、出不出错
            try:
                await cm.__aexit__(None, None, None)
            except Exception:
                pass

    async def _open_stream_with_retry(self, kwargs: dict) -> tuple[Any, Any]:
        """打开 stream — 失败按指数退避重试

        anthropic SDK 0.111+: `messages.stream()` 返回 AsyncMessageStreamManager
        (async context manager),__aenter__ 才真正发起 HTTP,返回 AsyncMessageStream
        (有 __anext__ 的 async iterator)。我们 enter 阶段包超时,返回 (stream, cm),
        stream 用于迭代,cm 用于最后 close。
        """
        async def _do_open() -> tuple[Any, Any]:
            cm = self._client.messages.stream(**kwargs)
            async with asyncio.timeout(self.timeout):
                stream = await cm.__aenter__()
            return stream, cm

        return await call_with_retry(
            _do_open,
            max_attempts=self.max_retries,
            op_name="claude.stream.open",
        )
