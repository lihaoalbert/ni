"""Evals pytest fixtures — Day 7"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.api.chat import get_llm_provider
from app.evals.schemas import EvalCase
from app.main import app
from app.memory.store import reset_conversation_store, reset_memory_store

from tests.evals.mock_provider import MockEvalProvider


CASES_DIR = Path(__file__).parent / "cases"


def _load_all_cases() -> list[EvalCase]:
    """从 cases/ 目录加载所有 YAML,展开成 flat list[EvalCase]"""
    cases: list[EvalCase] = []
    for yaml_file in sorted(CASES_DIR.glob("*.yaml")):
        with yaml_file.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        # 文件格式:cases: [EvalCase, EvalCase, ...]
        for case_dict in data.get("cases", []):
            cases.append(EvalCase(**case_dict))
    return cases


@pytest.fixture(scope="session")
def all_cases() -> list[EvalCase]:
    return _load_all_cases()


@pytest.fixture
def cases_by_tag(all_cases: list[EvalCase]):
    """按 tag 筛选 — 方便 pytest -k 'persona' 单独跑"""
    def _filter(tag: str) -> list[EvalCase]:
        return [c for c in all_cases if tag in c.tags]
    return _filter


@pytest.fixture
def eval_client() -> TestClient:
    """Eval 用的 TestClient — 注入 mock LLM,每个测试后清状态"""
    reset_memory_store()
    reset_conversation_store()

    mock = MockEvalProvider()
    app.dependency_overrides[get_llm_provider] = lambda: mock

    client = TestClient(app)
    yield client

    app.dependency_overrides.clear()
    reset_memory_store()
    reset_conversation_store()
