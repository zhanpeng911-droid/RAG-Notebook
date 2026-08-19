"""Tests for eval runner llm-smoke mode."""

import os
import sys
from pathlib import Path

import pytest

# Add backend to path
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from evals.runners.run_eval import load_cases, dry_run, mock_run, llm_smoke_run


class TestLlmSmokeDisabled:
    """Tests for llm-smoke mode when LLM is disabled."""

    def test_llm_smoke_disabled_skips_all(self):
        """未设置 EVAL_ENABLE_LLM 时，llm-smoke 不调用 LLM，case skipped"""
        # Ensure EVAL_ENABLE_LLM is not set
        os.environ.pop("EVAL_ENABLE_LLM", None)
        cases = load_cases("answer_quality")
        report = llm_smoke_run(cases, limit=4)
        assert report["llm_eval_enabled"] is False
        assert report["llm_cases_discovered"] > 0
        assert report["llm_cases_executed"] == 0
        assert report["llm_cases_skipped"] == report["llm_cases_discovered"]

    def test_llm_smoke_disabled_false_skips(self):
        """设置 EVAL_ENABLE_LLM=false 时，llm-smoke 不调用 LLM"""
        os.environ["EVAL_ENABLE_LLM"] = "false"
        cases = load_cases("answer_quality")
        report = llm_smoke_run(cases, limit=4)
        assert report["llm_eval_enabled"] is False
        assert report["llm_cases_executed"] == 0
        os.environ.pop("EVAL_ENABLE_LLM", None)

    def test_llm_smoke_results_contain_skip_reason(self):
        """skip 结果包含原因"""
        os.environ.pop("EVAL_ENABLE_LLM", None)
        cases = load_cases("answer_quality")
        report = llm_smoke_run(cases, limit=4)
        skipped_results = [r for r in report["results"] if r["status"] == "skipped"]
        assert len(skipped_results) > 0
        for r in skipped_results:
            assert "reason" in r


class TestDryRunUnchanged:
    """Tests that dry-run mode is unchanged by Eval 3."""

    def test_dry_run_behavior_evaluated_is_zero(self):
        """dry-run 模式 behavior_evaluated 始终为 0"""
        cases = load_cases()
        report = dry_run(cases)
        assert report["behavior_evaluated"] == 0

    def test_dry_run_schema_valid(self):
        """dry-run 模式能正确校验 schema"""
        cases = load_cases()
        report = dry_run(cases)
        assert report["schema_valid"] > 0
        assert report["schema_invalid"] == 0

    def test_dry_run_includes_answer_quality(self):
        """dry-run 模式包含 answer_quality category"""
        cases = load_cases()
        report = dry_run(cases)
        assert report["total_cases_loaded"] >= 35  # rag + agent_tool + safety + answer_quality


class TestMockUnchanged:
    """Tests that mock mode is unchanged by Eval 3."""

    def test_mock_rag_retrieval_still_passes(self):
        """mock rag_retrieval 模式仍然通过"""
        cases = load_cases("rag_retrieval")
        report = mock_run(cases)
        assert report["behavior_evaluated"] == 12
        assert report["behavior_passed"] == 12

    def test_mock_agent_tool_still_passes(self):
        """mock agent_tool 模式仍然通过"""
        cases = load_cases("agent_tool")
        report = mock_run(cases)
        assert report["behavior_evaluated"] == 13  # tool-007 已随 rag_summary_tools 删除
        assert report["behavior_passed"] == 13


class TestCaseLoading:
    """Tests for case loading."""

    def test_load_answer_quality_cases(self):
        """能加载 answer_quality cases"""
        cases = load_cases("answer_quality")
        assert len(cases) >= 3  # Should have 4 cases

    def test_answer_quality_cases_require_llm(self):
        """answer_quality cases 有 requires_llm=true"""
        cases = load_cases("answer_quality")
        for case in cases:
            assert case.get("requires_llm", False) is True, f"case {case['id']} should have requires_llm=true"

    def test_answer_quality_cases_have_context(self):
        """answer_quality cases 有 context_setup.context"""
        cases = load_cases("answer_quality")
        for case in cases:
            assert "context_setup" in case, f"case {case['id']} missing context_setup"

    def test_answer_quality_grader_type(self):
        """answer_quality cases grader_type 为 answer_quality"""
        cases = load_cases("answer_quality")
        for case in cases:
            assert case.get("grader_type") == "answer_quality"
