"""LLM Provider 测试 — Day 1 用 mock 验证接口契约

Day 4 新增 cache_control 相关测试。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.llm.base import ChatResponse, LLMProvider
from app.llm.claude_provider import ClaudeProvider
from app.llm.factory import get_llm_provider


@pytest.mark.asyncio
async def test_claude_provider_calls_anthropic_sdk() -> None:
    """验证 ClaudeProvider 正确调用 Anthropic SDK"""
    fake_response = AsyncMock()
    fake_response.content = [AsyncMock(type="text", text="你好呀")]
    fake_response.stop_reason = "end_turn"
    fake_response.usage.input_tokens = 10
    fake_response.usage.output_tokens = 5
    fake_response.usage.cache_creation_input_tokens = 0
    fake_response.usage.cache_read_input_tokens = 0
    fake_response.model = "claude-sonnet-4-6"

    with patch("app.llm.claude_provider.AsyncAnthropic") as MockClient:
        mock_instance = MockClient.return_value
        mock_instance.messages.create = AsyncMock(return_value=fake_response)

        provider = ClaudeProvider(
            api_key="sk-test", model="claude-sonnet-4-6", cache_control=False
        )
        result = await provider.chat(
            messages=[{"role": "user", "content": "hi"}],
            system="你是 AI",
            max_tokens=100,
        )

        assert result.text == "你好呀"
        assert result.input_tokens == 10
        assert result.output_tokens == 5
        assert result.cache_creation_tokens == 0
        assert result.cache_read_tokens == 0
        assert result.stop_reason == "end_turn"
        mock_instance.messages.create.assert_called_once()

        call_kwargs = mock_instance.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "claude-sonnet-4-6"
        assert call_kwargs["system"] == "你是 AI"  # 不开 cache 时是字符串
        assert call_kwargs["max_tokens"] == 100
        assert call_kwargs["messages"] == [{"role": "user", "content": "hi"}]


@pytest.mark.asyncio
async def test_claude_provider_rejects_empty_key() -> None:
    """空 API Key 应在构造时拒绝"""
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        ClaudeProvider(api_key="", model="claude-sonnet-4-6")


@pytest.mark.asyncio
async def test_factory_returns_claude_by_default() -> None:
    """默认配置应返回 ClaudeProvider"""
    get_llm_provider.cache_clear()
    with patch("app.config.get_settings") as mock_settings:
        mock_settings.return_value.llm_provider = "claude"
        mock_settings.return_value.anthropic_api_key = "sk-test"
        mock_settings.return_value.claude_model_main = "claude-sonnet-4-6"
        mock_settings.return_value.cache_control_enabled = True

        provider = get_llm_provider()
        assert isinstance(provider, ClaudeProvider)
        assert provider.model == "claude-sonnet-4-6"


def test_provider_protocol_runtime_checkable() -> None:
    """LLMProvider 是 Protocol，可运行时检查"""

    from typing import AsyncIterator

    from app.llm.base import StreamEvent

    class FakeProvider:
        model = "fake"

        async def chat(
            self, messages, system=None, max_tokens=1024, temperature=None, tools=None
        ):
            return ChatResponse(
                text="ok",
                stop_reason="end_turn",
                input_tokens=1,
                output_tokens=1,
                model="fake",
            )

        async def stream_chat(
            self, messages, system=None, max_tokens=1024, temperature=None, tools=None
        ) -> AsyncIterator[StreamEvent]:
            yield StreamEvent(type="message_stop", stop_reason="end_turn")

    assert isinstance(FakeProvider(), LLMProvider)


# ===== Day 4: Prompt Caching 测试 =====


def test_wrap_system_for_cache_marks_last_block() -> None:
    """system 字符串应被转成带 cache_control 的 block 数组"""
    result = ClaudeProvider._wrap_system_for_cache("你是苏晚")
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["type"] == "text"
    assert result[0]["text"] == "你是苏晚"
    assert result[0]["cache_control"] == {"type": "ephemeral"}


def test_wrap_tools_for_cache_marks_last_tool() -> None:
    """最后一个 tool 应被加 cache_control"""
    tools = [
        {"name": "search_memory", "description": "..."},
        {"name": "save_fact", "description": "..."},
    ]
    wrapped = ClaudeProvider._wrap_tools_for_cache(tools)
    assert "cache_control" not in wrapped[0]
    assert wrapped[-1]["cache_control"] == {"type": "ephemeral"}
    assert wrapped[0]["name"] == "search_memory"  # 前面的不动


def test_wrap_tools_for_cache_handles_empty() -> None:
    assert ClaudeProvider._wrap_tools_for_cache([]) == []


@pytest.mark.asyncio
async def test_claude_provider_passes_cached_system() -> None:
    """启用 cache 时，system 应是 list of blocks with cache_control"""
    fake_response = AsyncMock()
    fake_response.content = [AsyncMock(type="text", text="ok")]
    fake_response.stop_reason = "end_turn"
    fake_response.usage.input_tokens = 100
    fake_response.usage.output_tokens = 5
    fake_response.usage.cache_creation_input_tokens = 80
    fake_response.usage.cache_read_input_tokens = 0
    fake_response.model = "claude-sonnet-4-6"

    with patch("app.llm.claude_provider.AsyncAnthropic") as MockClient:
        mock_instance = MockClient.return_value
        mock_instance.messages.create = AsyncMock(return_value=fake_response)

        provider = ClaudeProvider(
            api_key="sk-test", model="claude-sonnet-4-6", cache_control=True
        )
        result = await provider.chat(
            messages=[{"role": "user", "content": "hi"}],
            system="你是苏晚",
            max_tokens=100,
        )

        call_kwargs = mock_instance.messages.create.call_args.kwargs
        # system 应是 list 而非 str
        assert isinstance(call_kwargs["system"], list)
        assert call_kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert result.cache_creation_tokens == 80


@pytest.mark.asyncio
async def test_claude_provider_parses_cache_read_tokens() -> None:
    """第二次调用时 cache_read_tokens 应被正确读取"""
    fake_response = AsyncMock()
    fake_response.content = [AsyncMock(type="text", text="ok")]
    fake_response.stop_reason = "end_turn"
    fake_response.usage.input_tokens = 100
    fake_response.usage.output_tokens = 5
    fake_response.usage.cache_creation_input_tokens = 0
    fake_response.usage.cache_read_input_tokens = 80  # 命中缓存
    fake_response.model = "claude-sonnet-4-6"

    with patch("app.llm.claude_provider.AsyncAnthropic") as MockClient:
        mock_instance = MockClient.return_value
        mock_instance.messages.create = AsyncMock(return_value=fake_response)

        provider = ClaudeProvider(
            api_key="sk-test", model="claude-sonnet-4-6", cache_control=True
        )
        result = await provider.chat(
            messages=[{"role": "user", "content": "hi"}],
            system="你是苏晚",
        )

        assert result.cache_read_tokens == 80
        assert result.cache_creation_tokens == 0
