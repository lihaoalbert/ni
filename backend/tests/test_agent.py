"""Agent 模块测试 — tools + runtime

测试策略：mock LLMProvider 模拟不同 stop_reason 序列，
验证 AgentRuntime 正确循环、调用工具、收集结果。
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.agent.runtime import AgentRuntime
from app.agent.tools import ToolRegistry
from app.llm.base import ChatResponse, ToolCall
from app.memory.schemas import FactCategory
from app.memory.store import InMemoryStore


def _resp_text(text: str, **kw) -> ChatResponse:
    return ChatResponse(
        text=text,
        stop_reason="end_turn",
        input_tokens=10,
        output_tokens=5,
        model="test",
        **kw,
    )


def _resp_tool(tool_calls: list[ToolCall], text: str = "") -> ChatResponse:
    return ChatResponse(
        text=text,
        stop_reason="tool_use",
        input_tokens=10,
        output_tokens=5,
        model="test",
        tool_calls=tool_calls,
    )


def _tc(name: str, input_: dict, id_: str = "tu_1") -> ToolCall:
    return ToolCall(id=id_, name=name, input=input_)


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def registry(store: InMemoryStore) -> ToolRegistry:
    return ToolRegistry(store)


# ===== Tool Registry 测试 =====


@pytest.mark.asyncio
async def test_save_fact_tool_executor(store: InMemoryStore, registry: ToolRegistry) -> None:
    result = await registry.execute(
        "save_fact",
        {"content": "用户叫小明", "category": "basic"},
        user_id="u1",
    )
    assert result["status"] == "saved"
    assert result["content"] == "用户叫小明"
    assert result["category"] == "basic"
    assert await store.count("u1") == 1


@pytest.mark.asyncio
async def test_search_memory_tool_executor(store: InMemoryStore, registry: ToolRegistry) -> None:
    await store.add("u1", FactCategory.WORK, "用户是软件工程师")
    result = await registry.execute(
        "search_memory",
        {"query": "工程师"},
        user_id="u1",
    )
    assert result["count"] == 1
    assert "软件工程师" in result["facts"][0]["content"]


@pytest.mark.asyncio
async def test_list_user_facts_tool(store: InMemoryStore, registry: ToolRegistry) -> None:
    await store.add("u1", FactCategory.BASIC, "a")
    await store.add("u1", FactCategory.WORK, "b")
    result = await registry.execute("list_user_facts", {}, user_id="u1")
    assert result["count"] == 2


@pytest.mark.asyncio
async def test_forget_fact_tool(store: InMemoryStore, registry: ToolRegistry) -> None:
    f = await store.add("u1", FactCategory.BASIC, "要忘掉")
    result = await registry.execute("forget_fact", {"fact_id": f.id}, user_id="u1")
    assert result["status"] == "forgotten"
    assert await store.count("u1") == 0


@pytest.mark.asyncio
async def test_unknown_tool_returns_error(registry: ToolRegistry) -> None:
    result = await registry.execute("nonexistent", {}, user_id="u1")
    assert "error" in result


# ===== Agent Runtime 测试 =====


@pytest.mark.asyncio
async def test_agent_ends_immediately_on_end_turn(registry: ToolRegistry) -> None:
    provider = AsyncMock()
    provider.chat = AsyncMock(return_value=_resp_text("你好呀"))
    agent = AgentRuntime(provider, registry)

    result = await agent.run(system="你是苏晚", user_message="hi", user_id="u1")
    assert result.text == "你好呀"
    assert result.iterations == 1
    assert result.tool_calls == []
    assert provider.chat.call_count == 1


@pytest.mark.asyncio
async def test_agent_loops_on_tool_use_then_ends(registry: ToolRegistry) -> None:
    provider = AsyncMock()
    # 第一次：tool_use，第二次：end_turn
    provider.chat = AsyncMock(
        side_effect=[
            _resp_tool(
                [_tc("save_fact", {"content": "用户叫小明", "category": "basic"})],
                text="让我记一下。",
            ),
            _resp_text("好的，我记住了。"),
        ]
    )
    agent = AgentRuntime(provider, registry)

    result = await agent.run(system="...", user_message="hi", user_id="u1")
    assert result.text == "好的，我记住了。"
    assert result.iterations == 2
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["name"] == "save_fact"
    assert provider.chat.call_count == 2


@pytest.mark.asyncio
async def test_agent_handles_multi_tool_in_one_turn(registry: ToolRegistry) -> None:
    provider = AsyncMock()
    provider.chat = AsyncMock(
        side_effect=[
            _resp_tool(
                [
                    _tc("save_fact", {"content": "用户叫小明", "category": "basic"}, id_="t1"),
                    _tc("save_fact", {"content": "用户是工程师", "category": "work"}, id_="t2"),
                ]
            ),
            _resp_text("记下了。"),
        ]
    )
    agent = AgentRuntime(provider, registry)

    result = await agent.run(system="...", user_message="hi", user_id="u1")
    assert result.iterations == 2
    assert len(result.tool_calls) == 2


@pytest.mark.asyncio
async def test_agent_stops_at_max_iterations(registry: ToolRegistry) -> None:
    provider = AsyncMock()
    # 永远 tool_use — 触发 max_iterations
    provider.chat = AsyncMock(
        return_value=_resp_tool([_tc("save_fact", {"content": "x", "category": "basic"})])
    )
    agent = AgentRuntime(provider, registry, max_iterations=3)

    result = await agent.run(system="...", user_message="hi", user_id="u1")
    assert result.iterations == 3
    assert provider.chat.call_count == 3


@pytest.mark.asyncio
async def test_agent_passes_tools_in_call(registry: ToolRegistry) -> None:
    provider = AsyncMock()
    provider.chat = AsyncMock(return_value=_resp_text("ok"))
    agent = AgentRuntime(provider, registry)

    await agent.run(system="...", user_message="hi", user_id="u1")
    call_kwargs = provider.chat.call_args.kwargs
    assert "tools" in call_kwargs
    assert len(call_kwargs["tools"]) == 4  # 4 个工具


@pytest.mark.asyncio
async def test_agent_includes_tool_use_blocks_in_messages(registry: ToolRegistry) -> None:
    provider = AsyncMock()
    provider.chat = AsyncMock(
        side_effect=[
            _resp_tool(
                [_tc("save_fact", {"content": "测试", "category": "basic"})],
                text="嗯。",
            ),
            _resp_text("ok"),
        ]
    )
    agent = AgentRuntime(provider, registry)
    await agent.run(system="...", user_message="hi", user_id="u1")

    # 第二次调用时，messages 应含 assistant tool_use + user tool_result
    second_call_messages = provider.chat.call_args_list[1].kwargs["messages"]

    def _has_block(msg: dict, block_type: str) -> bool:
        content = msg["content"]
        if not isinstance(content, list):
            return False
        return any(isinstance(b, dict) and b.get("type") == block_type for b in content)

    assert any(
        msg["role"] == "assistant" and _has_block(msg, "tool_use")
        for msg in second_call_messages
    )
    assert any(
        msg["role"] == "user" and _has_block(msg, "tool_result")
        for msg in second_call_messages
    )
