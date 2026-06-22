"""QdrantStore — 向量库 Memory Store 实现 (Loop 4a)

Phase A (当前): 纯文本存 Qdrant payload,Jaccard 检索 (跟 InMemoryStore 一致)
Phase B (Loop 4b): 加 embedding provider,cosine 语义检索

API 完全兼容 MemoryStore Protocol,可直接替换 InMemoryStore。

设计:
- 单 collection 存所有用户的 fact(按 user_id 分组)
- payload 存元数据,vector 字段保留(Phase A 用零向量,Phase B 换真向量)
- 字符串 ID → Qdrant point ID 用 uuid5(稳定)
- 客户端关闭用 close() 方法
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from app.memory.schemas import FactCategory, MemoryFact

logger = logging.getLogger(__name__)


# Qdrant vector size — Phase A 用零向量占位,size 可以是任意固定值
_PHASE_A_VECTOR_SIZE = 4


def _fact_id_to_point_id(fact_id: str) -> str:
    """MemoryFact.id (12字符 hex) → Qdrant point ID

    Qdrant 接受 str/int/UUID 作为 point ID。用 uuid5 让同 fact_id 永远映射到同 UUID。
    """
    return str(uuid.uuid5(uuid.NAMESPACE_OID, f"fact:{fact_id}"))


def _point_id_to_fact_id(point_id: str) -> str:
    """Qdrant point ID → MemoryFact.id 反查(取后 12 字符 hex)"""
    return point_id.split("-")[-1][:12]


class QdrantStore:
    """Qdrant 向量库 Memory Store

    用法:
        store = QdrantStore(url=":memory:")           # 内存模式(CI/单测)
        store = QdrantStore(url="http://localhost:6333") # 本地/生产
        await store.add(...)
        ...
        store.close()
    """

    def __init__(
        self,
        url: str = ":memory:",
        collection_name: str = "memory_facts",
        api_key: str | None = None,
    ) -> None:
        self.collection_name = collection_name
        # :memory: 模式不用 api_key
        if url == ":memory:":
            self._client = QdrantClient(location=":memory:")
        else:
            self._client = QdrantClient(url=url, api_key=api_key)

        # 确保 collection 存在
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        """检查/创建 collection。Phase A 不存真向量,用零向量占位。"""
        try:
            self._client.get_collection(self.collection_name)
        except Exception:
            self._client.create_collection(
                collection_name=self.collection_name,
                vectors_config=qmodels.VectorParams(
                    size=_PHASE_A_VECTOR_SIZE,
                    distance=qmodels.Distance.COSINE,
                ),
            )
            logger.info("qdrant collection created: %s", self.collection_name)

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

        point = qmodels.PointStruct(
            id=_fact_id_to_point_id(fact.id),
            vector=[0.0] * _PHASE_A_VECTOR_SIZE,  # Phase A 占位
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
            "qdrant memory added user=%s cat=%s fact=%s",
            user_id, category.value, fact.id,
        )
        return fact

    async def _find_existing(
        self,
        user_id: str,
        category: FactCategory,
        content: str,
    ) -> MemoryFact | None:
        """查重 — 用 scroll + filter 找同 (user_id, category, content)"""
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
        """Loop 4a: Jaccard 字符级相似度(Phase A 不调向量)

        后续 Loop 4b 替换为 cosine 语义检索。
        """
        # 1. 从 Qdrant 取 user 的所有 fact(payload filter)
        points = self._scroll_user(user_id, category=category, limit=1000)
        candidates = [self._payload_to_fact(p.payload) for p in points]

        # 2. Jaccard 评分(同 InMemoryStore.search 算法)
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
        # 按 created_at 倒序
        facts.sort(key=lambda f: f.created_at, reverse=True)
        return facts

    async def forget(self, user_id: str, fact_id: str) -> bool:
        """删除指定 fact。需要校验 user_id 匹配(防止误删别人)。"""
        point_id = _fact_id_to_point_id(fact_id)
        # 先确认 fact 存在且属于该 user
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
        """scroll 该 user 的所有 fact (Phase A 不分页,limit 是兜底)"""
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
                limit=min(limit, 100),  # Qdrant 一次最多 scroll 100
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
        """Qdrant payload → MemoryFact"""
        if not payload:
            raise ValueError("empty payload")
        return MemoryFact(
            id=payload.get("fact_id", ""),
            user_id=payload.get("user_id", ""),
            category=FactCategory(payload.get("category", "basic")),
            content=payload.get("content", ""),
            confidence=float(payload.get("confidence", 1.0)),
            created_at=datetime.fromisoformat(payload.get("created_at", datetime.now().isoformat())),
            source=payload.get("source", "agent"),
        )

    def close(self) -> None:
        """关闭客户端 — :memory: 释放,远程连接关闭"""
        try:
            self._client.close()
        except Exception:
            pass