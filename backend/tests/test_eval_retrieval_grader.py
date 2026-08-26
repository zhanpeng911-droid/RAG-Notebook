"""Tests for eval retrieval_grader."""

import sys
from pathlib import Path


# Add backend to path
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from evals.graders.retrieval_grader import grade  # noqa: E402 -- 测试需先注入 backend 到 sys.path


class TestRetrievalGrader:
    """Tests for the retrieval_keyword grader."""

    def test_all_expected_keywords_found(self):
        """expected_keywords 全部命中 => pass"""
        case = {
            "id": "test-001",
            "expected_keywords": ["Docker", "容器"],
            "forbidden_keywords": [],
        }
        result = {
            "retrieved_docs": [
                {"id": "doc-1", "content": "Docker 容器化技术：使用 Dockerfile 构建镜像。"}
            ]
        }
        grade_result = grade(case, result)
        assert grade_result["passed"] is True
        assert grade_result["details"]["expected_keywords_found"] == ["Docker", "容器"]
        assert grade_result["details"]["expected_keywords_missing"] == []

    def test_expected_keywords_missing(self):
        """expected_keywords 缺失 => fail"""
        case = {
            "id": "test-002",
            "expected_keywords": ["Docker", "Kubernetes"],
            "forbidden_keywords": [],
        }
        result = {
            "retrieved_docs": [
                {"id": "doc-1", "content": "Docker 容器化技术。"}
            ]
        }
        grade_result = grade(case, result)
        assert grade_result["passed"] is False
        assert "Kubernetes" in grade_result["details"]["expected_keywords_missing"]

    def test_forbidden_keywords_found(self):
        """forbidden_keywords 出现 => fail"""
        case = {
            "id": "test-003",
            "expected_keywords": ["配置"],
            "forbidden_keywords": ["API_KEY", "SECRET"],
        }
        result = {
            "retrieved_docs": [
                {"id": "doc-1", "content": "系统配置管理：API_KEY=xxx"}
            ]
        }
        grade_result = grade(case, result)
        assert grade_result["passed"] is False
        assert "API_KEY" in grade_result["details"]["forbidden_keywords_found"]

    def test_forbidden_keywords_not_found(self):
        """forbidden_keywords 未出现 => pass"""
        case = {
            "id": "test-004",
            "expected_keywords": ["配置"],
            "forbidden_keywords": ["API_KEY", "SECRET"],
        }
        result = {
            "retrieved_docs": [
                {"id": "doc-1", "content": "系统配置管理：使用环境变量管理应用设置。"}
            ]
        }
        grade_result = grade(case, result)
        assert grade_result["passed"] is True
        assert grade_result["details"]["forbidden_keywords_found"] == []

    def test_no_answer_empty_docs(self):
        """expected_no_answer=true 且 retrieved docs 为空 => pass"""
        case = {
            "id": "test-005",
            "expected_keywords": [],
            "expected_no_answer": True,
        }
        result = {"retrieved_docs": []}
        grade_result = grade(case, result)
        assert grade_result["passed"] is True
        assert grade_result["details"]["retrieved_count"] == 0
        assert grade_result["details"]["expected_no_answer"] is True

    def test_no_answer_with_non_relevant_docs(self):
        """expected_no_answer=true 但 retrieved docs 非空且无 expected_keywords 命中 => pass"""
        case = {
            "id": "test-006",
            "expected_keywords": ["量子纠缠"],
            "expected_no_answer": True,
        }
        result = {
            "retrieved_docs": [
                {"id": "doc-1", "content": "今天天气不错。"}
            ]
        }
        grade_result = grade(case, result)
        assert grade_result["passed"] is True
        assert grade_result["details"]["expected_keywords_missing"] == ["量子纠缠"]

    def test_no_answer_with_relevant_docs_fails(self):
        """expected_no_answer=true 但 retrieved docs 包含 expected_keywords => fail"""
        case = {
            "id": "test-007",
            "expected_keywords": ["量子"],
            "expected_no_answer": True,
        }
        result = {
            "retrieved_docs": [
                {"id": "doc-1", "content": "量子力学是物理学的分支。"}
            ]
        }
        grade_result = grade(case, result)
        assert grade_result["passed"] is False
        assert len(grade_result["details"]["no_answer_violations"]) > 0

    def test_not_no_answer_with_empty_docs_fails(self):
        """expected_no_answer=false 但 retrieved docs 为空 => fail"""
        case = {
            "id": "test-008",
            "expected_keywords": ["Docker"],
            "expected_no_answer": False,
        }
        result = {"retrieved_docs": []}
        grade_result = grade(case, result)
        assert grade_result["passed"] is False
        assert len(grade_result["details"]["no_answer_violations"]) > 0

    def test_case_insensitive_english_keywords(self):
        """英文关键词 case-insensitive"""
        case = {
            "id": "test-009",
            "expected_keywords": ["docker", "JWT"],
            "forbidden_keywords": [],
        }
        result = {
            "retrieved_docs": [
                {"id": "doc-1", "content": "Docker containers and jwt token authentication."}
            ]
        }
        grade_result = grade(case, result)
        assert grade_result["passed"] is True
        assert len(grade_result["details"]["expected_keywords_found"]) == 2

    def test_chinese_substring_match(self):
        """中文关键词 substring match"""
        case = {
            "id": "test-010",
            "expected_keywords": ["机器学习"],
            "forbidden_keywords": [],
        }
        result = {
            "retrieved_docs": [
                {"id": "doc-1", "content": "深度学习是机器学习的子集。"}
            ]
        }
        grade_result = grade(case, result)
        assert grade_result["passed"] is True

    def test_uses_mock_retrieved_docs_from_case(self):
        """从 case context_setup.mock_retrieved_docs 读取数据"""
        case = {
            "id": "test-011",
            "context_setup": {
                "mock_retrieved_docs": [
                    {"id": "doc-1", "content": "Redis 是一个内存数据库。"}
                ]
            },
            "expected_keywords": ["Redis"],
            "forbidden_keywords": [],
        }
        result = {}
        grade_result = grade(case, result)
        assert grade_result["passed"] is True
        assert grade_result["details"]["retrieved_count"] == 1

    def test_multiple_docs_combined(self):
        """多个文档内容合并检查"""
        case = {
            "id": "test-012",
            "expected_keywords": ["JWT", "Redis"],
            "forbidden_keywords": [],
        }
        result = {
            "retrieved_docs": [
                {"id": "doc-1", "content": "JWT token authentication."},
                {"id": "doc-2", "content": "Redis blacklist storage."}
            ]
        }
        grade_result = grade(case, result)
        assert grade_result["passed"] is True
        assert len(grade_result["details"]["expected_keywords_found"]) == 2
