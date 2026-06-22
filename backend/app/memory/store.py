"""Memory Store — 事实的 CRUD + 检索

Day 3: 进程内 InMemoryStore（重启即丢）
Day 5+: 换 PostgreSQL（结构化）+ Qdrant（向量）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import uuid4

from app.memory.schemas import FactCategory, MemoryFact

logger = logging.getLogger(__name__)


class MemoryStore(Protocol):
    """所有 Memory Store 实现必须满足的接口"""

    async def add(
        self,
        user_id: str,
        category: FactCategory,
        content: str,
        source: str = "agent",
    ) -> MemoryFact: ...

    async def search(
        self,
        user_id: str,
        query: str,
        top_k: int = 5,
        category: FactCategory | None = None,
    ) -> list[MemoryFact]: ...

    async def list_all(
        self,
        user_id: str,
        category: FactCategory | None = None,
    ) -> list[MemoryFact]: ...

    async def forget(self, user_id: str, fact_id: str) -> bool: ...

    async def count(self, user_id: str) -> int: ...


class InMemoryStore:
    """Day 3 用：进程内 dict，重启清空

    简单事实去重：相同 category + 相同 content 不重复存储。
    """

    def __init__(self) -> None:
        # user_id -> fact_id -> MemoryFact
        self._store: dict[str, dict[str, MemoryFact]] = {}

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

        bucket = self._store.setdefault(user_id, {})

        # 去重：相同 (category, content) 直接返回已有
        for existing in bucket.values():
            if existing.category == category and existing.content == content:
                logger.debug(f"memory dedup hit user={user_id} fact={existing.id}")
                return existing

        fact = MemoryFact(
            id=uuid4().hex[:12],
            user_id=user_id,
            category=category,
            content=content,
            source=source,
        )
        bucket[fact.id] = fact
        logger.info(f"memory added user={user_id} cat={category.value} fact={fact.id}")
        return fact

    async def search(
        self,
        user_id: str,
        query: str,
        top_k: int = 5,
        category: FactCategory | None = None,
    ) -> list[MemoryFact]:
        bucket = self._store.get(user_id, {})
        candidates = list(bucket.values())
        if category:
            candidates = [f for f in candidates if f.category == category]

        # Day 3 简单算法：字符级 Jaccard 相似度
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
            score = len(intersection) / len(union)  # Jaccard
            # 加 confidence 权重
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
        bucket = self._store.get(user_id, {})
        facts = list(bucket.values())
        if category:
            facts = [f for f in facts if f.category == category]
        # 按 created_at 倒序
        facts.sort(key=lambda f: f.created_at, reverse=True)
        return facts

    async def forget(self, user_id: str, fact_id: str) -> bool:
        bucket = self._store.get(user_id, {})
        if fact_id in bucket:
            del bucket[fact_id]
            logger.info(f"memory forgotten user={user_id} fact={fact_id}")
            return True
        return False

    async def count(self, user_id: str) -> int:
        return len(self._store.get(user_id, {}))


# 全局单例 — Day 3 用，Day 5+ 换依赖注入
_global_store: InMemoryStore | None = None


def get_memory_store() -> InMemoryStore:
    """获取全局 memory store 单例"""
    global _global_store
    if _global_store is None:
        _global_store = InMemoryStore()
        logger.info("memory store initialized (in-memory)")
    return _global_store


def reset_memory_store() -> None:
    """测试用：重置全局 store"""
    global _global_store
    _global_store = None


# ===== 会话历史（Day 3 补充）=====


@dataclass
class ChatTurn:
    """一条对话轮次"""

    role: str  # "user" or "assistant"
    content: str
    timestamp: datetime = field(default_factory=datetime.now)


class ConversationStore:
    """按 (user_id, character_id) 存最近 N 条对话历史

    Day 3 简单版：每个会话只保留最近 20 轮，超出截断。
    生产会换 PostgreSQL。
    """

    def __init__(self, max_turns: int = 20):
        self.max_turns = max_turns
        # key = (user_id, character_id) -> list[ChatTurn]
        self._conversations: dict[tuple[str, str], list[ChatTurn]] = {}

    def _key(self, user_id: str, character_id: str) -> tuple[str, str]:
        return (user_id, character_id)

    def get_history(self, user_id: str, character_id: str) -> list[ChatTurn]:
        return list(self._conversations.get(self._key(user_id, character_id), []))

    def append(self, user_id: str, character_id: str, role: str, content: str) -> None:
        key = self._key(user_id, character_id)
        history = self._conversations.setdefault(key, [])
        history.append(ChatTurn(role=role, content=content))
        # 保留最近 max_turns 条
        if len(history) > self.max_turns:
            self._conversations[key] = history[-self.max_turns:]

    def clear(self, user_id: str, character_id: str) -> None:
        self._conversations.pop(self._key(user_id, character_id), None)


_global_conversation: ConversationStore | None = None


def get_conversation_store() -> ConversationStore:
    global _global_conversation
    if _global_conversation is None:
        _global_conversation = ConversationStore()
        logger.info("conversation store initialized (in-memory)")
    return _global_conversation


def reset_conversation_store() -> None:
    global _global_conversation
    _global_conversation = None
