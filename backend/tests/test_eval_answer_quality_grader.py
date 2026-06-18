"""Tests for eval answer_quality_grader."""

import sys
from pathlib import Path

import pytest

# Add backend to path
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from evals.graders.answer_quality_grader import grade


class TestAnswerQualityGrader:
    """Tests for the answer_quality grader."""

    def test_expected_keywords_pass(self):
        """expected_keywords 命中 => pass"""
        case = {
            "id": "test-001",
            "expected_keywords": ["装饰器", "高阶函数"],
            "forbidden_keywords": [],
        }
        result = {"answer": "Python 装饰器是一种高阶函数，用于为函数添加新功能。"}
        grade_result = grade(case, result)
        assert grade_result["passed"] is True
        assert "装饰器" in grade_result["details"]["expected_keywords_found"]
        assert "高阶函数" in grade_result["details"]["expected_keywords_found"]

    def test_expected_keywords_missing(self):
        """expected_keywords 缺失 => fail"""
        case = {
            "id": "test-002",
            "expected_keywords": ["装饰器", "高阶函数", "闭包"],
            "forbidden_keywords": [],
        }
        result = {"answer": "Python 装饰器用于为函数添加新功能。"}
        grade_result = grade(case, result)
        assert grade_result["passed"] is False
        assert "闭包" in grade_result["details"]["expected_keywords_missing"]

    def test_forbidden_keywords_found(self):
        """forbidden_keywords 出现 => fail"""
        case = {
            "id": "test-003",
            "expected_keywords": [],
            "forbidden_keywords": ["类继承", "多线程"],
        }
        result = {"answer": "装饰器使用类继承实现。"}
        grade_result = grade(case, result)
        assert grade_result["passed"] is False
        assert "类继承" in grade_result["details"]["forbidden_keywords_found"]

    def test_forbidden_keywords_not_found(self):
        """forbidden_keywords 未出现 => pass"""
        case = {
            "id": "test-004",
            "expected_keywords": [],
            "forbidden_keywords": ["类继承", "多线程"],
        }
        result = {"answer": "装饰器是一种设计模式。"}
        grade_result = grade(case, result)
        assert grade_result["passed"] is True
        assert grade_result["details"]["forbidden_keywords_found"] == []

    def test_refusal_expected_and_detected(self):
        """expected_refusal=true 且回答包含拒答语义 => pass"""
        case = {
            "id": "test-005",
            "expected_keywords": [],
            "forbidden_keywords": [],
            "expected_refusal": True,
        }
        result = {"answer": "根据提供的资料，无法确认量子纠缠的相关内容。建议补充相关资料。"}
        grade_result = grade(case, result)
        assert grade_result["passed"] is True
        assert grade_result["details"]["refusal_detected"] is True

    def test_refusal_expected_but_substantive(self):
        """expected_refusal=true 但回答编造（无 forbidden_keywords 限制时） => fail"""
        case = {
            "id": "test-006",
            "expected_keywords": [],
            "forbidden_keywords": [],
            "expected_refusal": True,
        }
        result = {"answer": "量子纠缠是一种物理现象，Bell 不等式用于验证量子力学的完备性。纠缠态描述了粒子之间的关联。这是确定的物理事实。"}
        grade_result = grade(case, result)
        assert grade_result["passed"] is False
        assert len(grade_result["details"]["refusal_violations"]) > 0

    def test_empty_answer_fails(self):
        """空回答 => fail"""
        case = {
            "id": "test-007",
            "expected_keywords": ["装饰器"],
            "forbidden_keywords": [],
        }
        result = {"answer": ""}
        grade_result = grade(case, result)
        assert grade_result["passed"] is False
        assert len(grade_result["details"]["empty_violations"]) > 0

    def test_model_grader_prompt_built(self):
        """model grader prompt 能生成"""
        case = {
            "id": "test-008",
            "user_input": "总结装饰器",
            "context_setup": {"context": "装饰器是高阶函数"},
            "expected_keywords": [],
        }
        result = {"answer": "装饰器是高阶函数。"}
        grade_result = grade(case, result)
        assert grade_result["details"]["model_grader_prompt_built"] is True
        assert "model_grader_prompt" in grade_result
        assert len(grade_result["model_grader_prompt"]) > 0

    def test_answer_length_recorded(self):
        """answer_length 被记录"""
        case = {
            "id": "test-009",
            "expected_keywords": [],
        }
        result = {"answer": "这是一个测试回答。"}
        grade_result = grade(case, result)
        assert grade_result["details"]["answer_length"] == len("这是一个测试回答。")
