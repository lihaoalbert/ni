"""Character 模块测试 — loader + prompt + schemas"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.characters.loader import CharacterNotFound, LocalFileLoader
from app.characters.prompt import build_character_system_prompt
from app.characters.schemas import Character


@pytest.fixture
def characters_dir(tmp_path: Path) -> Path:
    suwan = {
        "id": "suwan",
        "name": "苏晚",
        "personality_traits": ["温柔", "内敛"],
        "backstory": "苏晚，28 岁，建筑设计师。",
        "speaking_style": {
            "tone": "温和、克制",
            "catchphrases": ["我觉得…"],
            "sentence_style": "中等长度句子",
        },
        "boundaries": ["不讨论政治"],
        "memory_seed": "我叫苏晚。",
        "voice_id": "v1",
    }
    (tmp_path / "suwan.json").write_text(json.dumps(suwan, ensure_ascii=False))
    return tmp_path


@pytest.mark.asyncio
async def test_local_loader_loads_suwan(characters_dir: Path) -> None:
    loader = LocalFileLoader(characters_dir)
    char = await loader.get("suwan")
    assert char.id == "suwan"
    assert char.name == "苏晚"
    assert "建筑设计师" in char.backstory
    assert "温和" in char.speaking_style.tone


@pytest.mark.asyncio
async def test_local_loader_caches(characters_dir: Path) -> None:
    loader = LocalFileLoader(characters_dir)
    char1 = await loader.get("suwan")
    char2 = await loader.get("suwan")
    assert char1 is char2  # 同一对象，缓存生效


@pytest.mark.asyncio
async def test_local_loader_raises_on_missing(characters_dir: Path) -> None:
    loader = LocalFileLoader(characters_dir)
    with pytest.raises(CharacterNotFound):
        await loader.get("nonexistent")


@pytest.mark.asyncio
async def test_local_loader_list_ids(characters_dir: Path) -> None:
    loader = LocalFileLoader(characters_dir)
    ids = await loader.list_ids()
    assert ids == ["suwan"]


def test_character_schema_validates_full_payload() -> None:
    """完整字段应该解析通过"""
    payload = {
        "id": "suwan",
        "name": "苏晚",
        "avatar_url": "https://example.com/a.png",
        "personality_traits": ["温柔"],
        "backstory": "苏晚，建筑设计师。",
        "speaking_style": {"tone": "温和"},
        "boundaries": ["不讨论政治"],
        "memory_seed": "我叫苏晚。",
        "voice_id": "v1",
        "metadata": {"region": "上海", "tags": ["文艺"]},
    }
    char = Character.model_validate(payload)
    assert char.metadata.region == "上海"
    assert char.metadata.tags == ["文艺"]


def test_character_schema_allows_extra_fields() -> None:
    """平台字段可能比 schema 多 — 应当宽容"""
    payload = {
        "id": "x",
        "name": "X",
        "backstory": "test",
        "platform_specific_field": "should not fail",
    }
    char = Character.model_validate(payload)
    assert char.id == "x"


# ===== Prompt 转换器测试 =====


def test_prompt_uses_first_person_and_identity() -> None:
    char = Character(id="suwan", name="苏晚", backstory="苏晚，建筑设计师。")
    prompt = build_character_system_prompt(char)
    assert "你是 苏晚" in prompt
    assert "第一人称" in prompt
    assert "苏晚，建筑设计师。" in prompt


def test_prompt_includes_speaking_style() -> None:
    char = Character(
        id="suwan",
        name="苏晚",
        backstory="x",
        speaking_style={
            "tone": "温和、克制",
            "catchphrases": ["我觉得…", "嗯…让我想想"],
            "sentence_style": "中等长度",
        },
    )
    prompt = build_character_system_prompt(char)
    assert "温和、克制" in prompt
    assert "我觉得…" in prompt
    assert "嗯…让我想想" in prompt


def test_prompt_includes_boundaries() -> None:
    char = Character(
        id="x",
        name="X",
        backstory="x",
        boundaries=["不讨论政治", "不提供医疗建议"],
    )
    prompt = build_character_system_prompt(char)
    assert "不讨论政治" in prompt
    assert "不提供医疗建议" in prompt
    # 应当说"礼貌地绕开"，而不是"我不能回答"
    assert "礼貌" in prompt


def test_prompt_includes_memory_seed() -> None:
    char = Character(
        id="suwan",
        name="苏晚",
        backstory="x",
        memory_seed="我叫苏晚，养了一只橘猫叫小满。",
    )
    prompt = build_character_system_prompt(char)
    assert "小满" in prompt


def test_prompt_handles_minimal_character() -> None:
    """只有必填字段也应该能生成可用 prompt"""
    char = Character(id="x", name="X", backstory="x")
    prompt = build_character_system_prompt(char)
    assert "你是 X" in prompt
    assert "回复" in prompt or "聊天" in prompt


def test_prompt_length_reasonable() -> None:
    """prompt 不应过长（影响成本）"""
    char = Character(
        id="suwan",
        name="苏晚",
        backstory="苏晚，建筑设计师。喜欢爵士乐，养了一只猫叫小满。",
        personality_traits=["温柔", "内敛"],
        boundaries=["不讨论政治"],
        memory_seed="我叫苏晚。",
    )
    prompt = build_character_system_prompt(char)
    # 苏晚这种典型角色 prompt 应 < 1500 中文字符
    assert len(prompt) < 1500
