"""记忆提取器 — Phase 1 (Loop 1+2)

职责: /chat 结束后,异步从对话中提取用户相关 fact,去重后入库。

设计:
- Protocol 接口,后续可换实现 (NoopExtractor / HaikuExtractor)
- NoopExtractor — 占位,Loop 1 用
- HaikuExtractor — 用小模型 + JSON prompt 提取,Loop 2 实现

为什么 async + fire-and-forget:
- /chat 不应该等提取(可能慢)
- asyncio.create_task 让提取在后台跑
- 失败不让 /chat 失败(只记日志)
"""
from __future__ import annotations

import json
import logging
import re
from typing import Protocol

from app.llm.base import LLMProvider
from app.memory.schemas import FactCategory
from app.memory.store import MemoryStore

logger = logging.getLogger(__name__)


# ===== Protocol =====


class MemoryExtractor(Protocol):
    """记忆提取器协议 — 所有实现必须遵守"""

    async def extract(
        self,
        user_id: str,
        character_id: str,
        turns: list[dict],
    ) -> list[dict]:
        """从对话 turns 提取 fact

        Args:
            user_id: 用户 ID
            character_id: 角色 ID
            turns: 对话 turns — [{"role": "user"|"assistant", "content": "..."}, ...]

        Returns:
            提取出的 fact 列表 — [{"content": "...", "category": "..."}, ...]
        """
        ...


# ===== Noop 实现 (Loop 1) =====


class NoopExtractor:
    """空实现 — 不提取任何东西,只占位"""

    async def extract(
        self,
        user_id: str,
        character_id: str,
        turns: list[dict],
    ) -> list[dict]:
        logger.debug(
            "noop_extractor user=%s char=%s turns=%d (no-op)",
            user_id, character_id, len(turns),
        )
        return []


# ===== Haiku 实现 (Loop 2) =====


_EXTRACTION_PROMPT = """你是用户信息提取助手。从以下对话中提取**关于用户本人**的事实。

要求:
1. 只提取用户**说过**或**透露**的信息,不提取助手说的话
2. 每条 fact 用一句简洁中文表达
3. category 必须是以下之一: basic (姓名/年龄/城市), preference (喜好/厌恶), relationship (家人/朋友/伴侣), work (工作/职业/项目), event (重要事件/生日)
4. **没有就返回 []**,不要编造
5. **只返回 JSON 数组**,不要任何其他文字

示例输入:
user: 我叫小明,在杭州做产品经理,平时喜欢爵士乐
assistant: 你好小明!

示例输出:
[{{"content": "用户叫小明", "category": "basic"}}, {{"content": "用户在杭州", "category": "basic"}}, {{"content": "用户是产品经理", "category": "work"}}, {{"content": "用户喜欢爵士乐", "category": "preference"}}]

对话:
{conversation}

输出:"""


class HaikuExtractor:
    """用 LLM (推荐 Haiku — 便宜) 提取 fact

    流程:
    1. 拼 prompt + 对话内容
    2. 调 LLM
    3. 解析 JSON
    4. 去重(子串匹配 — 后续可换 Qdrant 语义)
    5. 入库
    """

    def __init__(self, provider: LLMProvider, memory: MemoryStore):
        self.provider = provider
        self.memory = memory

    async def extract(
        self,
        user_id: str,
        character_id: str,
        turns: list[dict],
    ) -> list[dict]:
        # 1. 拼对话文本
        lines = []
        for t in turns:
            role = "user" if t.get("role") == "user" else "assistant"
            lines.append(f"{role}: {t.get('content', '')}")
        conversation = "\n".join(lines)
        prompt = _EXTRACTION_PROMPT.format(conversation=conversation)

        # 2. 调 LLM
        try:
            response = await self.provider.chat(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500,
                temperature=0.0,  # 提取要确定性
            )
        except Exception as e:
            logger.exception("extractor LLM call failed user=%s", user_id)
            return []

        # 3. 解析 JSON
        try:
            candidates = _parse_json_array(response.text)
        except Exception as e:
            logger.warning(
                "extractor bad JSON user=%s text=%r err=%s",
                user_id, response.text[:200], e,
            )
            return []

        # 4. 去重 + 入库
        existing = await self.memory.list_all(user_id)
        existing_contents = [f.content for f in existing]
        saved: list[dict] = []
        for c in candidates:
            content = c.get("content", "").strip()
            if not content:
                continue
            category_str = c.get("category", "basic")
            try:
                category = FactCategory(category_str)
            except ValueError:
                category = FactCategory.BASIC

            # 简单子串去重 — 后续接 Qdrant 做语义去重
            if any(_is_duplicate(content, e) for e in existing_contents):
                logger.debug("extractor dedup hit: %r", content)
                continue

            try:
                await self.memory.add(
                    user_id=user_id,
                    category=category,
                    content=content,
                    source="extractor",
                )
                saved.append({"content": content, "category": category.value})
                existing_contents.append(content)
            except Exception:
                logger.exception("extractor save failed user=%s content=%r", user_id, content)

        logger.info(
            "extractor_done user=%s char=%s candidates=%d saved=%d",
            user_id, character_id, len(candidates), len(saved),
        )
        return saved


# ===== 工具函数 =====


def _parse_json_array(text: str) -> list[dict]:
    """从 LLM 输出中提取 JSON 数组 — 容错处理 markdown 围栏"""
    text = text.strip()
    # 去掉 markdown code fence
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # 提取第一个 [ 到 ] 的内容
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        text = m.group(0)
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("not a list")
    return data


def _is_duplicate(a: str, b: str) -> bool:
    """简单子串匹配 — '用户叫小明' 和 '用户叫小明了' 算重复"""
    if not a or not b:
        return False
    shorter, longer = sorted([a, b], key=len)
    return shorter in longer

