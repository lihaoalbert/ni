"""错误映射 + 端点级集成测试 — Day 6"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
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

from app.api.errors import map_exception, to_http_exception, to_sse_error_event


# ===== map_exception 单元测试 =====


def _mock_response(status_code: int) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.headers = {}
    return r


def test_map_timeout_error() -> None:
    e = APITimeoutError(request=MagicMock())
    m = map_exception(e)
    assert m.status_code == 504
    assert m.kind == "timeout"


def test_map_connection_error() -> None:
    e = APIConnectionError(request=MagicMock())
    m = map_exception(e)
    assert m.status_code == 502
    assert m.kind == "network"


def test_map_rate_limit_error() -> None:
    e = RateLimitError(response=_mock_response(429), body=None, message="rate")
    m = map_exception(e)
    assert m.status_code == 503
    assert m.kind == "upstream_429"
    assert m.upstream_status == 429


def test_map_auth_error() -> None:
    e = AuthenticationError(response=_mock_response(401), body=None, message="auth")
    m = map_exception(e)
    assert m.status_code == 502
    assert m.kind == "auth"
    assert m.upstream_status == 401


def test_map_permission_error() -> None:
    e = PermissionDeniedError(response=_mock_response(403), body=None, message="forbidden")
    m = map_exception(e)
    assert m.status_code == 502
    assert m.kind == "permission"


def test_map_not_found_error() -> None:
    e = NotFoundError(response=_mock_response(404), body=None, message="not found")
    m = map_exception(e)
    assert m.status_code == 502
    assert m.kind == "not_found"


def test_map_bad_request_error() -> None:
    e = BadRequestError(response=_mock_response(400), body=None, message="bad")
    m = map_exception(e)
    assert m.status_code == 502
    assert m.kind == "bad_request"


def test_map_529_overloaded() -> None:
    """529 没有专门异常类 — 通过通用 APIStatusError 路径处理"""
    e = APIStatusError(message="overloaded", response=_mock_response(529), body=None)
    m = map_exception(e)
    assert m.status_code == 502
    assert m.kind == "overloaded"
    assert m.upstream_status == 529


def test_map_500_server_error() -> None:
    e = APIStatusError(message="oops", response=_mock_response(500), body=None)
    m = map_exception(e)
    assert m.status_code == 502
    assert m.kind == "upstream_5xx"


def test_map_asyncio_timeout() -> None:
    m = map_exception(asyncio.TimeoutError())
    assert m.status_code == 504
    assert m.kind == "timeout"


def test_map_unknown_exception() -> None:
    m = map_exception(ValueError("weird"))
    assert m.status_code == 500
    assert m.kind == "internal"


# ===== to_http_exception =====


def test_to_http_exception_includes_kind_and_message() -> None:
    e = RateLimitError(response=_mock_response(429), body=None, message="rate")
    he = to_http_exception(e)
    assert isinstance(he, HTTPException)
    assert he.status_code == 503
    assert he.detail["kind"] == "upstream_429"
    assert he.detail["upstream_status"] == 429
    assert "AI 服务限流" in he.detail["message"]


# ===== to_sse_error_event =====


def test_sse_event_format() -> None:
    e = RateLimitError(response=_mock_response(429), body=None, message="rate")
    ev = to_sse_error_event(e)
    assert ev["type"] == "error"
    assert ev["kind"] == "upstream_429"
    assert ev["upstream_status"] == 429
    assert "限流" in ev["error"]


# ===== /chat 端点错误测试 =====


def test_chat_returns_503_on_rate_limit_exhausted() -> None:
    """模拟 LLM 多次重试后仍然 429 — 端点应返回 503"""
    from app.api.chat import get_agent
    from app.main import app
    from app.memory.store import reset_conversation_store, reset_memory_store

    rate_err = RateLimitError(response=_mock_response(429), body=None, message="rate")

    async def boom(**kw):
        raise rate_err

    mock_agent = MagicMock()
    mock_agent.run = boom

    reset_memory_store()
    reset_conversation_store()
    app.dependency_overrides[get_agent] = lambda: mock_agent
    try:
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post(
            "/chat",
            json={"user_id": "u1", "character_id": "suwan", "message": "hi"},
        )
        assert resp.status_code == 503
        body = resp.json()
        assert body["detail"]["kind"] == "upstream_429"
        assert "限流" in body["detail"]["message"]
    finally:
        app.dependency_overrides.clear()


def test_chat_returns_504_on_timeout() -> None:
    from app.api.chat import get_agent
    from app.main import app
    from app.memory.store import reset_conversation_store, reset_memory_store

    async def slow(**kw):
        raise asyncio.TimeoutError()

    mock_agent = MagicMock()
    mock_agent.run = slow

    reset_memory_store()
    reset_conversation_store()
    app.dependency_overrides[get_agent] = lambda: mock_agent
    try:
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post(
            "/chat",
            json={"user_id": "u1", "character_id": "suwan", "message": "hi"},
        )
        assert resp.status_code == 504
        body = resp.json()
        assert body["detail"]["kind"] == "timeout"
    finally:
        app.dependency_overrides.clear()


def test_chat_returns_404_on_unknown_character() -> None:
    from app.api.chat import get_agent
    from app.main import app
    from app.memory.store import reset_conversation_store, reset_memory_store

    async def ok(**kw):
        return MagicMock(
            text="x", iterations=1, tool_calls=[], input_tokens=1, output_tokens=1,
            cache_creation_tokens=0, cache_read_tokens=0, model="m",
        )

    mock_agent = MagicMock()
    mock_agent.run = ok

    reset_memory_store()
    reset_conversation_store()
    app.dependency_overrides[get_agent] = lambda: mock_agent
    try:
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post(
            "/chat",
            json={"user_id": "u1", "character_id": "nonexistent", "message": "hi"},
        )
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.clear()


# ===== /chat/stream 端点错误测试 =====


def test_chat_stream_yields_friendly_error_event_on_llm_failure() -> None:
    """/chat/stream 端点：LLM 抛错 → 客户端收到 SSE 友好 error 事件"""
    from app.api.chat import get_agent
    from app.main import app
    from app.memory.store import reset_conversation_store, reset_memory_store

    rate_err = RateLimitError(response=_mock_response(429), body=None, message="rate")

    async def stream_with_error(**kw):
        # 模拟 agent 内部异常（不是 yield error 事件，而是真抛）
        raise rate_err
        yield  # 让它成为 generator  # noqa: F401

    mock_agent = MagicMock()

    def fake_stream(**kw):
        return stream_with_error(**kw)

    mock_agent.run_stream = fake_stream

    reset_memory_store()
    reset_conversation_store()
    app.dependency_overrides[get_agent] = lambda: mock_agent
    try:
        from fastapi.testclient import TestClient

        client = TestClient(app)
        with client.stream(
            "POST",
            "/chat/stream",
            json={"user_id": "u1", "character_id": "suwan", "message": "hi"},
        ) as resp:
            assert resp.status_code == 200
            chunks = list(resp.iter_lines())
        data_lines = [c for c in chunks if c.startswith("data: ")]
        assert len(data_lines) >= 1
        import json
        last = json.loads(data_lines[-1][len("data: "):])
        assert last["type"] == "error"
        assert last["kind"] == "upstream_429"
        assert "限流" in last["error"]
    finally:
        app.dependency_overrides.clear()
