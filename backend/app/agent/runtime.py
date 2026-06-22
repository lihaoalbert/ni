"""Agent Runtime — 同步 + 流式双版本

Day 3: 同步版本 .run()
Day 5: 流式版本 .run_stream() — 边生成边 yield 事件
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import AsyncIterator

from app.agent.tools import ToolRegistry
from app.llm.base import LLMProvider, ToolCall

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    """Agent 一次调用的完整结果（同步版）"""

    text: str
    iterations: int
    tool_calls: list[dict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0


class AgentRuntime:
    """简易 Agent 循环 — 同步 + 流式"""

    def __init__(
        self,
        provider: LLMProvider,
        tools: ToolRegistry,
        max_iterations: int = 5,
    ):
        self.provider = provider
        self.tools = tools
        self.max_iterations = max_iterations

    # ===== 同步版（Day 3）=====

    async def run(
        self,
        system: str,
        user_message: str,
        user_id: str,
        history: list[dict] | None = None,
        max_tokens: int = 512,
    ) -> AgentResult:
        messages: list[dict] = []
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})
        tool_defs = self.tools.definitions
        all_tool_calls: list[dict] = []
        total_input = 0
        total_output = 0
        total_cache_creation = 0
        total_cache_read = 0
        model_name = ""
        final_text = ""
        iteration = 0

        for iteration in range(1, self.max_iterations + 1):
            response = await self.provider.chat(
                messages=messages,
                system=system,
                tools=tool_defs,
                max_tokens=max_tokens,
            )
            total_input += response.input_tokens
            total_output += response.output_tokens
            total_cache_creation += response.cache_creation_tokens
            total_cache_read += response.cache_read_tokens
            model_name = response.model

            logger.debug(
                f"agent iter={iteration} stop={response.stop_reason} "
                f"text_len={len(response.text)} tool_calls={len(response.tool_calls)}"
            )

            if response.stop_reason == "end_turn":
                final_text = response.text
                break

            if response.stop_reason == "max_tokens":
                logger.warning(f"agent iter={iteration} hit max_tokens")
                final_text = response.text or "（回复过长被截断）"
                break

            if response.stop_reason == "tool_use" and response.tool_calls:
                assistant_content: list[dict] = []
                if response.text:
                    assistant_content.append({"type": "text", "text": response.text})
                for tc in response.tool_calls:
                    assistant_content.append(
                        {
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.input,
                        }
                    )
                messages.append({"role": "assistant", "content": assistant_content})

                tool_results = await self.tools.execute_all(response.tool_calls, user_id)
                messages.append({"role": "user", "content": tool_results})

                for tc, result_block in zip(response.tool_calls, tool_results):
                    try:
                        result_payload = json.loads(result_block["content"])
                    except Exception:
                        result_payload = {"raw": result_block["content"]}
                    all_tool_calls.append(
                        {
                            "name": tc.name,
                            "input": tc.input,
                            "result": result_payload,
                        }
                    )

                continue

            logger.warning(f"agent iter={iteration} unexpected stop={response.stop_reason}")
            final_text = response.text
            break

        return AgentResult(
            text=final_text,
            iterations=iteration,
            tool_calls=all_tool_calls,
            input_tokens=total_input,
            output_tokens=total_output,
            model=model_name,
            cache_creation_tokens=total_cache_creation,
            cache_read_tokens=total_cache_read,
        )

    # ===== 流式版（Day 5）=====

    async def run_stream(
        self,
        system: str,
        user_message: str,
        user_id: str,
        history: list[dict] | None = None,
        max_tokens: int = 512,
    ) -> AsyncIterator[dict]:
        """流式 Agent 循环 — 边生成边 yield 事件

        事件类型（JSON 可序列化）：
        - {"type": "text", "text": "..."}
        - {"type": "tool_use_start", "tool_id": "...", "tool_name": "..."}
        - {"type": "tool_use_input_delta", "tool_id": "...", "partial_json": "..."}
        - {"type": "tool_result", "tool_id": "...", "result": {...}}
        - {"type": "iter_end", "iteration": N, "stop_reason": "..."}
        - {"type": "done", "text": "...", "iterations": N, "tool_calls": [...]}
        - {"type": "error", "error": "..."}
        """
        messages: list[dict] = []
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})
        tool_defs = self.tools.definitions
        all_tool_calls: list[dict] = []
        total_input = 0
        total_output = 0
        total_cache_creation = 0
        total_cache_read = 0

        try:
            for iteration in range(1, self.max_iterations + 1):
                accumulated_text = ""
                # tool_id -> {name, input_json_str}
                accumulating_tools: dict[str, dict] = {}
                final_stop_reason = ""
                final_model = self.provider.model
                iter_input = iter_output = 0
                iter_cache_create = iter_cache_read = 0

                # 收集一轮的流事件
                async for event in self.provider.stream_chat(
                    messages=messages,
                    system=system,
                    tools=tool_defs,
                    max_tokens=max_tokens,
                ):
                    if event.type == "text":
                        accumulated_text += event.text
                        yield {"type": "text", "text": event.text}

                    elif event.type == "tool_use_start":
                        accumulating_tools[event.tool_id] = {
                            "name": event.tool_name,
                            "input_json": "",
                        }
                        yield {
                            "type": "tool_use_start",
                            "tool_id": event.tool_id,
                            "tool_name": event.tool_name,
                        }

                    elif event.type == "tool_use_input_delta":
                        if event.tool_id in accumulating_tools:
                            accumulating_tools[event.tool_id][
                                "input_json"
                            ] += event.partial_json
                        yield {
                            "type": "tool_use_input_delta",
                            "tool_id": event.tool_id,
                            "partial_json": event.partial_json,
                        }

                    elif event.type == "message_stop":
                        final_stop_reason = event.stop_reason or "end_turn"
                        final_model = event.model or final_model
                        iter_input = event.input_tokens
                        iter_output = event.output_tokens
                        iter_cache_create = event.cache_creation_tokens
                        iter_cache_read = event.cache_read_tokens

                    elif event.type == "error":
                        yield {"type": "error", "error": event.error}
                        return

                # 一轮结束
                total_input += iter_input
                total_output += iter_output
                total_cache_creation += iter_cache_create
                total_cache_read += iter_cache_read

                yield {
                    "type": "iter_end",
                    "iteration": iteration,
                    "stop_reason": final_stop_reason,
                }

                # 决策
                if final_stop_reason == "end_turn":
                    yield {
                        "type": "done",
                        "text": accumulated_text,
                        "iterations": iteration,
                        "tool_calls": all_tool_calls,
                        "input_tokens": total_input,
                        "output_tokens": total_output,
                        "cache_creation_tokens": total_cache_creation,
                        "cache_read_tokens": total_cache_read,
                        "model": final_model,
                    }
                    return

                if final_stop_reason == "tool_use" and accumulating_tools:
                    # 构造 assistant 消息
                    assistant_content: list[dict] = []
                    if accumulated_text:
                        assistant_content.append(
                            {"type": "text", "text": accumulated_text}
                        )
                    for tc_id, tc in accumulating_tools.items():
                        try:
                            input_dict = (
                                json.loads(tc["input_json"])
                                if tc["input_json"]
                                else {}
                            )
                        except json.JSONDecodeError:
                            logger.error(
                                f"tool input json decode failed: {tc['input_json']}"
                            )
                            input_dict = {}
                        assistant_content.append(
                            {
                                "type": "tool_use",
                                "id": tc_id,
                                "name": tc["name"],
                                "input": input_dict,
                            }
                        )
                    messages.append({"role": "assistant", "content": assistant_content})

                    # 执行工具
                    tool_calls_list = [
                        ToolCall(
                            id=tc_id,
                            name=tc["name"],
                            input=(
                                json.loads(tc["input_json"])
                                if tc["input_json"]
                                else {}
                            ),
                        )
                        for tc_id, tc in accumulating_tools.items()
                    ]
                    tool_results = await self.tools.execute_all(
                        tool_calls_list, user_id
                    )
                    messages.append({"role": "user", "content": tool_results})

                    # 记录 + yield
                    for tc, result_block in zip(tool_calls_list, tool_results):
                        try:
                            result_payload = json.loads(result_block["content"])
                        except Exception:
                            result_payload = {"raw": result_block["content"]}
                        all_tool_calls.append(
                            {
                                "name": tc.name,
                                "input": tc.input,
                                "result": result_payload,
                            }
                        )
                        yield {
                            "type": "tool_result",
                            "tool_id": tc.id,
                            "result": result_payload,
                        }

                    continue

                # max_tokens 或其他
                yield {
                    "type": "done",
                    "text": accumulated_text or "（回复被截断）",
                    "iterations": iteration,
                    "tool_calls": all_tool_calls,
                    "input_tokens": total_input,
                    "output_tokens": total_output,
                    "cache_creation_tokens": total_cache_creation,
                    "cache_read_tokens": total_cache_read,
                    "model": final_model,
                }
                return

            # 超 max_iterations
            yield {
                "type": "done",
                "text": "（超过最大轮次）",
                "iterations": self.max_iterations,
                "tool_calls": all_tool_calls,
                "input_tokens": total_input,
                "output_tokens": total_output,
                "cache_creation_tokens": total_cache_creation,
                "cache_read_tokens": total_cache_read,
                "model": "",
            }

        except Exception as e:
            logger.exception("agent run_stream failed")
            yield {"type": "error", "error": f"{type(e).__name__}: {e!s}"}
