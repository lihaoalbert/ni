"""Sample IP 数据 — 3 个 demo 数字人(对应 /Users/app/ni/docs/ips-api-requirements.md schema)。"""
from __future__ import annotations

from pathlib import Path
import json


_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "characters.json"


def _load() -> list[dict]:
    with _DATA_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def list_ips(status: str | None = None) -> list[dict]:
    items = _load()
    if status:
        items = [it for it in items if it.get("status") == status]
    return items


def get_ip(ip_id: str) -> dict | None:
    for ip in _load():
        if ip["id"] == ip_id:
            return ip
    return None
