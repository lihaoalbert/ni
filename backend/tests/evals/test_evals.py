"""Evals pytest entry — Day 7

跑法：
    pytest tests/evals/                  # 全跑
    pytest tests/evals/ -k persona       # 只跑 persona tag
    pytest tests/evals/ -k memory        # 只跑 memory tag
    EVAL_LIVE=1 pytest tests/evals/      # 用真模型（要 API key）

每个 YAML case → 1 个 pytest test，失败时输出具体的 turn + assertion。
"""
from __future__ import annotations

import pytest

from app.evals.runner import live_mode_warning, run_case

from .conftest import _load_all_cases


# ===== 全量跑 =====


def pytest_generate_tests(metafunc):
    """动态 parametrize — 每个 YAML case 生成一个 pytest test id"""
    if "eval_case" in metafunc.fixturenames:
        cases = _load_all_cases()
        metafunc.parametrize(
            "eval_case",
            cases,
            ids=[c.name for c in cases],
        )


def test_eval_case(eval_case, eval_client, request):
    """执行一个 eval case — 失败时输出人类可读 report"""
    # 顶头打印一次运行模式（方便查 CI 日志）
    if request.config.option.verbose > 0:
        print(f"\n[eval] mode: {live_mode_warning()}")

    result = run_case(eval_case, eval_client)

    if not result.passed:
        # 构造易读的失败信息
        report_lines = ["\n=== EVAL CASE FAILED ==="]
        report_lines.extend(result.summary_lines())
        report_lines.append(f"  total_latency: {result.total_latency_ms:.0f}ms")
        pytest.fail("\n".join(report_lines))


# ===== 汇总报告 — 非 fail 模式,只看跑通多少 =====


def test_eval_summary(all_cases, eval_client, request, capsys):
    """跑完所有 case,打印汇总 — 总是 pass,仅做汇报"""
    results = []
    for case in all_cases:
        r = run_case(case, eval_client)
        results.append(r)

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print(f"\n[eval summary] {passed}/{total} cases passed")
    print(f"[eval] mode: {live_mode_warning()}")
    for r in results:
        status = "✓" if r.passed else "✗"
        print(f"  {status} {r.case_name} ({r.total_latency_ms:.0f}ms)")

    # 这个 test 永远 pass — 它只是报告
