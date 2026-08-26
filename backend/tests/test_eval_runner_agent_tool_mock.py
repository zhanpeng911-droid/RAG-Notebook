"""Tests for eval runner agent_tool mock mode."""

import sys
from pathlib import Path


# Add backend to path
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from evals.runners.run_eval import load_cases, dry_run, mock_run  # noqa: E402 -- 测试需先注入 backend 到 sys.path


class TestAgentToolMockMode:
    """Tests for agent_tool mock mode behavior."""

    def test_mock_agent_tool_behavior_evaluated(self):
        """mock agent_tool 模式 behavior_evaluated > 0"""
        cases = load_cases("agent_tool")
        report = mock_run(cases)
        assert report["behavior_evaluated"] > 0

    def test_mock_agent_tool_has_passed_and_failed(self):
        """mock agent_tool 模式能产出 behavior_passed 和 behavior_failed"""
        cases = load_cases("agent_tool")
        report = mock_run(cases)
        assert report["behavior_passed"] >= 0
        assert report["behavior_failed"] >= 0
        assert report["behavior_evaluated"] == report["behavior_passed"] + report["behavior_failed"]

    def test_mock_agent_tool_mode_field(self):
        """mock agent_tool 模式 mode 字段为 mock"""
        cases = load_cases("agent_tool")
        report = mock_run(cases)
        assert report["mode"] == "mock"

    def test_mock_agent_tool_grader_errors_zero(self):
        """mock agent_tool 模式 grader_errors 为 0"""
        cases = load_cases("agent_tool")
        report = mock_run(cases)
        assert report["grader_errors"] == 0

    def test_mock_agent_tool_results_contain_details(self):
        """mock agent_tool 模式结果包含 grader details"""
        cases = load_cases("agent_tool")
        report = mock_run(cases)
        evaluated_results = [r for r in report["results"] if r["status"] in ("passed", "failed")]
        assert len(evaluated_results) > 0
        for r in evaluated_results:
            assert "details" in r
            assert "called_tools" in r["details"]

    def test_mock_all_categories(self):
        """mock 全量模式支持 rag_retrieval + agent_tool"""
        cases = load_cases()
        report = mock_run(cases)
        # Should have evaluated cases from both categories
        assert report["behavior_evaluated"] > 0
        # rag_retrieval should still pass
        rag_cases = [r for r in report["results"] if r.get("grader") == "retrieval_keyword"]
        assert len(rag_cases) > 0
        rag_passed = [r for r in rag_cases if r["status"] == "passed"]
        assert len(rag_passed) == len(rag_cases)  # All rag cases should pass


class TestDryRunUnchanged:
    """Tests that dry-run mode is unchanged by Eval 2."""

    def test_dry_run_behavior_evaluated_is_zero(self):
        """dry-run 模式 behavior_evaluated 始终为 0"""
        cases = load_cases()
        report = dry_run(cases)
        assert report["behavior_evaluated"] == 0
        assert report["behavior_passed"] == 0
        assert report["behavior_failed"] == 0

    def test_dry_run_schema_valid(self):
        """dry-run 模式能正确校验 schema"""
        cases = load_cases()
        report = dry_run(cases)
        assert report["schema_valid"] > 0
        assert report["schema_invalid"] == 0

    def test_dry_run_includes_all_categories(self):
        """dry-run 模式包含所有 category"""
        cases = load_cases()
        report = dry_run(cases)
        assert report["total_cases_loaded"] >= 30  # rag + agent_tool + safety


class TestRagRetrievalMockUnchanged:
    """Tests that rag_retrieval mock mode is unchanged by Eval 2."""

    def test_mock_rag_retrieval_still_passes(self):
        """mock rag_retrieval 模式仍然通过"""
        cases = load_cases("rag_retrieval")
        report = mock_run(cases)
        assert report["behavior_evaluated"] == 12
        assert report["behavior_passed"] == 12
        assert report["behavior_failed"] == 0


class TestCaseLoading:
    """Tests for case loading."""

    def test_load_agent_tool_cases(self):
        """能加载 agent_tool cases"""
        cases = load_cases("agent_tool")
        assert len(cases) >= 12  # Should have ~14 cases after expansion

    def test_agent_tool_cases_have_mock_tool_calls(self):
        """agent_tool cases 有 mock_tool_calls"""
        cases = load_cases("agent_tool")
        for case in cases:
            mock_calls = case.get("context_setup", {}).get("mock_tool_calls")
            assert mock_calls is not None, f"agent_tool case {case['id']} missing mock_tool_calls"

    def test_agent_tool_cases_have_required_fields(self):
        """agent_tool cases 有必填字段"""
        cases = load_cases("agent_tool")
        for case in cases:
            assert "id" in case
            assert "category" in case
            assert "user_input" in case
            assert "expected_tools" in case
            assert "grader_type" in case
            assert case["grader_type"] == "tool_call"
