"""LLM Provider 抽象接口

Day 5 新增流式接口 — 用 AsyncIterator[StreamEvent] 解耦。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol, runtime_checkable


@dataclass
class ToolCall:
    """模型请求调用一个工具"""

    id: str
    name: str
    input: dict


@dataclass
class ChatResponse:
    """统一的聊天响应 — 与具体 SDK 解耦

    Day 3 新增 tool_calls 字段：
    - 当 stop_reason == "tool_use" 时，text 通常是模型对工具调用的"说明"
    - tool_calls 列表里是要执行的具体工具

    Day 4 新增 cache 字段：
    - cache_creation_tokens: 本次为创建缓存付的 tokens（首次会高）
    - cache_read_tokens: 本次从缓存读的 tokens（命中时高，便宜 90%）
    """

    text: str
    stop_reason: str
    input_tokens: int
    output_tokens: int
    model: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0


# ===== Day 5: 流式事件 =====


@dataclass
class StreamEvent:
    """统一的流式事件 — 与具体 SDK 解耦

    事件类型：
    - text: 文本增量（每次一小段）
    - tool_use_start: 工具调用开始（含 id 和 name）
    - tool_use_input_delta: 工具输入 JSON 增量（要累积）
    - message_stop: 一轮结束（含 stop_reason 和 token 计量）
    - error: 出错
    """

    type: str
    text: str = ""
    tool_id: str = ""
    tool_name: str = ""
    partial_json: str = ""
    stop_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    model: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v != "" or k in ("type",)}


@runtime_checkable
class LLMProvider(Protocol):
    """所有 LLM Provider 必须实现的接口

    Day 5 新增 stream_chat：
    - 流式返回 AsyncIterator[StreamEvent]
    - 业务侧统一消费，不直接依赖 SDK
    """

    model: str

    async def chat(
        self,
        messages: list[dict],
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float | None = None,
        tools: list[dict] | None = None,
    ) -> ChatResponse: ...

    async def stream_chat(
        self,
        messages: list[dict],
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float | None = None,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]: ...
