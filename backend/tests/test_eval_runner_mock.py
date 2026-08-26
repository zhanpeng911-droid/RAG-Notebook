"""Tests for eval runner mock mode."""

import sys
from pathlib import Path


# Add backend to path
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from evals.runners.run_eval import load_cases, dry_run, mock_run  # noqa: E402 -- 测试需先注入 backend 到 sys.path


class TestRunnerDryRun:
    """Tests for dry-run mode behavior."""

    def test_dry_run_behavior_evaluated_is_zero(self):
        """dry-run 模式 behavior_evaluated 始终为 0"""
        cases = load_cases("rag_retrieval")
        report = dry_run(cases)
        assert report["behavior_evaluated"] == 0
        assert report["behavior_passed"] == 0
        assert report["behavior_failed"] == 0

    def test_dry_run_schema_valid(self):
        """dry-run 模式能正确校验 schema"""
        cases = load_cases("rag_retrieval")
        report = dry_run(cases)
        assert report["schema_valid"] > 0
        assert report["schema_invalid"] == 0

    def test_dry_run_mode_field(self):
        """dry-run 模式 mode 字段为 dry-run"""
        cases = load_cases("rag_retrieval")
        report = dry_run(cases)
        assert report["mode"] == "dry-run"


class TestRunnerMockMode:
    """Tests for mock mode behavior."""

    def test_mock_behavior_evaluated_greater_than_zero(self):
        """mock 模式 behavior_evaluated > 0"""
        cases = load_cases("rag_retrieval")
        report = mock_run(cases)
        assert report["behavior_evaluated"] > 0

    def test_mock_mode_field(self):
        """mock 模式 mode 字段为 mock"""
        cases = load_cases("rag_retrieval")
        report = mock_run(cases)
        assert report["mode"] == "mock"

    def test_mock_has_passed_and_failed(self):
        """mock 模式能产出 behavior_passed 和 behavior_failed"""
        cases = load_cases("rag_retrieval")
        report = mock_run(cases)
        assert report["behavior_passed"] >= 0
        assert report["behavior_failed"] >= 0
        # Total evaluated should equal passed + failed
        assert report["behavior_evaluated"] == report["behavior_passed"] + report["behavior_failed"]

    def test_mock_no_llm_calls(self):
        """mock 模式不调用真实 LLM"""
        cases = load_cases("rag_retrieval")
        report = mock_run(cases)
        # All cases should have requires_llm=false
        assert report["skipped_requires_llm"] == 0

    def test_mock_results_contain_details(self):
        """mock 模式结果包含 grader details"""
        cases = load_cases("rag_retrieval")
        report = mock_run(cases)
        evaluated_results = [r for r in report["results"] if r["status"] in ("passed", "failed")]
        assert len(evaluated_results) > 0
        for r in evaluated_results:
            assert "details" in r
            assert "expected_keywords_found" in r["details"]

    def test_mock_grader_errors_zero(self):
        """mock 模式 grader_errors 为 0"""
        cases = load_cases("rag_retrieval")
        report = mock_run(cases)
        assert report["grader_errors"] == 0


class TestCaseLoading:
    """Tests for case loading."""

    def test_load_rag_retrieval_cases(self):
        """能加载 rag_retrieval cases"""
        cases = load_cases("rag_retrieval")
        assert len(cases) >= 10  # Should have ~12 cases after expansion

    def test_load_all_categories(self):
        """能加载所有 category"""
        cases = load_cases()
        assert len(cases) >= 25  # rag_retrieval + agent_tool + safety

    def test_cases_have_required_fields(self):
        """所有 case 有必填字段"""
        cases = load_cases()
        for case in cases:
            assert "id" in case, f"case missing id: {case}"
            assert "category" in case, f"case missing category: {case}"
            assert "user_input" in case, f"case missing user_input: {case}"
            assert "success_criteria" in case, f"case missing success_criteria: {case}"
            assert "grader_type" in case, f"case missing grader_type: {case}"

    def test_rag_cases_have_mock_retrieved_docs(self):
        """rag_retrieval cases 有 mock_retrieved_docs"""
        cases = load_cases("rag_retrieval")
        for case in cases:
            if case.get("grader_type") == "retrieval_keyword":
                mock_docs = case.get("context_setup", {}).get("mock_retrieved_docs")
                assert mock_docs is not None, f"rag case {case['id']} missing mock_retrieved_docs"
