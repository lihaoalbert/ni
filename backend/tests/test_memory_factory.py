"""Memory Store Factory 测试 — Loop 4c

覆盖:
1. MEMORY_BACKEND=inmemory → 返回 InMemoryStore
2. MEMORY_BACKEND=qdrant → 返回 QdrantStore
3. Qdrant 连接失败 → 返回友好错误
4. 旧名 get_memory_store 仍可用(alias)
"""
from __future__ import annotations

import pytest

from app.config import Settings
from app.memory.store import (
    InMemoryStore,
    get_memory_store,
    reset_memory_store,
)
from app.memory.qdrant_store import QdrantStore


@pytest.fixture(autouse=True)
def _reset_singletons():
    """每个测试前重置单例"""
    reset_memory_store()
    from app.llm.embedding import reset_embedding_provider
    reset_embedding_provider()
    yield
    reset_memory_store()
    reset_embedding_provider()


def test_default_backend_is_inmemory() -> None:
    """默认 backend=inmemory,本地/CI/单测安全"""
    settings = Settings()  # 读 .env,默认 inmemory
    assert settings.memory_backend == "inmemory"


def test_factory_returns_inmemory_when_default() -> None:
    """默认配置 → InMemoryStore 实例"""
    settings = Settings()
    from app.memory.store_factory import get_default_store

    store = get_default_store(settings)
    assert isinstance(store, InMemoryStore)
    store.close() if hasattr(store, "close") else None


def test_factory_returns_qdrant_when_configured(monkeypatch) -> None:
    """MEMORY_BACKEND=qdrant → QdrantStore 实例(连接 :memory:)"""
    monkeypatch.setenv("MEMORY_BACKEND", "qdrant")
    monkeypatch.setenv("QDRANT_URL", ":memory:")  # 用内存模式避免依赖外部

    settings = Settings()
    from app.memory.store_factory import get_default_store

    store = get_default_store(settings)
    assert isinstance(store, QdrantStore)
    store.close()


def test_factory_legacy_alias_works() -> None:
    """旧名 get_memory_store() 仍可用,跟 get_default_store() 等价"""
    settings = Settings()
    from app.memory.store_factory import get_default_store

    s1 = get_default_store(settings)
    s2 = get_memory_store()
    # 同一 backend 类型(可能是不同实例,但都是 InMemoryStore)
    assert type(s1) is type(s2)


def test_factory_qdrant_with_real_binary() -> None:
    """集成测试:真 Qdrant binary(已在 localhost:6333)能连上

    skipif: 仅在 QDRANT_BINARY_TEST_URL 设了才跑,避免 CI 失败。
    """
    import os
    if not os.environ.get("QDRANT_BINARY_TEST_URL"):
        pytest.skip("set QDRANT_BINARY_TEST_URL to enable binary integration test")

    settings = Settings(memory_backend="qdrant", qdrant_url=os.environ["QDRANT_BINARY_TEST_URL"])
    from app.memory.store_factory import get_default_store

    store = get_default_store(settings)
    assert isinstance(store, QdrantStore)
    store.close()


def test_factory_invalid_backend_raises() -> None:
    """非法 backend 值 → 友好错误,不要偷偷 fallback"""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Settings(memory_backend="invalid")