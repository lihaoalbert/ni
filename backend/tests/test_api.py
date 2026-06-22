"""/chat 端点测试 — Day 3：mock AgentRuntime"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.agent.runtime import AgentResult
from app.api.chat import get_agent
from app.main import app
from app.memory.store import reset_conversation_store, reset_memory_store


@pytest.fixture(autouse=True)
def _reset_stores():
    reset_memory_store()
    reset_conversation_store()
    yield
    reset_memory_store()
    reset_conversation_store()


@pytest.fixture
def mock_agent() -> AsyncMock:
    agent = AsyncMock()
    agent.run = AsyncMock(
        return_value=AgentResult(
            text="嗯，我在这里。",
            iterations=1,
            tool_calls=[],
            input_tokens=12,
            output_tokens=6,
            model="claude-sonnet-4-6",
            cache_creation_tokens=0,
            cache_read_tokens=0,
        )
    )
    return agent


@pytest.fixture(autouse=True)
def _override_agent(mock_agent: AsyncMock):
    """替换 AgentRuntime 依赖"""
    app.dependency_overrides[get_agent] = lambda: mock_agent
    yield
    app.dependency_overrides.clear()


def test_health_endpoint() -> None:
    client = TestClient(app)
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "provider" in data


def test_root_endpoint() -> None:
    client = TestClient(app)
    res = client.get("/")
    assert res.status_code == 200
    assert res.json()["name"] == "Companion AI Backend"


def test_chat_returns_reply(mock_agent: AsyncMock) -> None:
    client = TestClient(app)
    res = client.post(
        "/chat",
        json={"user_id": "u1", "character_id": "suwan", "message": "你好"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["reply"] == "嗯，我在这里。"
    assert data["iterations"] == 1
    assert data["memory_ops"] == []
    mock_agent.run.assert_called_once()


def test_chat_returns_404_for_missing_character(mock_agent: AsyncMock) -> None:
    client = TestClient(app)
    res = client.post(
        "/chat",
        json={"user_id": "u1", "character_id": "nonexistent", "message": "你好"},
    )
    assert res.status_code == 404
    assert "nonexistent" in res.json()["detail"]


def test_chat_validates_message_length() -> None:
    client = TestClient(app)
    res = client.post(
        "/chat",
        json={"user_id": "u1", "message": ""},
    )
    assert res.status_code == 422


def test_chat_includes_tool_calls_in_response(mock_agent: AsyncMock) -> None:
    """如果 agent 调用了工具，memory_ops 应出现在响应里"""
    mock_agent.run = AsyncMock(
        return_value=AgentResult(
            text="我记住了。",
            iterations=2,
            tool_calls=[
                {
                    "name": "save_fact",
                    "input": {"content": "用户叫小明", "category": "basic"},
                    "result": {"id": "abc", "status": "saved"},
                }
            ],
            input_tokens=100,
            output_tokens=20,
            model="claude-sonnet-4-6",
            cache_creation_tokens=0,
            cache_read_tokens=80,
        )
    )
    client = TestClient(app)
    res = client.post(
        "/chat",
        json={"user_id": "u1", "character_id": "suwan", "message": "我叫小明"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["iterations"] == 2
    assert len(data["memory_ops"]) == 1
    assert data["memory_ops"][0]["name"] == "save_fact"
    assert data["cache_read_tokens"] == 80
