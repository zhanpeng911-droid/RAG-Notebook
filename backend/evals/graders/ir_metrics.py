"""IR（信息检索）标准指标 —— 纯函数实现，零外部依赖，可在 CI 中直接测试。

指标语义（与业界 RAG 评测对齐）：
- Recall@K（出处级）  ：case 的标准出处文档（expected_sources）是否出现在 Top-K 检索结果的出处集合中。
                        单 case 取值 0/1，多 case 取均值。代表检索能力的天花板。
- Precision@K（chunk 级）：Top-K 切片中来自标准出处文档的比例。反映送入上下文的噪声量。
- MRR（Mean Reciprocal Rank）：第一个命中标准出处的切片排名的倒数。单 case 未命中记 0。
- 拒答正确率          ：不可答 case（expected_no_answer=true）中 Top-K 未命中任何标准出处的比例。

出处匹配规则：
- 检索结果的 source 可能是完整路径（Windows/Unix）或纯文件名，统一取 basename 后比对；
- expected_sources 统一标注为文件名（如 "mysql_01_index.txt"）。
"""

import os
from typing import Iterable, List, Optional, Sequence

# 从 doc metadata 中提取出处时依次尝试的字段
_SOURCE_KEYS = ("original_filename", "source", "filename")


def normalize_source(source: Optional[str]) -> str:
    """归一化出处标识：取 basename，兼容 Windows / Unix 路径与空值。"""
    if not source or not isinstance(source, str):
        return ""
    return os.path.basename(source.replace("\\", "/")).strip()


def extract_source(doc: dict) -> str:
    """从检索结果 doc（dict 形式，含 metadata）提取归一化出处文件名。"""
    metadata = doc.get("metadata") or {}
    for key in _SOURCE_KEYS:
        value = metadata.get(key) if isinstance(metadata, dict) else None
        if value:
            normalized = normalize_source(str(value))
            if normalized:
                return normalized
    return ""


def sources_from_docs(docs: Iterable[dict]) -> List[str]:
    """按排序顺序提取每个 doc 的出处（保持排名信息）。"""
    return [extract_source(doc) for doc in docs]


def hit_relevant(source: str, relevant_sources: Iterable[str]) -> bool:
    """判断单个出处是否命中标准出处集合。"""
    normalized = normalize_source(source)
    return any(normalized == normalize_source(r) for r in relevant_sources if r)


def recall_at_k(
    ranked_sources: Sequence[str],
    relevant_sources: Sequence[str],
    k: int,
) -> float:
    """出处级 Recall@K：标准出处是否出现在 Top-K 的出处集合中。

    多个标准出处时按"命中的出处数 / 总出处数"计（软召回），
    便于跨文档 case 得到部分分。
    """
    if not relevant_sources or k <= 0:
        return 0.0
    top_sources = {normalize_source(s) for s in ranked_sources[:k] if s}
    relevant = {normalize_source(r) for r in relevant_sources if r}
    if not relevant:
        return 0.0
    return len(top_sources & relevant) / len(relevant)


def precision_at_k(
    ranked_sources: Sequence[str],
    relevant_sources: Sequence[str],
    k: int,
) -> float:
    """chunk 级 Precision@K：Top-K 切片中来自标准出处文档的比例。"""
    if k <= 0:
        return 0.0
    top = [normalize_source(s) for s in ranked_sources[:k] if s]
    if not top:
        return 0.0
    hits = sum(1 for s in top if hit_relevant(s, relevant_sources))
    return hits / len(top)


def reciprocal_rank(
    ranked_sources: Sequence[str],
    relevant_sources: Sequence[str],
) -> float:
    """单 case 的 RR：第一个命中标准出处的排名倒数，未命中记 0。"""
    for rank, source in enumerate(ranked_sources, start=1):
        if source and hit_relevant(source, relevant_sources):
            return 1.0 / rank
    return 0.0


def refusal_correct(
    ranked_sources: Sequence[str],
    relevant_sources: Sequence[str],
    k: int,
) -> bool:
    """不可答 case 判定：Top-K 中不应命中任何标准出处。

    不可答 case 通常没有 expected_sources（语义上"语料库中没有答案"），
    此时退化为"Top-K 不包含任何语料命中"由调用方判定；有 expected_sources
    （如跨主题干扰 case）时按此函数判定。
    """
    top = [normalize_source(s) for s in ranked_sources[:k] if s]
    return not any(hit_relevant(s, relevant_sources) for s in top)


def grade_case_ir(
    ranked_docs: Sequence[dict],
    relevant_sources: Sequence[str],
    k: int = 3,
) -> dict:
    """计算单个 case 的全套 IR 指标。

    :param ranked_docs: 按相关性降序的检索结果（dict，含 metadata）
    :param relevant_sources: 标准出处文件名列表
    :param k: Top-K
    :return: {"recall_at_k", "precision_at_k", "reciprocal_rank", "hit_rank", "retrieved_count"}
    """
    ranked_sources = sources_from_docs(ranked_docs)

    hit_rank = None
    for rank, source in enumerate(ranked_sources, start=1):
        if source and hit_relevant(source, relevant_sources):
            hit_rank = rank
            break

    return {
        "recall_at_k": round(recall_at_k(ranked_sources, relevant_sources, k), 4),
        "precision_at_k": round(precision_at_k(ranked_sources, relevant_sources, k), 4),
        "reciprocal_rank": round(reciprocal_rank(ranked_sources, relevant_sources), 4),
        "hit_rank": hit_rank,
        "retrieved_count": len(ranked_docs),
        "k": k,
    }


def aggregate_ir(per_case_results: Sequence[dict]) -> dict:
    """汇总多 case 的 IR 指标（各指标取均值，保留 4 位小数）。

    :param per_case_results: 每项为 grade_case_ir 的返回值
    :return: {"recall_at_k", "precision_at_k", "mrr", "case_count"}
    """
    if not per_case_results:
        return {"recall_at_k": 0.0, "precision_at_k": 0.0, "mrr": 0.0, "case_count": 0}

    count = len(per_case_results)
    return {
        "recall_at_k": round(sum(r["recall_at_k"] for r in per_case_results) / count, 4),
        "precision_at_k": round(sum(r["precision_at_k"] for r in per_case_results) / count, 4),
        "mrr": round(sum(r["reciprocal_rank"] for r in per_case_results) / count, 4),
        "case_count": count,
    }


def aggregate_by_topic(
    topics: Sequence[str],
    per_case_results: Sequence[dict],
) -> dict:
    """按主题分组汇总指标（用于定位哪类知识检索质量偏低）。

    :param topics: 与 per_case_results 一一对应的主题标签
    :return: {topic: aggregate_ir 结果}
    """
    grouped: dict = {}
    for topic, result in zip(topics, per_case_results):
        grouped.setdefault(topic, []).append(result)
    return {topic: aggregate_ir(results) for topic, results in grouped.items()}


def refusal_accuracy(refusal_results: Sequence[bool]) -> float:
    """拒答正确率：不可答 case 中正确"未命中"的比例。"""
    if not refusal_results:
        return 0.0
    return round(sum(1 for r in refusal_results if r) / len(refusal_results), 4)
