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

# 主要主题的最少用例数（保证分主题得分的排序意义）
MAIN_TOPICS = ["mysql", "redis", "python", "network", "mq", "linux", "docker", "k8s", "go", "algo"]


def _load_ir_cases():
    with open(IR_CASES_FILE, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _seed_corpus_text():
    """拼接全部评测语料文本（用于禁答词语料级排除校验）。"""
    seed_dir = BACKEND_DIR / "evals" / "seed_docs"
    return "\n".join(p.read_text(encoding="utf-8") for p in seed_dir.glob("*.txt"))


def _contains(text: str, keyword: str) -> bool:
    """关键词包含判定：ASCII 关键词大小写不敏感，中文子串。"""
    if keyword.isascii():
        return keyword.lower() in text.lower()
    return keyword in text


def test_ir_eval_cases_file_schema_and_coverage():
    """IR 评测集全量 schema 校验 + 规模与结构守护

    规模依据：p≈0.9 时 n≈100 的 95% CI 半宽约 ±5.9pp（可对外引用门槛）；
    配对对比要分辨 10pp 增益需 n≥80-100。低于 95 视为退化，高于 120 视为冗余。
    """
    assert IR_CASES_FILE.exists(), "ir_eval_cases.jsonl 必须存在"

    cases = _load_ir_cases()
    assert 95 <= len(cases) <= 120, (
        f"评测集规模应在 95-120 条（统计置信度要求），当前 {len(cases)}"
    )

    # ID 唯一性
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "case id 存在重复"

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

    # 结构守护：主题/跨文档/边界/不可答的配额
    answerable = [c for c in cases if not c.get("expected_no_answer")]
    unanswerable = [c for c in cases if c.get("expected_no_answer")]
    cross_doc = [c for c in answerable if len(c.get("expected_sources", [])) > 1]
    edge = [c for c in answerable if c.get("topic") == "edge"]

    assert len(unanswerable) >= 10, "不可答 case 至少 10 条（覆盖 10 类域外问题）"
    assert len(cross_doc) >= 6, "跨文档 case 至少 6 条"
    assert len(edge) >= 5, "边界 case 至少 5 条"

    # 每个主要主题至少 5 条，保证分主题得分可用于定位薄弱环节
    topic_counts = {}
    for c in answerable:
        topic_counts[c.get("topic")] = topic_counts.get(c.get("topic"), 0) + 1
    for topic in MAIN_TOPICS:
        assert topic_counts.get(topic, 0) >= 5, (
            f"主题 {topic} 用例不足 5 条（当前 {topic_counts.get(topic, 0)}），分主题得分无排序意义"
        )


def test_ir_eval_case_keywords_exist_in_sources():
    """标注质量守护：每个可答 case 的关键词必须真实出现在其标注出处的内容中。

    防止"关键词凭印象写"导致评测永远失败的标注错误。
    """
    cases = _load_ir_cases()
    seed_dir = BACKEND_DIR / "evals" / "seed_docs"

    for case in cases:
        if case.get("expected_no_answer"):
            continue
        sources_text = "\n".join(
            (seed_dir / s).read_text(encoding="utf-8") for s in case.get("expected_sources", [])
        )
        for kw in case.get("expected_keywords", []):
            assert _contains(sources_text, kw), (
                f"{case['id']} 关键词 {kw!r} 未出现在标注出处 {case['expected_sources']} 中（标注错误）"
            )


def test_ir_eval_forbidden_keywords_absent_from_corpus():
    """不可答判定有效性守护：禁答词不得出现在评测语料的任何位置。

    若禁答词本身就在语料里，"Top-K 不含禁答词"的判定会把正确命中误判为失败。
    """
    cases = _load_ir_cases()
    corpus = _seed_corpus_text()

    for case in cases:
        for kw in case.get("forbidden_keywords", []):
            assert not _contains(corpus, kw), (
                f"{case['id']} 禁答词 {kw!r} 出现在评测语料中，该 case 的拒答判定无效（应换词）"
            )


def _load_bm25_tokenizer():
    """从文件路径直接加载分词模块（绕过包 __init__ 的 langchain 依赖链）。"""
    import importlib.util

    path = BACKEND_DIR / "app" / "rag" / "retrievers" / "bm25_tokenizer.py"
    spec = importlib.util.spec_from_file_location("bm25_tokenizer_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_bm25_tokenizer_handles_chinese():
    """BM25 中文分词守护：修复前 BM25Retriever 默认按空格分词，
    中文查询切不出 token，BM25 一路在中文场景近乎失效（IR 评测 recall 仅 0.60）。
    """
    tokenizer = _load_bm25_tokenizer()

    tokens = tokenizer.tokenize_for_bm25("MySQL 索引失效的场景有哪些？")
    # 中文应被切出（而非整句一个 token），且无空白 token
    assert len(tokens) > 2, f"中文分词失效: {tokens}"
    assert all(t.strip() for t in tokens)
    # 英文应小写化（BM25 匹配大小写不敏感）
    assert "mysql" in tokens
    assert any("索引" in t or t == "索引" for t in tokens)
    # 空串容错
    assert tokenizer.tokenize_for_bm25("") == []
