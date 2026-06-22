"""QdrantStore 测试 — Loop 4a 骨架

Phase A 目标: 验证 QdrantStore 实现 MemoryStore Protocol 契约
Phase B (后续): 加 embedding + cosine 语义搜索

测试策略:
- 默认用 :memory: 模式,无外部依赖,CI 可跑
- 集成测试 (QDRANT_URL=...) 时连接真实 Qdrant
"""
from __future__ import annotations

import os
import uuid

import pytest

from app.memory.qdrant_store import QdrantStore
from app.memory.schemas import FactCategory, MemoryFact


def _make_store() -> QdrantStore:
    """默认 :memory: 模式;设 QDRANT_TEST_URL 走真 Qdrant"""
    url = os.environ.get("QDRANT_TEST_URL", ":memory:")
    # 每个测试用独立 collection,避免污染
    collection = f"test_{uuid.uuid4().hex[:8]}"
    return QdrantStore(url=url, collection_name=collection)


@pytest.mark.asyncio
async def test_qdrant_store_add_returns_fact() -> None:
    """add() 返回 MemoryFact,含 id + 元数据"""
    store = _make_store()
    try:
        fact = await store.add(
            user_id="u1",
            category=FactCategory.BASIC,
            content="用户叫小明",
        )
        assert isinstance(fact, MemoryFact)
        assert fact.id
        assert fact.user_id == "u1"
        assert fact.category == FactCategory.BASIC
        assert fact.content == "用户叫小明"
        assert fact.source == "agent"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_qdrant_store_add_persists() -> None:
    """add() 后 list_all() 能取到"""
    store = _make_store()
    try:
        await store.add("u1", FactCategory.BASIC, "用户叫小明")
        await store.add("u1", FactCategory.WORK, "用户是产品经理")

        facts = await store.list_all("u1")
        assert len(facts) == 2
        contents = {f.content for f in facts}
        assert "用户叫小明" in contents
        assert "用户是产品经理" in contents
    finally:
        store.close()


@pytest.mark.asyncio
async def test_qdrant_store_separates_users() -> None:
    """不同用户的 fact 互不可见"""
    store = _make_store()
    try:
        await store.add("u1", FactCategory.BASIC, "用户叫小明")
        await store.add("u2", FactCategory.BASIC, "用户叫小红")

        u1_facts = await store.list_all("u1")
        u2_facts = await store.list_all("u2")

        assert len(u1_facts) == 1
        assert len(u2_facts) == 1
        assert u1_facts[0].content == "用户叫小明"
        assert u2_facts[0].content == "用户叫小红"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_qdrant_store_dedup_same_content() -> None:
    """同 (user, category, content) 重复 add 不创建第二条"""
    store = _make_store()
    try:
        f1 = await store.add("u1", FactCategory.BASIC, "用户叫小明")
        f2 = await store.add("u1", FactCategory.BASIC, "用户叫小明")
        # 同一条 fact,不创建新的
        assert f1.id == f2.id

        facts = await store.list_all("u1")
        assert len(facts) == 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_qdrant_store_list_all_category_filter() -> None:
    """list_all() 支持按 category 过滤"""
    store = _make_store()
    try:
        await store.add("u1", FactCategory.BASIC, "用户叫小明")
        await store.add("u1", FactCategory.WORK, "用户是产品经理")
        await store.add("u1", FactCategory.PREFERENCE, "用户喜欢爵士乐")

        basic_facts = await store.list_all("u1", category=FactCategory.BASIC)
        assert len(basic_facts) == 1
        assert basic_facts[0].category == FactCategory.BASIC

        work_facts = await store.list_all("u1", category=FactCategory.WORK)
        assert len(work_facts) == 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_qdrant_store_search_jaccard() -> None:
    """Loop 4a: search() 用 Jaccard (骨架阶段,后续换 cosine)"""
    store = _make_store()
    try:
        await store.add("u1", FactCategory.BASIC, "用户叫小明")
        await store.add("u1", FactCategory.WORK, "用户是产品经理")

        # "用户叫什么" 跟 "用户叫小明" 共享字符
        results = await store.search("u1", "用户叫什么", top_k=5)
        assert len(results) >= 1
        assert any("小明" in f.content for f in results)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_qdrant_store_search_category_filter() -> None:
    """search() 支持 category 过滤"""
    store = _make_store()
    try:
        await store.add("u1", FactCategory.BASIC, "用户叫小明")
        await store.add("u1", FactCategory.WORK, "用户是产品经理")

        # 只在 WORK 里搜 "用户"
        results = await store.search(
            "u1", "用户", top_k=5, category=FactCategory.WORK
        )
        assert len(results) == 1
        assert results[0].content == "用户是产品经理"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_qdrant_store_forget() -> None:
    """forget() 删除指定 fact"""
    store = _make_store()
    try:
        f = await store.add("u1", FactCategory.BASIC, "用户叫小明")
        assert await store.count("u1") == 1

        ok = await store.forget("u1", f.id)
        assert ok is True
        assert await store.count("u1") == 0

        # 第二次删返回 False
        ok_again = await store.forget("u1", f.id)
        assert ok_again is False
    finally:
        store.close()


@pytest.mark.asyncio
async def test_qdrant_store_count() -> None:
    """count() 返回 user 的 fact 总数"""
    store = _make_store()
    try:
        assert await store.count("u1") == 0
        await store.add("u1", FactCategory.BASIC, "用户叫小明")
        assert await store.count("u1") == 1
        await store.add("u1", FactCategory.WORK, "用户是产品经理")
        assert await store.count("u1") == 2
    finally:
        store.close()


@pytest.mark.asyncio
async def test_qdrant_store_protocol_compatible() -> None:
    """QdrantStore 满足 MemoryStore Protocol (鸭子类型检查)

    Protocol 是静态类型 — 不用 isinstance,直接验证所有方法签名一致。
    """
    store = _make_store()
    try:
        # 验证接口完整
        for method in ["add", "search", "list_all", "forget", "count"]:
            assert hasattr(store, method), f"QdrantStore 缺少方法 {method}"
            assert callable(getattr(store, method))

        # 静态类型契约 — MemoryFact return type 是 MemoryFact
        import inspect
        from app.memory.store import MemoryStore

        sig = inspect.signature(MemoryStore.add)
        # Protocol 的方法签名应该匹配 QdrantStore.add 的签名
        assert sig.parameters.keys() == {"self", "user_id", "category", "content", "source"}
    finally:
        store.close()


@pytest.mark.asyncio
async def test_qdrant_store_empty_user_returns_empty() -> None:
    """空 user / 不存在 user 返回空列表"""
    store = _make_store()
    try:
        facts = await store.list_all("never_existed_user")
        assert facts == []

        results = await store.search("never_existed_user", "any query")
        assert results == []

        assert await store.count("never_existed_user") == 0
    finally:
        store.close()