"""Memory Store Factory — Loop 4c 生产开关

根据 settings.memory_backend 选择 InMemoryStore 或 QdrantStore。

设计:
- 默认 inmemory(本地/CI/单测安全,无外部依赖)
- 选 qdrant 时自动配 embedding provider(SentenceTransformerProvider)
- Qdrant 是 lazy connect:实例化不连,第一次操作才连(连不上 → 操作时报错)
- 单例缓存:同一 backend 多次调用返回同一实例

为什么叫 get_default_store 而不是 get_memory_store:
- 跟 settings.memory_backend 配对(语义对齐)
- 区分"获取默认实例" vs "构造新实例"
- 旧名 get_memory_store 仍可用,作为 alias
"""
from __future__ import annotations

import logging
from typing import Any

from app.config import Settings
from app.memory.schemas import MemoryFact
from app.memory.store import MemoryStore

logger = logging.getLogger(__name__)


# 单例缓存 — 跟 backend 类型分开存(允许两种 backend 共存,虽然实际不会)
_default_store: MemoryStore | None = None
_default_store_key: tuple[str, str, str] | None = None


def get_default_store(settings: Settings | None = None) -> MemoryStore:
    """获取默认 Memory Store(根据 settings 选 backend)

    Args:
        settings: 可选,None 时读全局 settings

    Returns:
        MemoryStore 实例(InMemoryStore 或 QdrantStore)

    Raises:
        RuntimeError: backend=qdrant 但 Qdrant 不可达(实际操作时报错)
    """
    global _default_store, _default_store_key

    if settings is None:
        from app.config import get_settings
        settings = get_settings()

    cache_key = (
        settings.memory_backend,
        settings.qdrant_url,
        settings.qdrant_collection,
    )
    if _default_store is not None and _default_store_key == cache_key:
        return _default_store

    if settings.memory_backend == "inmemory":
        from app.memory.store import InMemoryStore
        logger.info("memory store factory: creating InMemoryStore")
        _default_store = InMemoryStore()
    elif settings.memory_backend == "qdrant":
        from app.memory.qdrant_store import QdrantStore
        from app.llm.embedding import SentenceTransformerProvider

        # Qdrant 模式必须配 embedding — cosine 搜索需要
        embedding = SentenceTransformerProvider(
            model_name=settings.embedding_model,
            device=settings.embedding_device,
        )
        logger.info(
            "memory store factory: creating QdrantStore url=%s collection=%s embedding=%s",
            settings.qdrant_url, settings.qdrant_collection, settings.embedding_model,
        )
        _default_store = QdrantStore(
            url=settings.qdrant_url,
            collection_name=settings.qdrant_collection,
            api_key=settings.qdrant_api_key or None,
            embedding_provider=embedding,
        )
    else:
        # 不可能到这里(Settings Literal 已经验证过)
        raise ValueError(f"unknown memory backend: {settings.memory_backend}")

    _default_store_key = cache_key
    return _default_store


def reset_default_store() -> None:
    """测试用:重置单例缓存

    同时关掉 QdrantStore 客户端连接。
    """
    global _default_store, _default_store_key
    if _default_store is not None and hasattr(_default_store, "close"):
        try:
            _default_store.close()
        except Exception:
            pass
    _default_store = None
    _default_store_key = None


def get_qdrant_health() -> dict[str, Any]:
    """检查 Qdrant 健康状态(供 /health 端点)

    仅在 backend=qdrant 时有意义;inmemory 模式返回 {"backend": "inmemory", "healthy": true}。
    """
    settings = Settings()
    if settings.memory_backend == "inmemory":
        return {"backend": "inmemory", "healthy": True}

    try:
        import httpx
        resp = httpx.get(f"{settings.qdrant_url}/healthz", timeout=2.0)
        return {
            "backend": "qdrant",
            "healthy": resp.status_code == 200,
            "url": settings.qdrant_url,
            "status_code": resp.status_code,
        }
    except Exception as e:
        return {
            "backend": "qdrant",
            "healthy": False,
            "url": settings.qdrant_url,
            "error": str(e),
        }


# ===== 兼容旧 API =====
# 老代码用 get_memory_store(),定义在 app/memory/store.py 里(避免重复定义)
# 这里不再重复,避免 import 冲突