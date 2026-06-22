"""Evals package — Day 7

包内结构：
- schemas.py  : EvalCase / TurnExpect / EvalResult 模型
- runner.py   : 执行多轮对话 + 跑断言
- conftest.py : pytest fixtures（TestClient + 状态重置）
- cases/*.yaml: 评测用例
- test_*.py   : pytest 入口
"""
