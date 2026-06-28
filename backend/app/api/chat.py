"""/chat 端点 — Day 3-6：Agent 循环 + 永久记忆 + 流式 + 错误处理 + 访问日志"""
from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.agent.runtime import AgentRuntime
from app.agent.tools import ToolRegistry
from app.api.errors import to_http_exception, to_sse_error_event
from app.characters.loader import CharacterLoader, CharacterNotFound, get_character_loader
from app.characters.prompt import build_character_system_prompt
from app.config import Settings, get_settings
from app.llm.base import LLMProvider
from app.llm.factory import get_llm_provider
from app.logging_setup import log_chat_call
from app.memory.extractor import MemoryExtractor, NoopExtractor
from app.memory.schemas import FactCategory
from app.memory.store import MemoryStore, get_memory_store
from app.schemas import (
    ChatRequest,
    ChatResponse,
    HealthResponse,
    MemoryListResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def get_tool_registry(memory: MemoryStore = Depends(get_memory_store)) -> ToolRegistry:
    return ToolRegistry(memory)


def get_agent(
    provider: LLMProvider = Depends(get_llm_provider),
    tools: ToolRegistry = Depends(get_tool_registry),
) -> AgentRuntime:
    return AgentRuntime(provider=provider, tools=tools)


# Phase 1: 记忆提取器
# 默认 NoopExtractor — 安全占位,不调 LLM,既有测试不破坏
# 生产: 把 memory_pipeline_enabled=True (或在 .env 设 MEMORY_PIPELINE_ENABLED=true)
def get_extractor(
    settings: Settings = Depends(get_settings),
    provider: LLMProvider = Depends(get_llm_provider),
    memory: MemoryStore = Depends(get_memory_store),
) -> MemoryExtractor:
    if settings.memory_pipeline_enabled:
        from app.memory.extractor import HaikuExtractor
        return HaikuExtractor(provider=provider, memory=memory)
    return NoopExtractor()


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    agent: AgentRuntime = Depends(get_agent),
    loader: CharacterLoader = Depends(get_character_loader),
    settings: Settings = Depends(get_settings),
    extractor: MemoryExtractor = Depends(get_extractor),
) -> ChatResponse:
    """Day 6: 错误处理 + 访问日志 + Agent 循环 + 永久记忆

    Loop 13 重构:4 层记忆全在 iOS SQLite,后端改无状态。
    - history 直接用 request.history(客户端发来)
    - 不再调 conversations.append / get_history
    - 角色失忆的根因是后端 ConversationStore 重启即丢 → 改无状态后 iOS 自己带历史,稳了

    流程：
    1. 加载角色 → 构造 system prompt
    2. 用客户端发的 history 作为 LLM 上下文
    3. AgentRuntime.run() 循环（内部已重试 + 超时）
    4. 异常 → 映射到合适 HTTP 状态码 + 友好 detail
    5. log_chat_call 记录 metrics
    """
    request_id = uuid.uuid4().hex[:12]

    with log_chat_call(
        request_id=request_id,
        user_id=request.user_id,
        character_id=request.character_id,
    ) as metrics:
        try:
            character = await loader.get(request.character_id)
        except CharacterNotFound as e:
            metrics.status = "error"
            metrics.error = "character_not_found"
            raise HTTPException(
                status_code=404, detail=f"角色不存在: {request.character_id}"
            ) from e

        system_prompt = build_character_system_prompt(character)

        history_dicts = [
            {"role": t.role, "content": t.content} for t in request.history
        ]

        try:
            result = await agent.run(
                system=system_prompt,
                user_message=request.message,
                user_id=request.user_id,
                history=history_dicts,
                max_tokens=512,
            )
        except Exception as e:
            # 已重试 + 已超时，最后还是失败 → 映射
            raise to_http_exception(e) from e

        # 触发记忆提取（fire-and-forget,失败不阻塞 chat）
        import asyncio
        latest_turns = [
            {"role": "user", "content": request.message},
            {"role": "assistant", "content": result.text},
        ]
        task = asyncio.create_task(
            extractor.extract(request.user_id, request.character_id, latest_turns)
        )
        # 把 task 加到 metrics 以便保留引用,避免 GC 警告
        metrics.extra["extractor_task"] = task

        # 填充 metrics（在 with 块结束时会自动 log）
        metrics.iterations = result.iterations
        metrics.tool_calls = len(result.tool_calls)
        metrics.input_tokens = result.input_tokens
        metrics.output_tokens = result.output_tokens
        metrics.cache_creation_tokens = result.cache_creation_tokens
        metrics.cache_read_tokens = result.cache_read_tokens
        metrics.extra["model"] = result.model
        metrics.extra["history_len"] = len(history_dicts)

    return ChatResponse(
        reply=result.text,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        stop_reason="end_turn" if result.iterations > 0 else "error",
        iterations=result.iterations,
        memory_ops=result.tool_calls,
        cache_creation_tokens=result.cache_creation_tokens,
        cache_read_tokens=result.cache_read_tokens,
    )


@router.get("/health", response_model=HealthResponse)
async def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    return HealthResponse(
        status="ok",
        provider=settings.llm_provider,
        model=settings.claude_model_main,
        env=settings.app_env,
    )


@router.get("/characters")
async def list_characters(
    loader: CharacterLoader = Depends(get_character_loader),
) -> dict:
    """列出可用角色 ID — MVP 阶段调试用"""
    ids = await loader.list_ids()
    return {"count": len(ids), "ids": ids}


# ===== Memory 调试端点 =====


@router.get("/memories/{user_id}", response_model=MemoryListResponse)
async def list_memories(
    user_id: str,
    category: str | None = None,
    memory: MemoryStore = Depends(get_memory_store),
) -> MemoryListResponse:
    cat = FactCategory(category) if category else None
    facts = await memory.list_all(user_id=user_id, category=cat)
    return MemoryListResponse(
        user_id=user_id,
        count=len(facts),
        facts=[f.to_dict() for f in facts],
    )


@router.delete("/memories/{user_id}/{fact_id}")
async def forget_memory(
    user_id: str,
    fact_id: str,
    memory: MemoryStore = Depends(get_memory_store),
) -> dict:
    ok = await memory.forget(user_id=user_id, fact_id=fact_id)
    return {"fact_id": fact_id, "forgotten": ok}


# ===== Day 5: 流式 SSE 端点 =====


@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    agent: AgentRuntime = Depends(get_agent),
    loader: CharacterLoader = Depends(get_character_loader),
) -> StreamingResponse:
    """Day 5+6: 流式聊天 — Server-Sent Events + 错误处理

    Loop 13 重构:4 层记忆全在 iOS SQLite,后端改无状态。
    history 用 request.history,不再调 conversations。

    事件序列（每行 `data: <json>\\n\\n`）：
    - {type:"text", text:"..."}                  文本增量
    - {type:"tool_use_start", tool_id, tool_name}  工具调用开始
    - {type:"tool_use_input_delta", ...}          工具输入 JSON 增量
    - {type:"tool_result", tool_id, result}       工具执行结果
    - {type:"iter_end", iteration, stop_reason}   一轮结束
    - {type:"done", text, iterations, ...}        全部完成
    - {type:"error", kind, error, upstream_status} 出错

    错误处理：
    - LLM 异常（已重试 + 超时）→ 末尾 yield 友好 error 事件
    - 角色不存在 → 端点直接 404
    - 客户端断线 → FastAPI 取消生成器，让 CancelledError 透传
    """
    request_id = uuid.uuid4().hex[:12]
    metrics_extra: dict = {}

    try:
        character = await loader.get(request.character_id)
    except CharacterNotFound as e:
        raise HTTPException(
            status_code=404, detail=f"角色不存在: {request.character_id}"
        ) from e

    system_prompt = build_character_system_prompt(character)
    history_dicts = [
        {"role": t.role, "content": t.content} for t in request.history
    ]

    async def event_generator():
        accumulated_text = ""
        last_error: Exception | None = None
        try:
            with log_chat_call(
                request_id=request_id,
                user_id=request.user_id,
                character_id=request.character_id,
            ) as metrics:
                async for event in agent.run_stream(
                    system=system_prompt,
                    user_message=request.message,
                    user_id=request.user_id,
                    history=history_dicts,
                    max_tokens=512,
                ):
                    # agent 内部已经把 LLM 异常翻译成 error 事件；这里不处理 type=text 之外的累加
                    if event.get("type") == "text":
                        accumulated_text += event.get("text", "")
                    if event.get("type") == "done":
                        metrics.iterations = event.get("iterations", 0)
                        metrics.tool_calls = len(event.get("tool_calls", []))
                        metrics.input_tokens = event.get("input_tokens", 0)
                        metrics.output_tokens = event.get("output_tokens", 0)
                        metrics.cache_creation_tokens = event.get("cache_creation_tokens", 0)
                        metrics.cache_read_tokens = event.get("cache_read_tokens", 0)
                        metrics.extra["model"] = event.get("model", "")
                    if event.get("type") == "error":
                        # LLM 在流中报错了
                        metrics.status = "error"
                        metrics.error = event.get("error", "unknown")
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            # agent 之外的异常（一般是 generator 内的） → 翻译为 SSE error
            logger.exception("chat_stream failed")
            last_error = e
            yield f"data: {json.dumps(to_sse_error_event(e), ensure_ascii=False)}\n\n"

        # Loop 13: 后端不再写历史,iOS 已本地落 SQLite。
        # 流正常结束只打个 metric,失败不补任何东西。

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
