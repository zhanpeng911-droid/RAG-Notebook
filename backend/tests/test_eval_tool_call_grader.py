"""Tests for eval tool_call_grader."""

import sys
from pathlib import Path

import pytest

# Add backend to path
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from evals.graders.tool_call_grader import grade


class TestToolCallGrader:
    """Tests for the tool_call grader."""

    def test_all_expected_tools_found(self):
        """expected_tools 全部出现 => pass"""
        case = {
            "id": "test-001",
            "expected_tools": ["search_notes_tool"],
            "forbidden_tools": [],
        }
        result = {
            "tool_calls": [
                {"name": "search_notes_tool", "arguments": {"query": "Docker"}}
            ]
        }
        grade_result = grade(case, result)
        assert grade_result["passed"] is True
        assert grade_result["details"]["expected_tools_found"] == ["search_notes_tool"]
        assert grade_result["details"]["expected_tools_missing"] == []

    def test_expected_tools_missing(self):
        """expected_tools 缺失 => fail"""
        case = {
            "id": "test-002",
            "expected_tools": ["search_notes_tool", "get_note_stats_tool"],
            "forbidden_tools": [],
        }
        result = {
            "tool_calls": [
                {"name": "search_notes_tool", "arguments": {"query": "Docker"}}
            ]
        }
        grade_result = grade(case, result)
        assert grade_result["passed"] is False
        assert "get_note_stats_tool" in grade_result["details"]["expected_tools_missing"]

    def test_forbidden_tools_found(self):
        """forbidden_tools 出现 => fail"""
        case = {
            "id": "test-003",
            "expected_tools": ["search_notes_tool"],
            "forbidden_tools": ["get_note_stats_tool"],
        }
        result = {
            "tool_calls": [
                {"name": "search_notes_tool", "arguments": {"query": "Docker"}},
                {"name": "get_note_stats_tool", "arguments": {}},
            ]
        }
        grade_result = grade(case, result)
        assert grade_result["passed"] is False
        assert "get_note_stats_tool" in grade_result["details"]["forbidden_tools_found"]

    def test_forbidden_tools_not_found(self):
        """forbidden_tools 未出现 => pass"""
        case = {
            "id": "test-004",
            "expected_tools": ["search_notes_tool"],
            "forbidden_tools": ["get_note_stats_tool"],
        }
        result = {
            "tool_calls": [
                {"name": "search_notes_tool", "arguments": {"query": "Docker"}}
            ]
        }
        grade_result = grade(case, result)
        assert grade_result["passed"] is True
        assert grade_result["details"]["forbidden_tools_found"] == []

    def test_no_tool_needed_empty_calls(self):
        """expected_tools 为空且 mock_tool_calls 为空 => pass"""
        case = {
            "id": "test-005",
            "expected_tools": [],
            "forbidden_tools": ["get_note_stats_tool"],
        }
        result = {"tool_calls": []}
        grade_result = grade(case, result)
        assert grade_result["passed"] is True

    def test_no_tool_needed_with_calls_fails(self):
        """expected_tools 为空但 mock_tool_calls 非空 => fail"""
        case = {
            "id": "test-006",
            "expected_tools": [],
            "forbidden_tools": [],
        }
        result = {
            "tool_calls": [
                {"name": "search_notes_tool", "arguments": {"query": "Docker"}}
            ]
        }
        grade_result = grade(case, result)
        assert grade_result["passed"] is False
        assert len(grade_result["details"]["no_tool_violations"]) > 0

    def test_sequence_correct(self):
        """expected_tool_sequence 顺序正确 => pass"""
        case = {
            "id": "test-007",
            "expected_tools": ["search_notes_tool", "get_related_notes_tool"],
            "expected_tool_sequence": ["search_notes_tool", "get_related_notes_tool"],
        }
        result = {
            "tool_calls": [
                {"name": "search_notes_tool", "arguments": {"query": "Docker"}},
                {"name": "get_related_notes_tool", "arguments": {"note_id": "note-123"}},
            ]
        }
        grade_result = grade(case, result)
        assert grade_result["passed"] is True
        assert grade_result["details"]["sequence_passed"] is True

    def test_sequence_wrong_order(self):
        """expected_tool_sequence 顺序错误 => fail"""
        case = {
            "id": "test-008",
            "expected_tools": ["get_related_notes_tool", "search_notes_tool"],
            "expected_tool_sequence": ["search_notes_tool", "get_related_notes_tool"],
        }
        result = {
            "tool_calls": [
                {"name": "get_related_notes_tool", "arguments": {"note_id": "note-123"}},
                {"name": "search_notes_tool", "arguments": {"query": "Docker"}},
            ]
        }
        grade_result = grade(case, result)
        assert grade_result["passed"] is False
        assert grade_result["details"]["sequence_passed"] is False

    def test_unknown_tool_recorded(self):
        """unknown tool name 被记录但不影响 pass/fail"""
        case = {
            "id": "test-009",
            "expected_tools": ["search_notes_tool"],
            "forbidden_tools": [],
        }
        result = {
            "tool_calls": [
                {"name": "search_notes_tool", "arguments": {"query": "Docker"}},
                {"name": "unknown_tool_xyz", "arguments": {}},
            ]
        }
        grade_result = grade(case, result)
        assert grade_result["passed"] is True
        assert "unknown_tool_xyz" in grade_result["details"]["unknown_tools"]

    def test_argument_keywords_pass(self):
        """expected_tool_argument_keywords 命中 => pass"""
        case = {
            "id": "test-010",
            "expected_tools": ["create_note_tool"],
            "expected_tool_argument_keywords": {
                "create_note_tool": ["Redis", "缓存"]
            },
        }
        result = {
            "tool_calls": [
                {"name": "create_note_tool", "arguments": {"title": "Redis 缓存策略", "content": ""}}
            ]
        }
        grade_result = grade(case, result)
        assert grade_result["passed"] is True
        assert grade_result["details"]["argument_keyword_missing"] == []

    def test_argument_keywords_missing(self):
        """expected_tool_argument_keywords 缺失 => fail"""
        case = {
            "id": "test-011",
            "expected_tools": ["create_note_tool"],
            "expected_tool_argument_keywords": {
                "create_note_tool": ["Redis", "缓存"]
            },
        }
        result = {
            "tool_calls": [
                {"name": "create_note_tool", "arguments": {"title": "Git 分支管理", "content": ""}}
            ]
        }
        grade_result = grade(case, result)
        assert grade_result["passed"] is False
        assert len(grade_result["details"]["argument_keyword_missing"]) > 0

    def test_backward_compat_tools_called(self):
        """向后兼容：支持 tools_called 简单列表"""
        case = {
            "id": "test-012",
            "expected_tools": ["search_notes_tool"],
            "forbidden_tools": [],
        }
        result = {"tools_called": ["search_notes_tool"]}
        grade_result = grade(case, result)
        assert grade_result["passed"] is True
        assert grade_result["details"]["called_tools"] == ["search_notes_tool"]

    def test_duplicate_tool_calls_allowed(self):
        """允许重复工具调用"""
        case = {
            "id": "test-013",
            "expected_tools": ["search_notes_tool"],
            "forbidden_tools": [],
        }
        result = {
            "tool_calls": [
                {"name": "search_notes_tool", "arguments": {"query": "Docker"}},
                {"name": "search_notes_tool", "arguments": {"query": "Kubernetes"}},
            ]
        }
        grade_result = grade(case, result)
        assert grade_result["passed"] is True
        assert grade_result["details"]["called_tools"] == ["search_notes_tool", "search_notes_tool"]
