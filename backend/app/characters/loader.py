"""Character Loader — 从本地 JSON 或未来平台 API 加载

设计要点：
- Protocol 接口，未来可换 HttpApiLoader
- LRU 缓存避免每次请求都读盘
- 错误明确：找不到角色抛 CharacterNotFound
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.characters.schemas import Character

logger = logging.getLogger(__name__)


class CharacterNotFound(Exception):
    """角色不存在 — HTTP 层应映射为 404"""


class CharacterLoader(Protocol):
    async def get(self, character_id: str) -> Character: ...
    async def list_ids(self) -> list[str]: ...


class LocalFileLoader:
    """从本地 JSON 目录加载角色（Demo 阶段）"""

    def __init__(self, characters_dir: Path):
        self._dir = characters_dir
        self._cache: dict[str, Character] = {}

    def _load_file(self, character_id: str) -> Character:
        path = self._dir / f"{character_id}.json"
        if not path.exists():
            raise CharacterNotFound(f"character not found: {character_id}")
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return Character.model_validate(data)

    async def get(self, character_id: str) -> Character:
        if character_id not in self._cache:
            logger.info(f"loading character from disk: {character_id}")
            self._cache[character_id] = self._load_file(character_id)
        return self._cache[character_id]

    async def list_ids(self) -> list[str]:
        if not self._dir.exists():
            return []
        return sorted(p.stem for p in self._dir.glob("*.json"))


@lru_cache
def get_character_loader() -> CharacterLoader:
    """单例 loader — 缓存目录来自配置"""
    from app.config import get_settings

    # 暂时硬编码 data/characters 目录，后续可移到 settings
    return LocalFileLoader(Path(__file__).resolve().parents[2] / "data" / "characters")
