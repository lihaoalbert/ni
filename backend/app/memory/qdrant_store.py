"""QdrantStore — 向量库 Memory Store 实现

Loop 4a (Phase A): 纯文本存 Qdrant payload,Jaccard 检索
Loop 4b (Phase B): 加 embedding provider,cosine 语义检索(可关闭,退回 Phase A 行为)

API 完全兼容 MemoryStore Protocol,可直接替换 InMemoryStore。

设计:
- 单 collection 存所有用户的 fact(按 user_id 分组)
- payload 存元数据(content/user_id/category/...)
- vector 字段:有 embedding 时存真向量;无时存零向量占位
- 无 embedding_provider 时,search 退化用 Jaccard(Phase A 行为)
- collection dim 跟 embedding dim 不匹配时,自动重建并警告
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from app.llm.embedding import EmbeddingProvider
from app.memory.schemas import FactCategory, MemoryFact

logger = logging.getLogger(__name__)


# Phase A 占位向量大小(无 embedding 时)
_PHASE_A_VECTOR_SIZE = 4


def _fact_id_to_point_id(fact_id: str) -> str:
    """MemoryFact.id (12字符 hex) → Qdrant point ID (UUID 字符串)"""
    return str(uuid.uuid5(uuid.NAMESPACE_OID, f"fact:{fact_id}"))


class QdrantStore:
    """Qdrant 向量库 Memory Store

    用法:
        # 纯文本(Jaccard,Phase A 行为)
        store = QdrantStore(url=":memory:")

        # 接入 embedding(Phase B,语义检索)
        store = QdrantStore(
            url=":memory:",
            embedding_provider=SentenceTransformerProvider(),
        )

        await store.add(...)
        ...
        store.close()

    Args:
        url: Qdrant URL 或 ":memory:" (默认,用于测试)
        collection_name: collection 名
        embedding_provider: 可选,提供时启用语义检索;None 时走 Jaccard
    """

    def __init__(
        self,
        url: str = ":memory:",
        collection_name: str = "memory_facts",
        api_key: str | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self.collection_name = collection_name
        self.embedding_provider = embedding_provider

        # 如果 embedding 是 lazy load(初始 dim=0),强制加载以确定 dim
        # 否则建 collection 时 size=0 会被 Qdrant server 拒绝
        if embedding_provider is not None and embedding_provider.dim == 0:
            if hasattr(embedding_provider, "_ensure_loaded"):
                embedding_provider._ensure_loaded()

        if url == ":memory:":
            self._client = QdrantClient(location=":memory:")
        else:
            self._client = QdrantClient(url=url, api_key=api_key)

        # 计算目标 dim
        self._target_dim = (
            embedding_provider.dim if embedding_provider else _PHASE_A_VECTOR_SIZE
        )

        self._ensure_collection()

    def _ensure_collection(self) -> None:
        """检查/创建 collection。dim 不匹配时自动重建(开发期 OK,生产需迁移策略)。"""
        try:
            info = self._client.get_collection(self.collection_name)
            existing_dim = info.config.params.vectors.size if info.config.params.vectors else None
        except Exception:
            existing_dim = None

        if existing_dim is None:
            # 不存在 → 创建
            self._client.create_collection(
                collection_name=self.collection_name,
                vectors_config=qmodels.VectorParams(
                    size=self._target_dim,
                    distance=qmodels.Distance.COSINE,
                ),
            )
            logger.info(
                "qdrant collection created: %s dim=%d",
                self.collection_name, self._target_dim,
            )
        elif existing_dim != self._target_dim:
            # dim 不匹配 → 重建(警告)
            logger.warning(
                "qdrant collection dim mismatch: %d != %d, recreating",
                existing_dim, self._target_dim,
            )
            self._client.delete_collection(self.collection_name)
            self._client.create_collection(
                collection_name=self.collection_name,
                vectors_config=qmodels.VectorParams(
                    size=self._target_dim,
                    distance=qmodels.Distance.COSINE,
                ),
            )

    # ===== CRUD =====

    async def add(
        self,
        user_id: str,
        category: FactCategory,
        content: str,
        source: str = "agent",
    ) -> MemoryFact:
        content = content.strip()
        if not content:
            raise ValueError("memory content 不能为空")

        # 去重:同 (user_id, category, content) 直接返回已有
        existing = await self._find_existing(user_id, category, content)
        if existing is not None:
            logger.debug("qdrant dedup hit user=%s fact=%s", user_id, existing.id)
            return existing

        fact = MemoryFact(
            id=uuid.uuid4().hex[:12],
            user_id=user_id,
            category=category,
            content=content,
            source=source,
        )

        # 向量:有 embedding_provider 就用真向量,否则零向量占位
        if self.embedding_provider is not None:
            vector = await self.embedding_provider.embed(content)
        else:
            vector = [0.0] * _PHASE_A_VECTOR_SIZE

        point = qmodels.PointStruct(
            id=_fact_id_to_point_id(fact.id),
            vector=vector,
            payload={
                "fact_id": fact.id,
                "user_id": user_id,
                "category": category.value,
                "content": content,
                "confidence": fact.confidence,
                "created_at": fact.created_at.isoformat(),
                "source": source,
            },
        )

        self._client.upsert(
            collection_name=self.collection_name,
            points=[point],
        )
        logger.info(
            "qdrant memory added user=%s cat=%s fact=%s dim=%d",
            user_id, category.value, fact.id, len(vector),
        )
        return fact

    async def _find_existing(
        self,
        user_id: str,
        category: FactCategory,
        content: str,
    ) -> MemoryFact | None:
        points, _ = self._client.scroll(
            collection_name=self.collection_name,
            scroll_filter=qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key="user_id", match=qmodels.MatchValue(value=user_id)
                    ),
                    qmodels.FieldCondition(
                        key="category", match=qmodels.MatchValue(value=category.value)
                    ),
                    qmodels.FieldCondition(
                        key="content", match=qmodels.MatchValue(value=content)
                    ),
                ]
            ),
            limit=1,
            with_payload=True,
            with_vectors=False,
        )
        if not points:
            return None
        return self._payload_to_fact(points[0].payload)

    async def search(
        self,
        user_id: str,
        query: str,
        top_k: int = 5,
        category: FactCategory | None = None,
    ) -> list[MemoryFact]:
        """Loop 4b:有 embedding 时用 cosine 语义检索;否则用 Jaccard(Phase A 行为)"""
        if self.embedding_provider is not None:
            return await self._search_cosine(user_id, query, top_k, category)
        return await self._search_jaccard(user_id, query, top_k, category)

    async def _search_cosine(
        self,
        user_id: str,
        query: str,
        top_k: int,
        category: FactCategory | None,
    ) -> list[MemoryFact]:
        """向量检索 — 用 Qdrant query_points"""
        query_vec = await self.embedding_provider.embed(query)  # type: ignore[union-attr]

        must = [
            qmodels.FieldCondition(
                key="user_id", match=qmodels.MatchValue(value=user_id)
            )
        ]
        if category is not None:
            must.append(
                qmodels.FieldCondition(
                    key="category", match=qmodels.MatchValue(value=category.value)
                )
            )

        hits = self._client.query_points(
            collection_name=self.collection_name,
            query=query_vec,
            query_filter=qmodels.Filter(must=must),
            limit=top_k,
            with_payload=True,
            with_vectors=False,
        )

        results: list[MemoryFact] = []
        for hit in hits.points:
            fact = self._payload_to_fact(hit.payload)
            fact.touch()
            results.append(fact)
        return results

    async def _search_jaccard(
        self,
        user_id: str,
        query: str,
        top_k: int,
        category: FactCategory | None,
    ) -> list[MemoryFact]:
        """Phase A 行为:字符级 Jaccard"""
        points = self._scroll_user(user_id, category=category, limit=1000)
        candidates = [self._payload_to_fact(p.payload) for p in points]

        query_chars = set(query.lower())
        scored: list[tuple[float, MemoryFact]] = []
        for f in candidates:
            content_chars = set(f.content.lower())
            if not content_chars:
                continue
            union = query_chars | content_chars
            intersection = query_chars & content_chars
            if not intersection:
                continue
            score = len(intersection) / len(union)
            score *= f.confidence
            scored.append((score, f))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [f for _, f in scored[:top_k]]
        for f in results:
            f.touch()
        return results

    async def list_all(
        self,
        user_id: str,
        category: FactCategory | None = None,
    ) -> list[MemoryFact]:
        points = self._scroll_user(user_id, category=category, limit=1000)
        facts = [self._payload_to_fact(p.payload) for p in points]
        facts.sort(key=lambda f: f.created_at, reverse=True)
        return facts

    async def forget(self, user_id: str, fact_id: str) -> bool:
        point_id = _fact_id_to_point_id(fact_id)
        try:
            points = self._client.retrieve(
                collection_name=self.collection_name,
                ids=[point_id],
                with_payload=True,
                with_vectors=False,
            )
        except Exception:
            return False

        if not points:
            return False

        payload = points[0].payload or {}
        if payload.get("user_id") != user_id:
            logger.warning(
                "qdrant forget rejected: fact=%s user mismatch (%s != %s)",
                fact_id, payload.get("user_id"), user_id,
            )
            return False

        self._client.delete(
            collection_name=self.collection_name,
            points_selector=qmodels.PointIdsList(points=[point_id]),
        )
        logger.info("qdrant memory forgotten user=%s fact=%s", user_id, fact_id)
        return True

    async def count(self, user_id: str) -> int:
        points = self._scroll_user(user_id, category=None, limit=10000)
        return len(points)

    # ===== Helpers =====

    def _scroll_user(
        self,
        user_id: str,
        category: FactCategory | None = None,
        limit: int = 100,
    ) -> list[Any]:
        must = [
            qmodels.FieldCondition(
                key="user_id", match=qmodels.MatchValue(value=user_id)
            )
        ]
        if category is not None:
            must.append(
                qmodels.FieldCondition(
                    key="category", match=qmodels.MatchValue(value=category.value)
                )
            )

        all_points: list[Any] = []
        offset: Any = None
        while True:
            points, next_offset = self._client.scroll(
                collection_name=self.collection_name,
                scroll_filter=qmodels.Filter(must=must),
                limit=min(limit, 100),
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            all_points.extend(points)
            if next_offset is None or len(all_points) >= limit:
                break
            offset = next_offset

        return all_points[:limit]

    @staticmethod
    def _payload_to_fact(payload: dict | None) -> MemoryFact:
        if not payload:
            raise ValueError("empty payload")
        return MemoryFact(
            id=payload.get("fact_id", ""),
            user_id=payload.get("user_id", ""),
            category=FactCategory(payload.get("category", "basic")),
            content=payload.get("content", ""),
            confidence=float(payload.get("confidence", 1.0)),
            created_at=datetime.fromisoformat(
                payload.get("created_at", datetime.now().isoformat())
            ),
            source=payload.get("source", "agent"),
        )

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass