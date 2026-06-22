"""重试 + 超时 测试 — Day 6"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anthropic import APIConnectionError, APITimeoutError, RateLimitError

from app.llm.claude_provider import ClaudeProvider
from app.llm.retry import (
    aiter_with_idle_timeout,
    call_with_retry,
    is_retryable,
)


# ===== is_retryable 单元测试 =====


def test_is_retryable_on_connection_error() -> None:
    assert is_retryable(APIConnectionError(request=MagicMock()))


def test_is_retryable_on_timeout_error() -> None:
    assert is_retryable(APITimeoutError(request=MagicMock()))


def test_is_retryable_on_rate_limit() -> None:
    # RateLimitError status_code=429
    err = RateLimitError(
        response=MagicMock(status_code=429, headers={}),
        body=None,
        message="rate limited",
    )
    assert is_retryable(err)


def test_is_retryable_on_529_overloaded_via_status() -> None:
    """529 没有专门的异常类，但 529 在 status code 白名单里"""
    from anthropic import APIStatusError

    err = APIStatusError(
        message="overloaded",
        response=MagicMock(status_code=529, headers={}),
        body=None,
    )
    assert is_retryable(err)


def test_is_retryable_false_on_400_bad_request() -> None:
    from anthropic import BadRequestError

    err = BadRequestError(
        message="bad",
        response=MagicMock(status_code=400, headers={}),
        body=None,
    )
    assert not is_retryable(err)


def test_is_retryable_false_on_401_auth() -> None:
    from anthropic import AuthenticationError

    err = AuthenticationError(
        message="bad key",
        response=MagicMock(status_code=401, headers={}),
        body=None,
    )
    assert not is_retryable(err)


def test_is_retryable_false_on_cancelled() -> None:
    """asyncio.CancelledError 永远不重试"""
    assert not is_retryable(asyncio.CancelledError())


def test_is_retryable_false_on_value_error() -> None:
    assert not is_retryable(ValueError("nope"))


# ===== call_with_retry 单元测试 =====


@pytest.mark.asyncio
async def test_call_with_retry_succeeds_first_try() -> None:
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        return "ok"

    result = await call_with_retry(factory, max_attempts=3, base_delay=0.001)
    assert result == "ok"
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_call_with_retry_recovers_after_transient() -> None:
    """第一次连接错误，第二次成功"""
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        if calls["n"] < 2:
            raise APIConnectionError(request=MagicMock())
        return "ok"

    result = await call_with_retry(factory, max_attempts=3, base_delay=0.001)
    assert result == "ok"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_call_with_retry_gives_up_after_max() -> None:
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        raise APIConnectionError(request=MagicMock())

    with pytest.raises(APIConnectionError):
        await call_with_retry(factory, max_attempts=3, base_delay=0.001)
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_call_with_retry_no_retry_on_4xx() -> None:
    """400 类错误应该立即抛出，不重试"""
    from anthropic import BadRequestError

    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        raise BadRequestError(
            message="bad",
            response=MagicMock(status_code=400, headers={}),
            body=None,
        )

    with pytest.raises(BadRequestError):
        await call_with_retry(factory, max_attempts=3, base_delay=0.001)
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_call_with_retry_cancelled_not_retried() -> None:
    """任务取消不会被吞掉"""
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await call_with_retry(factory, max_attempts=3, base_delay=0.001)
    assert calls["n"] == 1


# ===== aiter_with_idle_timeout 单元测试 =====


@pytest.mark.asyncio
async def test_aiter_with_idle_timeout_yields_all() -> None:
    async def gen():
        yield 1
        yield 2
        yield 3

    out = []
    async for x in aiter_with_idle_timeout(gen(), idle_timeout=0.5):
        out.append(x)
    assert out == [1, 2, 3]


@pytest.mark.asyncio
async def test_aiter_with_idle_timeout_raises_on_slow() -> None:
    """下一事件迟迟不到 → 抛 TimeoutError"""

    async def gen():
        yield 1
        await asyncio.sleep(1.0)  # 远大于 idle_timeout
        yield 2  # noqa: F821 — 实际不会执行

    with pytest.raises(asyncio.TimeoutError):
        async for _ in aiter_with_idle_timeout(gen(), idle_timeout=0.05):
            pass


# ===== ClaudeProvider.chat() 集成测试 =====


@pytest.mark.asyncio
async def test_chat_retries_on_rate_limit() -> None:
    """chat() 在 429 时应自动重试"""
    fake = AsyncMock()
    fake.content = [AsyncMock(type="text", text="ok")]
    fake.stop_reason = "end_turn"
    fake.usage.input_tokens = 10
    fake.usage.output_tokens = 2
    fake.usage.cache_creation_input_tokens = 0
    fake.usage.cache_read_input_tokens = 0
    fake.model = "test"

    rate_err = RateLimitError(
        response=MagicMock(status_code=429, headers={}),
        body=None,
        message="rate limited",
    )

    call_count = {"n": 0}

    async def flaky_create(**kw):
        call_count["n"] += 1
        if call_count["n"] < 2:
            raise rate_err
        return fake

    with patch("app.llm.claude_provider.AsyncAnthropic") as MockClient:
        instance = MockClient.return_value
        instance.messages.create = flaky_create

        provider = ClaudeProvider(
            api_key="sk-test",
            model="claude-sonnet-4-6",
            cache_control=False,
            timeout=5.0,
            max_retries=3,
        )
        result = await provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert result.text == "ok"
    assert call_count["n"] == 2  # 第一次失败，第二次成功


@pytest.mark.asyncio
async def test_chat_total_timeout() -> None:
    """整个 chat() 超过 self.timeout 应抛 TimeoutError"""

    async def slow_create(**kw):
        await asyncio.sleep(0.5)
        return AsyncMock()

    with patch("app.llm.claude_provider.AsyncAnthropic") as MockClient:
        instance = MockClient.return_value
        instance.messages.create = slow_create

        provider = ClaudeProvider(
            api_key="sk-test",
            model="claude-sonnet-4-6",
            cache_control=False,
            timeout=0.05,  # 50ms — 远小于 0.5s
            max_retries=1,  # 关掉重试看超时本身
        )
        with pytest.raises(asyncio.TimeoutError):
            await provider.chat(messages=[{"role": "user", "content": "hi"}])


# ===== ClaudeProvider.stream_chat() 集成测试 =====


def _make_event(type_: str, **kw):
    ev = MagicMock()
    ev.type = type_
    for k, v in kw.items():
        setattr(ev, k, v)
    return ev


class _FakeStream:
    """模拟 anthropic 的 stream context manager

    block_on_get: 设为大于 0 时,每次 __anext__ 在拿下一个事件前会 sleep 这么久 —
    用来模拟"流卡住"场景。
    """

    def __init__(
        self,
        events: list,
        open_delay: float = 0.0,
        block_on_get: float = 0.0,
    ):
        self._events = events
        self._open_delay = open_delay
        self._block_on_get = block_on_get
        self._idx = 0
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        if self._open_delay:
            await asyncio.sleep(self._open_delay)
        self.entered = True
        return self

    async def __aexit__(self, *args):
        self.exited = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._block_on_get and self._idx < len(self._events):
            await asyncio.sleep(self._block_on_get)
        if self._idx >= len(self._events):
            raise StopAsyncIteration
        ev = self._events[self._idx]
        self._idx += 1
        return ev


@pytest.mark.asyncio
async def test_stream_chat_emits_error_on_idle_timeout() -> None:
    """__anext__ 等不到下一事件超 idle_timeout → yield error 事件"""
    # 第二个 __anext__ 永远不返回 — 模拟流卡住
    block = asyncio.Event()

    msg_start = _make_event(
        "message_start",
        message=MagicMock(model="m", usage=MagicMock(
            input_tokens=1, cache_creation_input_tokens=0, cache_read_input_tokens=0
        )),
    )

    class _BlockingStream:
        def __init__(self):
            self.entered = False
            self.exited = False
            self._first_returned = False

        async def __aenter__(self):
            self.entered = True
            return self

        async def __aexit__(self, *args):
            self.exited = True

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._first_returned:
                self._first_returned = True
                return msg_start
            await block.wait()  # 永远等
            raise AssertionError("should not get here")

    fake = _BlockingStream()

    with patch("app.llm.claude_provider.AsyncAnthropic") as MockClient:
        instance = MockClient.return_value
        instance.messages.stream = lambda **kw: fake

        provider = ClaudeProvider(
            api_key="sk-test",
            model="m",
            cache_control=False,
            timeout=5.0,
            idle_timeout=0.05,
            max_retries=1,
        )

        out = []
        async for ev in provider.stream_chat(messages=[{"role": "user", "content": "hi"}]):
            out.append(ev)
        # 解除 block 以便 stream 清理
        block.set()

    types = [e.type for e in out]
    assert "message_stop" in types or "error" in types
    err_event = next((e for e in out if e.type == "error"), None)
    assert err_event is not None, f"expected error event, got {out}"
    assert "stream_idle_timeout" in err_event.error
    assert fake.exited


@pytest.mark.asyncio
async def test_stream_chat_emits_error_on_open_timeout() -> None:
    """__aenter__ 阶段超过 timeout → yield error"""
    events: list = []
    fake = _FakeStream(events, open_delay=0.5)

    with patch("app.llm.claude_provider.AsyncAnthropic") as MockClient:
        instance = MockClient.return_value
        instance.messages.stream = lambda **kw: fake

        provider = ClaudeProvider(
            api_key="sk-test",
            model="m",
            cache_control=False,
            timeout=0.05,
            idle_timeout=5.0,
            max_retries=1,
        )

        out = []
        async for ev in provider.stream_chat(messages=[{"role": "user", "content": "hi"}]):
            out.append(ev)

    assert len(out) == 1
    assert out[0].type == "error"
    assert "open_timeout" in out[0].error


@pytest.mark.asyncio
async def test_stream_chat_retry_on_open_failure() -> None:
    """__aenter__ 阶段 RateLimitError → 重试"""
    good_events = [
        _make_event("message_start", message=MagicMock(model="m", usage=MagicMock(
            input_tokens=1, cache_creation_input_tokens=0, cache_read_input_tokens=0
        ))),
        _make_event("content_block_delta", delta=MagicMock(type="text_delta", text="ok")),
    ]
    good = _FakeStream(good_events)
    rate_err = RateLimitError(
        response=MagicMock(status_code=429, headers={}),
        body=None,
        message="rate limited",
    )

    call_count = {"n": 0}

    def make_stream(**kw):
        call_count["n"] += 1
        if call_count["n"] < 2:
            return _FakeStreamWithOpenError(rate_err)
        return good

    class _FakeStreamWithOpenError:
        def __init__(self, exc):
            self._exc = exc

        async def __aenter__(self):
            raise self._exc

        async def __aexit__(self, *args):
            pass

    with patch("app.llm.claude_provider.AsyncAnthropic") as MockClient:
        instance = MockClient.return_value
        instance.messages.stream = make_stream

        provider = ClaudeProvider(
            api_key="sk-test",
            model="m",
            cache_control=False,
            timeout=5.0,
            idle_timeout=5.0,
            max_retries=3,
        )

        out = []
        async for ev in provider.stream_chat(messages=[{"role": "user", "content": "hi"}]):
            out.append(ev)

    assert call_count["n"] == 2  # 第一次 429，第二次成功
    text_events = [e for e in out if e.type == "text"]
    assert text_events[0].text == "ok"
