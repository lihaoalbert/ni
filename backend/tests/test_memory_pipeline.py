"""记忆管道测试 — Phase 1 demo

Loop 1: /chat 应触发 extractor（骨架版,extractor 可以是空的）
Loop 2: extractor 应真的从对话中提取 fact 并入库
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest

from app.api.chat import get_agent
from app.main import app
from app.memory.store import reset_conversation_store, reset_memory_store

# ===== Loop 1: extractor 被 /chat 触发 =====


@dataclass
class _RecordingExtractor:
    """记录调用次数和参数的 extractor — 用于测试"""
    call_count: int = 0
    last_user_id: str = ""
    last_turns: list[dict] = field(default_factory=list)

    async def extract(self, user_id: str, character_id: str, turns: list[dict]) -> list[dict]:
        self.call_count += 1
        self.last_user_id = user_id
        self.last_turns = list(turns)
        return []


@pytest.mark.asyncio
async def test_chat_triggers_extractor_after_response() -> None:
    """/chat 完成后,extractor.extract 会被调用一次"""
    from fastapi.testclient import TestClient

    from app.agent.runtime import AgentResult
    from app.api.chat import get_extractor

    extractor = _RecordingExtractor()

    # AsyncMock — agent.run 直接给个结果
    mock_agent = AsyncMock()
    mock_agent.run = AsyncMock(return_value=AgentResult(
        text="好的,记下了。",
        iterations=1, tool_calls=[],
        input_tokens=10, output_tokens=5, model="fake",
    ))

    reset_memory_store()
    reset_conversation_store()
    app.dependency_overrides[get_agent] = lambda: mock_agent
    app.dependency_overrides[get_extractor] = lambda: extractor

    try:
        client = TestClient(app)
        resp = client.post(
            "/chat",
            json={"user_id": "u1", "character_id": "suwan", "message": "你好"},
        )
        assert resp.status_code == 200
    finally:
        app.dependency_overrides.clear()
        reset_memory_store()
        reset_conversation_store()

    # 给 fire-and-forget task 一点时间完成
    await asyncio.sleep(0.05)

    # 关键断言
    assert extractor.call_count == 1
    assert extractor.last_user_id == "u1"
    assert len(extractor.last_turns) == 2  # (user, assistant)


# ===== Loop 2: 真实提取 — 用 LLM 提取 fact 并入库 =====


@pytest.mark.asyncio
async def test_extractor_saves_basic_fact_to_memory() -> None:
    """extractor 用 LLM 提取 fact,成功后存入 memory store

    模拟 LLM 返回 JSON: [{"content": "用户叫小明", "category": "basic"}]
    验证: 1) extractor 不抛错  2) memory store 多了一条 fact
    """
    import json

    from app.llm.base import ChatResponse
    from app.memory.extractor import HaikuExtractor
    from app.memory.schemas import FactCategory
    from app.memory.store import InMemoryStore, reset_memory_store

    reset_memory_store()
    memory = InMemoryStore()

    # mock LLM 返回一个 fact JSON
    mock_provider = AsyncMock()
    mock_provider.chat = AsyncMock(return_value=ChatResponse(
        text=json.dumps([{"content": "用户叫小明", "category": "basic"}], ensure_ascii=False),
        stop_reason="end_turn",
        input_tokens=20, output_tokens=10, model="haiku-mock",
    ))

    extractor = HaikuExtractor(provider=mock_provider, memory=memory)

    facts = await extractor.extract(
        user_id="u1", character_id="suwan",
        turns=[{"role": "user", "content": "你好,我叫小明"}],
    )

    # 断言 1: 提取到 1 条 fact
    assert len(facts) == 1
    assert facts[0]["content"] == "用户叫小明"

    # 断言 2: 真的存进 memory store
    stored = await memory.list_all("u1")
    assert len(stored) == 1
    assert stored[0].content == "用户叫小明"
    assert stored[0].category == FactCategory.BASIC

    # 断言 3: LLM 被调过一次
    mock_provider.chat.assert_called_once()

    reset_memory_store()


@pytest.mark.asyncio
async def test_extractor_dedups_against_existing() -> None:
    """extractor 不会重复保存已经存在的 fact"""
    import json

    from app.llm.base import ChatResponse
    from app.memory.extractor import HaikuExtractor
    from app.memory.schemas import FactCategory
    from app.memory.store import InMemoryStore, reset_memory_store

    reset_memory_store()
    memory = InMemoryStore()

    # 预先存一条 "用户叫小明"
    await memory.add("u1", FactCategory.BASIC, "用户叫小明")

    # LLM 又返回同样的 fact
    mock_provider = AsyncMock()
    mock_provider.chat = AsyncMock(return_value=ChatResponse(
        text=json.dumps([{"content": "用户叫小明", "category": "basic"}], ensure_ascii=False),
        stop_reason="end_turn",
        input_tokens=20, output_tokens=10, model="haiku-mock",
    ))

    extractor = HaikuExtractor(provider=mock_provider, memory=memory)
    facts = await extractor.extract(
        user_id="u1", character_id="suwan",
        turns=[{"role": "user", "content": "我刚才说我叫小明"}],
    )

    # 应该 dedup,不返回也不新存
    assert len(facts) == 0
    stored = await memory.list_all("u1")
    assert len(stored) == 1  # 还是原来那一条,没新增

    reset_memory_store()


# ===== Loop 3: 端到端 — /chat 应真的让 background 提取 fact 并可被 search 召回 =====


@pytest.mark.asyncio
async def test_chat_end_to_end_extraction_then_recall() -> None:
    """用户说"我叫小明" → /chat 走完 → 后台提取 → search_memory 能召回

    验证整条链路:
      /chat → agent → fire-and-forget task → HaikuExtractor → memory.add
    """
    import json
    from fastapi.testclient import TestClient

    from app.agent.runtime import AgentResult
    from app.api.chat import get_extractor
    from app.llm.base import ChatResponse
    from app.memory.extractor import HaikuExtractor
    from app.memory.store import InMemoryStore

    reset_memory_store()
    reset_conversation_store()

    # mock agent
    mock_agent = AsyncMock()
    mock_agent.run = AsyncMock(return_value=AgentResult(
        text="好的,记下了。", iterations=1, tool_calls=[],
        input_tokens=10, output_tokens=5, model="fake",
    ))

    # mock Haiku LLM（用真 InMemoryStore + 假 LLM）
    memory = InMemoryStore()
    mock_haiku = AsyncMock()
    mock_haiku.chat = AsyncMock(return_value=ChatResponse(
        text=json.dumps([{"content": "用户叫小明", "category": "basic"}], ensure_ascii=False),
        stop_reason="end_turn",
        input_tokens=20, output_tokens=10, model="haiku-mock",
    ))
    real_extractor = HaikuExtractor(provider=mock_haiku, memory=memory)

    app.dependency_overrides[get_agent] = lambda: mock_agent
    app.dependency_overrides[get_extractor] = lambda: real_extractor

    try:
        client = TestClient(app)
        resp = client.post(
            "/chat",
            json={"user_id": "u1", "character_id": "suwan", "message": "你好,我叫小明"},
        )
        assert resp.status_code == 200

        # 等 fire-and-forget task 完成
        await asyncio.sleep(0.1)

        # 断言: Haiku 被调过
        mock_haiku.chat.assert_called_once()

        # 断言: fact 真的存进 memory store
        stored = await memory.list_all("u1")
        assert len(stored) == 1
        assert stored[0].content == "用户叫小明"

        # 断言: search_memory 能召回（用同一个 store — 上面 extractor 写过的）
        results = await memory.search("u1", "用户叫什么名字", top_k=5)
        # InMemoryStore.search 是 Jaccard 相似度 — "用户叫小明" 应能匹配 "用户叫什么名字"
        assert any("小明" in f.content for f in results), f"expected recall, got {results}"
    finally:
        app.dependency_overrides.clear()
        reset_memory_store()
        reset_conversation_store()
