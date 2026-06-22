"""Agent 工具定义 + 执行器

每个工具有两部分：
1. definition: Anthropic API 格式的 tool schema
2. executor: 实际执行逻辑（注入 user_id，自动从 tool input 取其他参数）
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.memory.schemas import FactCategory
from app.memory.store import MemoryStore

logger = logging.getLogger(__name__)


@dataclass
class ToolDef:
    """工具定义"""

    name: str
    description: str
    input_schema: dict
    executor: Callable[..., Awaitable[dict]]


# ===== 工具定义 =====

SEARCH_MEMORY_DEF: dict = {
    "name": "search_memory",
    "description": (
        "在用户的长期记忆里搜索相关的事实。"
        "当用户提到过去的事情、问'你还记得吗'、或你需要回忆用户的背景时使用。"
        "返回最相关的几条事实。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词或问题，例如'工作'、'宠物'、'生日'",
            },
            "category": {
                "type": "string",
                "enum": [c.value for c in FactCategory],
                "description": "可选，按类别筛选",
            },
        },
        "required": ["query"],
    },
}

SAVE_FACT_DEF: dict = {
    "name": "save_fact",
    "description": (
        "保存一条关于用户的事实到长期记忆。"
        "当用户分享了值得记住的信息（姓名、工作、喜好、重要事件等）时调用。"
        "已经在记忆中存在的相同事实会被自动去重，不会重复保存。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "事实描述，简短完整，例如'用户叫小明，是软件工程师'",
            },
            "category": {
                "type": "string",
                "enum": [c.value for c in FactCategory],
                "description": "事实类别",
            },
        },
        "required": ["content", "category"],
    },
}

LIST_FACTS_DEF: dict = {
    "name": "list_user_facts",
    "description": (
        "列出用户的所有记忆事实，可按类别筛选。"
        "用于概览用户画像，或在 search_memory 没找到时全量查看。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": [c.value for c in FactCategory],
                "description": "可选，按类别筛选",
            },
        },
    },
}

FORGET_FACT_DEF: dict = {
    "name": "forget_fact",
    "description": (
        "从长期记忆中删除一条事实。"
        "当用户明确要求'忘掉这件事'或纠正了之前的事实时调用。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "fact_id": {
                "type": "string",
                "description": "事实 ID（从 search_memory 或 list_user_facts 返回中获取）",
            },
        },
        "required": ["fact_id"],
    },
}


# ===== 执行器 =====


async def _exec_search_memory(
    memory: MemoryStore,
    user_id: str,
    inp: dict,
) -> dict:
    category_str = inp.get("category")
    category = FactCategory(category_str) if category_str else None
    facts = await memory.search(
        user_id=user_id,
        query=inp["query"],
        top_k=5,
        category=category,
    )
    return {
        "count": len(facts),
        "facts": [f.to_dict() for f in facts],
    }


async def _exec_save_fact(
    memory: MemoryStore,
    user_id: str,
    inp: dict,
) -> dict:
    try:
        category = FactCategory(inp["category"])
    except ValueError:
        return {"error": f"unknown category: {inp['category']}"}
    fact = await memory.add(
        user_id=user_id,
        category=category,
        content=inp["content"],
    )
    return {
        "id": fact.id,
        "category": fact.category.value,
        "content": fact.content,
        "status": "saved",
    }


async def _exec_list_facts(
    memory: MemoryStore,
    user_id: str,
    inp: dict,
) -> dict:
    category_str = inp.get("category")
    category = FactCategory(category_str) if category_str else None
    facts = await memory.list_all(user_id=user_id, category=category)
    return {
        "count": len(facts),
        "facts": [f.to_dict() for f in facts],
    }


async def _exec_forget_fact(
    memory: MemoryStore,
    user_id: str,
    inp: dict,
) -> dict:
    ok = await memory.forget(user_id=user_id, fact_id=inp["fact_id"])
    return {"fact_id": inp["fact_id"], "status": "forgotten" if ok else "not_found"}


# ===== Registry =====


class ToolRegistry:
    """工具注册表 — 集中管理所有工具的定义和执行"""

    def __init__(self, memory: MemoryStore):
        self.memory = memory

    @property
    def definitions(self) -> list[dict]:
        return [SEARCH_MEMORY_DEF, SAVE_FACT_DEF, LIST_FACTS_DEF, FORGET_FACT_DEF]

    async def execute(
        self,
        name: str,
        tool_input: dict,
        user_id: str,
    ) -> dict:
        """执行一个工具调用 — user_id 由 runtime 注入"""
        logger.info(f"tool call user={user_id} name={name} input={tool_input}")
        try:
            if name == "search_memory":
                return await _exec_search_memory(self.memory, user_id, tool_input)
            if name == "save_fact":
                return await _exec_save_fact(self.memory, user_id, tool_input)
            if name == "list_user_facts":
                return await _exec_list_facts(self.memory, user_id, tool_input)
            if name == "forget_fact":
                return await _exec_forget_fact(self.memory, user_id, tool_input)
            return {"error": f"unknown tool: {name}"}
        except Exception as e:
            logger.exception(f"tool execution failed: {name}")
            return {"error": f"{type(e).__name__}: {e}"}

    async def execute_all(
        self,
        tool_calls: list,
        user_id: str,
    ) -> list[dict]:
        """批量执行多个工具调用 — 返回 tool_result 块列表"""
        results: list[dict] = []
        for tc in tool_calls:
            payload = await self.execute(tc.name, tc.input, user_id)
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": json.dumps(payload, ensure_ascii=False),
                }
            )
        return results
