"""API 请求/响应模型 — Pydantic v2"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class HistoryTurn(BaseModel):
    """客户端发来的对话历史一条 — 由 iOS SQLite 提供,后端不持久化"""
    role: Literal["user", "assistant"] = Field(..., description="user 或 assistant")
    content: str = Field(..., min_length=1, max_length=8000, description="消息内容")


class ChatRequest(BaseModel):
    user_id: str = Field(..., description="用户 ID（Day 1 mock 即可）")
    character_id: str = Field(default="demo", description="数字人角色 ID")
    message: str = Field(..., min_length=1, max_length=4000, description="用户消息")
    history: list[HistoryTurn] = Field(
        default_factory=list,
        description="客户端提供的最近 N 轮对话历史 — 后端只用作 LLM 上下文,不持久化。"
        "Loop 13 重构:4 层记忆全在 iOS SQLite,后端改无状态。",
    )


class ChatResponse(BaseModel):
    reply: str
    model: str
    input_tokens: int
    output_tokens: int
    stop_reason: str
    iterations: int = Field(default=1, description="Agent 循环轮次 — 1 表示没调工具")
    memory_ops: list[dict] = Field(
        default_factory=list,
        description="本轮调用的工具及其结果 — 调试用",
    )
    cache_creation_tokens: int = Field(
        default=0,
        description="本轮创建缓存的 tokens 数 — 仅首次调用会有值",
    )
    cache_read_tokens: int = Field(
        default=0,
        description="本轮从缓存读取的 tokens 数 — 命中后通常占大头",
    )


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"] = "ok"
    provider: str
    model: str
    env: str


class MemoryListResponse(BaseModel):
    user_id: str
    count: int
    facts: list[dict]


class MemoryForgetRequest(BaseModel):
    user_id: str
    fact_id: str
