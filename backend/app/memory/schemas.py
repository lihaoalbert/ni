"""Memory 数据模型"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class FactCategory(str, Enum):
    """事实类别 — 引导 Claude 做结构化提取

    Day 3 简单分 5 类；后续可扩展。
    """

    BASIC = "basic"  # 姓名、年龄、城市等基础信息
    PREFERENCE = "preference"  # 喜好、厌恶
    RELATIONSHIP = "relationship"  # 家人、朋友、伴侣
    WORK = "work"  # 工作、职业、项目
    EVENT = "event"  # 重要事件、生日、纪念日


@dataclass
class MemoryFact:
    """一条关于用户的事实

    Day 3 用 dataclass；后续会迁移到 SQLAlchemy（结构化）+ Qdrant（向量）。
    """

    id: str
    user_id: str
    category: FactCategory
    content: str
    confidence: float = 1.0
    created_at: datetime = field(default_factory=datetime.now)
    last_accessed_at: datetime = field(default_factory=datetime.now)
    access_count: int = 0
    source: str = "agent"  # "agent" (Claude 提取) / "user" (显式输入) / "seed" (初始)

    def touch(self) -> None:
        """被检索到时调用 — 标记热度"""
        self.last_accessed_at = datetime.now()
        self.access_count += 1

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "category": self.category.value,
            "content": self.content,
            "confidence": self.confidence,
            "created_at": self.created_at.isoformat(),
        }
