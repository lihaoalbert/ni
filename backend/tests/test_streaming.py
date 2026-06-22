"""流式响应测试 — Day 5"""
from __future__ import annotations

from typing import AsyncIterator
from unittest.mock import AsyncMock

import pytest

from app.agent.runtime import AgentRuntime
from app.agent.tools import ToolRegistry
from app.llm.base import ChatResponse, StreamEvent
from app.memory.schemas import FactCategory
from app.memory.store import InMemoryStore


def _stream(*events: StreamEvent) -> AsyncIterator[StreamEvent]:
    async def gen():
        for e in events:
            yield e

    return gen()


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def registry(store: InMemoryStore) -> ToolRegistry:
    return ToolRegistry(store)


# ===== AgentRuntime.run_stream 测试 =====


@pytest.mark.asyncio
async def test_run_stream_yields_text_then_done(registry: ToolRegistry) -> None:
    provider = AsyncMock()
    provider.model = "test"
    provider.stream_chat = lambda **kw: _stream(
        StreamEvent(type="text", text="你"),
        StreamEvent(type="text", text="好"),
        StreamEvent(
            type="message_stop",
            stop_reason="end_turn",
            input_tokens=10,
            output_tokens=2,
            model="test",
        ),
    )

    agent = AgentRuntime(provider, registry)
    events = []
    async for ev in agent.run_stream(system="...", user_message="hi", user_id="u1"):
        events.append(ev)

    types = [e["type"] for e in events]
    assert "text" in types
    assert "done" in types
    assert types[-1] == "done"

    # 累积文本
    done_event = next(e for e in events if e["type"] == "done")
    assert done_event["text"] == "你好"
    assert done_event["iterations"] == 1


@pytest.mark.asyncio
async def test_run_stream_handles_tool_use(registry: ToolRegistry) -> None:
    provider = AsyncMock()
    provider.model = "test"
    provider.stream_chat = lambda **kw: _stream(
        StreamEvent(type="text", text="让我记一下。"),
        StreamEvent(type="tool_use_start", tool_id="t1", tool_name="save_fact"),
        StreamEvent(type="tool_use_input_delta", tool_id="t1", partial_json='{"co'),
        StreamEvent(type="tool_use_input_delta", tool_id="t1", partial_json='ntent":"x","category":"basic"}'),
        StreamEvent(
            type="message_stop",
            stop_reason="tool_use",
            input_tokens=20,
            output_tokens=10,
            model="test",
        ),
    )
    agent = AgentRuntime(provider, registry, max_iterations=2)
    events = []
    async for ev in agent.run_stream(system="...", user_message="记住 x", user_id="u1"):
        events.append(ev)

    types = [e["type"] for e in events]
    assert "tool_use_start" in types
    assert "tool_use_input_delta" in types
    assert "tool_result" in types
    assert "done" in types

    # 工具结果应被 yield
    tr = next(e for e in events if e["type"] == "tool_result")
    assert tr["tool_id"] == "t1"
    assert tr["result"]["status"] == "saved"

    # 记忆真的存了
    facts = await registry.memory.list_all("u1")
    assert len(facts) == 1
    assert facts[0].content == "x"


@pytest.mark.asyncio
async def test_run_stream_propagates_error(registry: ToolRegistry) -> None:
    provider = AsyncMock()
    provider.model = "test"
    provider.stream_chat = lambda **kw: _stream(
        StreamEvent(type="error", error="boom"),
    )
    agent = AgentRuntime(provider, registry)
    events = []
    async for ev in agent.run_stream(system="...", user_message="hi", user_id="u1"):
        events.append(ev)
    assert events[-1]["type"] == "error"


@pytest.mark.asyncio
async def test_run_stream_multi_iteration_with_cache(registry: ToolRegistry) -> None:
    """两轮迭代 — 第一轮 tool_use，第二轮 end_turn — cache tokens 累加"""
    provider = AsyncMock()
    provider.model = "test"

    call_count = {"n": 0}

    def fake_stream(**kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _stream(
                StreamEvent(type="text", text="嗯"),
                StreamEvent(type="tool_use_start", tool_id="t1", tool_name="save_fact"),
                StreamEvent(
                    type="tool_use_input_delta",
                    tool_id="t1",
                    partial_json='{"content":"test","category":"basic"}',
                ),
                StreamEvent(
                    type="message_stop",
                    stop_reason="tool_use",
                    input_tokens=20,
                    output_tokens=5,
                    cache_creation_tokens=10,
                    cache_read_tokens=0,
                    model="test",
                ),
            )
        else:
            return _stream(
                StreamEvent(type="text", text="记下了"),
                StreamEvent(
                    type="message_stop",
                    stop_reason="end_turn",
                    input_tokens=25,
                    output_tokens=3,
                    cache_creation_tokens=0,
                    cache_read_tokens=15,
                    model="test",
                ),
            )

    provider.stream_chat = fake_stream
    agent = AgentRuntime(provider, registry)
    events = []
    async for ev in agent.run_stream(system="...", user_message="hi", user_id="u1"):
        events.append(ev)

    done = next(e for e in events if e["type"] == "done")
    assert done["iterations"] == 2
    assert done["cache_creation_tokens"] == 10  # 累加
    assert done["cache_read_tokens"] == 15
    assert done["input_tokens"] == 45  # 20 + 25
    assert done["output_tokens"] == 8


# ===== FastAPI 端点测试 =====


@pytest.fixture
def mock_streaming_agent() -> AsyncMock:
    agent = AsyncMock()

    async def fake_stream(**kw):
        yield {"type": "text", "text": "你"}
        yield {"type": "text", "text": "好"}
        yield {
            "type": "done",
            "text": "你好",
            "iterations": 1,
            "tool_calls": [],
            "input_tokens": 10,
            "output_tokens": 2,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
            "model": "test",
        }

    agent.run_stream = fake_stream
    return agent


def test_chat_stream_returns_sse_format(mock_streaming_agent) -> None:
    from app.api.chat import get_agent
    from app.main import app
    from app.memory.store import reset_conversation_store, reset_memory_store

    reset_memory_store()
    reset_conversation_store()
    app.dependency_overrides[get_agent] = lambda: mock_streaming_agent

    try:
        from fastapi.testclient import TestClient

        client = TestClient(app)
        with client.stream(
            "POST",
            "/chat/stream",
            json={"user_id": "u1", "character_id": "suwan", "message": "hi"},
        ) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            chunks = list(resp.iter_lines())
        # 应该有 3 个 data 行
        data_lines = [c for c in chunks if c.startswith("data: ")]
        assert len(data_lines) == 3
        # 第一条是 text
        assert '"text"' in data_lines[0]
        # 最后一条是 done
        assert '"done"' in data_lines[-1]
    finally:
        app.dependency_overrides.clear()
