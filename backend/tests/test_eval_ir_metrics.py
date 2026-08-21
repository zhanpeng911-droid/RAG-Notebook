"""IR 评测指标单测 —— 纯函数，CI 可跑，不依赖 embedding/ChromaDB。"""
import json
from pathlib import Path

import pytest

from evals.graders.ir_metrics import (
    aggregate_by_topic,
    aggregate_ir,
    extract_source,
    grade_case_ir,
    normalize_source,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    refusal_accuracy,
    sources_from_docs,
)
from evals.graders.retrieval_grader import grade as retrieval_grade
from evals.graders.schema_validator import validate_case

BACKEND_DIR = Path(__file__).resolve().parents[1]
IR_CASES_FILE = BACKEND_DIR / "evals" / "cases" / "ir_eval_cases.jsonl"


# ==================== normalize / extract ====================


def test_normalize_source_handles_paths():
    assert normalize_source("C:\\data\\docs\\mysql_01_index.txt") == "mysql_01_index.txt"
    assert normalize_source("/home/user/data/mysql_01_index.txt") == "mysql_01_index.txt"
    assert normalize_source("mysql_01_index.txt") == "mysql_01_index.txt"
    assert normalize_source(None) == ""
    assert normalize_source("") == ""


def test_extract_source_prefers_original_filename():
    doc = {"content": "x", "metadata": {"original_filename": "a.txt", "source": "/tmp/b.txt"}}
    assert extract_source(doc) == "a.txt"

    doc_only_source = {"content": "x", "metadata": {"source": "/tmp/b.txt"}}
    assert extract_source(doc_only_source) == "b.txt"

    assert extract_source({"content": "x", "metadata": {}}) == ""


# ==================== 单指标 ====================


def test_recall_at_k_hit_and_miss():
    sources = ["mysql_01_index.txt", "redis_01_persistence.txt", "k8s_02_deployment.txt"]
    assert recall_at_k(sources, ["redis_01_persistence.txt"], k=3) == 1.0
    assert recall_at_k(sources, ["redis_01_persistence.txt"], k=1) == 0.0
    assert recall_at_k([], ["redis_01_persistence.txt"], k=3) == 0.0
    assert recall_at_k(sources, [], k=3) == 0.0


def test_recall_at_k_multi_source_partial_credit():
    sources = ["mysql_08_binlog.txt", "redis_01_persistence.txt", "k8s_02_deployment.txt"]
    # 两个标准出处只命中一个 -> 0.5（跨文档 case 部分分）
    assert recall_at_k(sources, ["mysql_08_binlog.txt", "k8s_10_probe.txt"], k=3) == 0.5


def test_precision_at_k():
    sources = ["a.txt", "a.txt", "b.txt"]
    assert precision_at_k(sources, ["a.txt"], k=3) == pytest.approx(2 / 3)
    assert precision_at_k(sources, ["a.txt"], k=2) == 1.0
    assert precision_at_k(sources, ["c.txt"], k=3) == 0.0
    assert precision_at_k([], ["a.txt"], k=3) == 0.0


def test_reciprocal_rank():
    sources = ["b.txt", "a.txt", "c.txt"]
    assert reciprocal_rank(sources, ["a.txt"]) == 0.5
    assert reciprocal_rank(sources, ["b.txt"]) == 1.0
    assert reciprocal_rank(sources, ["z.txt"]) == 0.0


# ==================== grade_case_ir / aggregate ====================


def test_grade_case_ir_full_flow():
    docs = [
        {"content": "1", "metadata": {"original_filename": "redis_01_persistence.txt"}},
        {"content": "2", "metadata": {"original_filename": "mysql_01_index.txt"}},
        {"content": "3", "metadata": {"original_filename": "k8s_02_deployment.txt"}},
    ]
    result = grade_case_ir(docs, ["mysql_01_index.txt"], k=3)

    assert result["recall_at_k"] == 1.0
    assert result["precision_at_k"] == round(1 / 3, 4)
    assert result["reciprocal_rank"] == 0.5
    assert result["hit_rank"] == 2
    assert result["retrieved_count"] == 3
    assert result["k"] == 3


def test_aggregate_ir_averages():
    r1 = {"recall_at_k": 1.0, "precision_at_k": 1.0, "reciprocal_rank": 1.0}
    r2 = {"recall_at_k": 0.0, "precision_at_k": 0.0, "reciprocal_rank": 0.0}
    agg = aggregate_ir([r1, r2])
    assert agg == {"recall_at_k": 0.5, "precision_at_k": 0.5, "mrr": 0.5, "case_count": 2}

    empty = aggregate_ir([])
    assert empty == {"recall_at_k": 0.0, "precision_at_k": 0.0, "mrr": 0.0, "case_count": 0}


def test_aggregate_by_topic_groups():
    results = [
        {"recall_at_k": 1.0, "precision_at_k": 1.0, "reciprocal_rank": 1.0},
        {"recall_at_k": 0.0, "precision_at_k": 0.0, "reciprocal_rank": 0.0},
        {"recall_at_k": 0.5, "precision_at_k": 0.5, "reciprocal_rank": 0.5},
    ]
    by_topic = aggregate_by_topic(["mysql", "mysql", "redis"], results)

    assert by_topic["mysql"]["case_count"] == 2
    assert by_topic["mysql"]["recall_at_k"] == 0.5
    assert by_topic["redis"]["case_count"] == 1


def test_refusal_accuracy():
    assert refusal_accuracy([True, True, True, False]) == 0.75
    assert refusal_accuracy([]) == 0.0


def test_sources_from_docs_keeps_order():
    docs = [
        {"content": "1", "metadata": {"source": "/a/b.txt"}},
        {"content": "2", "metadata": {}},
        {"content": "3", "metadata": {"source": "c.txt"}},
    ]
    assert sources_from_docs(docs) == ["b.txt", "", "c.txt"]


# ==================== retrieval_grader IR 扩展 ====================


def test_retrieval_grader_appends_ir_metrics():
    """带 expected_sources 的 case 应在 details 中附带 IR 指标，且不影响原有 pass/fail"""
    case = {
        "id": "ir-test-1",
        "category": "rag_retrieval",
        "user_input": "测试查询",
        "success_criteria": "命中标准出处",
        "grader_type": "retrieval_keyword",
        "expected_sources": ["mysql_01_index.txt"],
        "expected_keywords": ["索引"],
    }
    result_docs = {
        "retrieved_docs": [
            {"content": "InnoDB 索引与回表", "metadata": {"original_filename": "mysql_01_index.txt"}},
            {"content": "无关内容", "metadata": {"original_filename": "other.txt"}},
        ]
    }

    graded = retrieval_grade(case, result_docs)

    assert graded["passed"] is True  # 关键词命中，原有逻辑不变
    ir = graded["details"]["ir_metrics"]
    assert ir["recall_at_k"] == 1.0
    assert ir["precision_at_k"] == 0.5
    assert ir["hit_rank"] == 1


def test_retrieval_grader_without_expected_sources_no_ir():
    """无 expected_sources 的 case 不产生 IR 指标（向后兼容）"""
    case = {
        "id": "rag-test-1",
        "category": "rag_retrieval",
        "user_input": "测试查询",
        "success_criteria": "关键词命中",
        "grader_type": "retrieval_keyword",
        "expected_keywords": ["机器学习"],
    }
    result_docs = {
        "retrieved_docs": [
            {"content": "机器学习是人工智能的分支", "metadata": {"source": "notes"}}
        ]
    }

    graded = retrieval_grade(case, result_docs)

    assert graded["passed"] is True
    assert "ir_metrics" not in graded["details"]


# ==================== schema 校验 ====================


def test_schema_accepts_ir_fields():
    case = {
        "id": "ir-schema-1",
        "category": "rag_retrieval",
        "user_input": "q",
        "success_criteria": "s",
        "grader_type": "retrieval_keyword",
        "expected_sources": ["a.txt", "b.txt"],
        "topic": "mysql",
        "ir_top_k": 3,
    }
    assert validate_case(case)["valid"] is True


def test_schema_rejects_bad_ir_fields():
    base = {
        "id": "ir-schema-2",
        "category": "rag_retrieval",
        "user_input": "q",
        "success_criteria": "s",
        "grader_type": "retrieval_keyword",
    }
    assert validate_case({**base, "expected_sources": "a.txt"})["valid"] is False
    assert validate_case({**base, "expected_sources": []})["valid"] is False
    assert validate_case({**base, "expected_sources": [""]})["valid"] is False
    assert validate_case({**base, "topic": 123})["valid"] is False
    assert validate_case({**base, "ir_top_k": 0})["valid"] is False
    assert validate_case({**base, "ir_top_k": True})["valid"] is False


# ==================== ir_eval_cases.jsonl 标注守护 ====================


def test_ir_eval_cases_file_schema_and_coverage():
    """IR 评测集全量 schema 校验 + 规模与结构守护"""
    assert IR_CASES_FILE.exists(), "ir_eval_cases.jsonl 必须存在"

    cases = []
    with open(IR_CASES_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))

    assert 24 <= len(cases) <= 30, f"评测集规模应在 24-30 条，当前 {len(cases)}"

    for case in cases:
        result = validate_case(case)
        assert result["valid"], f"{case.get('id')} schema 无效: {result['errors']}"

        # 可答 case 必须有标准出处与主题
        if not case.get("expected_no_answer"):
            assert case.get("expected_sources"), f"{case['id']} 可答 case 缺少 expected_sources"
            assert case.get("topic"), f"{case['id']} 可答 case 缺少 topic"
        else:
            # 不可答 case 必须有判定词，保证拒答正确率可计算
            assert case.get("forbidden_keywords"), f"{case['id']} 不可答 case 缺少 forbidden_keywords"

    # 标准出处必须真实存在于 seed_docs
    seed_dir = BACKEND_DIR / "evals" / "seed_docs"
    for case in cases:
        for source in case.get("expected_sources", []):
            assert (seed_dir / source).exists(), f"{case['id']} 标注出处不存在: {source}"

    # 结构守护：覆盖可答/不可答/跨文档三类
    answerable = [c for c in cases if not c.get("expected_no_answer")]
    unanswerable = [c for c in cases if c.get("expected_no_answer")]
    cross_doc = [c for c in answerable if len(c.get("expected_sources", [])) > 1]

    assert len(unanswerable) >= 3, "不可答 case 至少 3 条"
    assert len(cross_doc) >= 2, "跨文档 case 至少 2 条"
    topics = {c.get("topic") for c in answerable}
    assert len(topics) >= 8, f"主题覆盖应 >= 8 个，当前 {len(topics)}"
