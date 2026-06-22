"""Memory 模块测试 — store + schemas"""
from __future__ import annotations

import pytest

from app.memory.schemas import FactCategory, MemoryFact
from app.memory.store import (
    ConversationStore,
    InMemoryStore,
    get_conversation_store,
    get_memory_store,
    reset_conversation_store,
    reset_memory_store,
)


@pytest.fixture(autouse=True)
def _reset_global_store():
    reset_memory_store()
    yield
    reset_memory_store()


@pytest.mark.asyncio
async def test_add_creates_fact() -> None:
    store = InMemoryStore()
    fact = await store.add("u1", FactCategory.WORK, "用户是软件工程师")
    assert isinstance(fact, MemoryFact)
    assert fact.id
    assert fact.user_id == "u1"
    assert fact.category == FactCategory.WORK


@pytest.mark.asyncio
async def test_add_deduplicates_same_content() -> None:
    store = InMemoryStore()
    f1 = await store.add("u1", FactCategory.WORK, "用户是软件工程师")
    f2 = await store.add("u1", FactCategory.WORK, "用户是软件工程师")
    assert f1.id == f2.id  # 去重返回同一个
    assert await store.count("u1") == 1


@pytest.mark.asyncio
async def test_add_different_users_independent() -> None:
    store = InMemoryStore()
    await store.add("u1", FactCategory.BASIC, "我叫小明")
    await store.add("u2", FactCategory.BASIC, "我叫小红")
    assert await store.count("u1") == 1
    assert await store.count("u2") == 1


@pytest.mark.asyncio
async def test_search_ranks_by_overlap() -> None:
    store = InMemoryStore()
    await store.add("u1", FactCategory.WORK, "用户是软件工程师")
    await store.add("u1", FactCategory.PREFERENCE, "用户喜欢爵士乐")
    await store.add("u1", FactCategory.BASIC, "用户住在北京")

    results = await store.search("u1", "软件工程师")
    assert len(results) >= 1
    assert "软件工程师" in results[0].content


@pytest.mark.asyncio
async def test_search_filters_by_category() -> None:
    store = InMemoryStore()
    await store.add("u1", FactCategory.WORK, "用户是软件工程师")
    await store.add("u1", FactCategory.PREFERENCE, "用户喜欢软件")

    results = await store.search("u1", "软件", category=FactCategory.WORK)
    assert len(results) == 1
    assert results[0].category == FactCategory.WORK


@pytest.mark.asyncio
async def test_search_touches_access_stats() -> None:
    store = InMemoryStore()
    fact = await store.add("u1", FactCategory.WORK, "用户是软件工程师")
    initial_count = fact.access_count
    await store.search("u1", "工程师")
    assert fact.access_count == initial_count + 1


@pytest.mark.asyncio
async def test_list_all_orders_by_created_desc() -> None:
    store = InMemoryStore()
    f1 = await store.add("u1", FactCategory.BASIC, "先添加")
    f2 = await store.add("u1", FactCategory.BASIC, "后添加")
    facts = await store.list_all("u1")
    assert facts[0].id == f2.id  # 新的在前
    assert facts[1].id == f1.id


@pytest.mark.asyncio
async def test_forget_returns_true_on_existing() -> None:
    store = InMemoryStore()
    fact = await store.add("u1", FactCategory.BASIC, "要忘掉")
    assert await store.forget("u1", fact.id) is True
    assert await store.count("u1") == 0


@pytest.mark.asyncio
async def test_forget_returns_false_on_missing() -> None:
    store = InMemoryStore()
    assert await store.forget("u1", "nonexistent") is False


@pytest.mark.asyncio
async def test_global_store_singleton() -> None:
    s1 = get_memory_store()
    s2 = get_memory_store()
    assert s1 is s2
    await s1.add("u1", FactCategory.BASIC, "global test")
    assert await s2.count("u1") == 1


# ===== ConversationStore 测试 =====


def test_conversation_store_append_and_get() -> None:
    cs = ConversationStore()
    cs.append("u1", "suwan", "user", "hi")
    cs.append("u1", "suwan", "assistant", "你好")
    history = cs.get_history("u1", "suwan")
    assert len(history) == 2
    assert history[0].role == "user"
    assert history[1].role == "assistant"


def test_conversation_store_separates_users() -> None:
    cs = ConversationStore()
    cs.append("u1", "suwan", "user", "u1 的消息")
    cs.append("u2", "suwan", "user", "u2 的消息")
    assert len(cs.get_history("u1", "suwan")) == 1
    assert len(cs.get_history("u2", "suwan")) == 1


def test_conversation_store_separates_characters() -> None:
    cs = ConversationStore()
    cs.append("u1", "suwan", "user", "给苏晚的")
    cs.append("u1", "other", "user", "给 other 的")
    assert cs.get_history("u1", "suwan")[0].content == "给苏晚的"
    assert cs.get_history("u1", "other")[0].content == "给 other 的"


def test_conversation_store_truncates_at_max_turns() -> None:
    cs = ConversationStore(max_turns=3)
    for i in range(5):
        cs.append("u1", "c", "user", f"msg{i}")
    history = cs.get_history("u1", "c")
    assert len(history) == 3
    assert history[0].content == "msg2"
    assert history[-1].content == "msg4"


def test_conversation_store_clear() -> None:
    cs = ConversationStore()
    cs.append("u1", "c", "user", "x")
    cs.clear("u1", "c")
    assert cs.get_history("u1", "c") == []


def test_global_conversation_store_singleton() -> None:
    reset_conversation_store()
    s1 = get_conversation_store()
    s2 = get_conversation_store()
    assert s1 is s2
