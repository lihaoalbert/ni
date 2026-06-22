"""Character 数据模型 — 对应平台 API 的 JSON Schema

平台出 API 时，按这个 schema 提供数据。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class SpeakingStyle(BaseModel):
    tone: str = Field(default="", description="语气基调")
    catchphrases: list[str] = Field(default_factory=list, description="口头禅")
    sentence_style: str = Field(default="", description="句式偏好")


class CharacterMetadata(BaseModel):
    era: str | None = None
    region: str | None = None
    occupation: str | None = None
    languages: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class Character(BaseModel):
    """数字人角色 — Day 2 核心数据模型"""

    id: str = Field(..., description="平台侧唯一 ID")
    name: str = Field(..., min_length=1, max_length=64)
    avatar_url: HttpUrl | None = None
    personality_traits: list[str] = Field(default_factory=list)
    backstory: str = Field(..., min_length=1, description="人物小传原文")
    speaking_style: SpeakingStyle = Field(default_factory=SpeakingStyle)
    boundaries: list[str] = Field(default_factory=list)
    memory_seed: str = Field(default="", description="角色关于自己的初始记忆")
    voice_id: str = Field(default="")
    metadata: CharacterMetadata = Field(default_factory=CharacterMetadata)

    model_config = {"extra": "allow"}  # 平台字段可能比 schema 多


# ============= Day 2 占位的"轻量角色"类型 =============

CharacterCategory = Literal["ip_owned", "ip_subscribed", "demo"]


class CharacterSummary(BaseModel):
    """角色列表（轻量，不含完整小传）"""

    id: str
    name: str
    avatar_url: HttpUrl | None = None
    tags: list[str] = Field(default_factory=list)
    category: CharacterCategory = "ip_owned"
