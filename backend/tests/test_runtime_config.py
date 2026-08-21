"""方案5 回归测试：运行时配置（检索参数热更新）。

覆盖：
- 参数默认值读取与未知 key 防护
- 值校验（类型 / 范围 / bool / 置信度阈值耦合）
- 读取点生效性：planner top_k / grader 阈值 / retrieval_service 候选倍数
"""
import pytest

from app.core import runtime_config
from app.core.runtime_config import PARAM_DEFS, _validate_and_coerce


@pytest.fixture(autouse=True)
def _clean_overrides():
    """每个测试前后清空内存覆盖缓存，避免测试间污染。"""
    with runtime_config._lock:
        runtime_config._overrides.clear()
    yield
    with runtime_config._lock:
        runtime_config._overrides.clear()


def _set_override(key, value):
    with runtime_config._lock:
        runtime_config._overrides[key] = value


# ==================== 默认值与读取 ====================


def test_get_returns_defaults_without_db():
    """无 DB 覆盖时返回注册默认值，且与原代码常量一致（行为不变守护）"""
    assert runtime_config.get("retrieval.top_k_baseline") == 5
    assert runtime_config.get("retrieval.chroma_k") == 6
    assert runtime_config.get("retrieval.rerank_candidate_multiplier") == 3
    assert runtime_config.get("retrieval.rerank_enabled") is True
    assert runtime_config.get("grader.min_relevance") == 0.3
    assert runtime_config.get("grader.confidence_high") == 0.7
    assert runtime_config.get("grader.confidence_medium") == 0.4
    assert runtime_config.get("grader.confidence_low") == 0.1


def test_get_unknown_key_raises():
    """未注册的 key 必须抛 KeyError（编码错误尽早暴露）"""
    with pytest.raises(KeyError):
        runtime_config.get("not.registered")


def test_get_returns_override_when_set():
    _set_override("retrieval.top_k_baseline", 8)
    assert runtime_config.get("retrieval.top_k_baseline") == 8


def test_get_all_structure():
    """get_all 返回完整参数视图（含默认值与覆盖标记）"""
    _set_override("retrieval.chroma_k", 10)

    params = {p["key"]: p for p in runtime_config.get_all()}

    assert set(params.keys()) == set(PARAM_DEFS.keys())
    chroma = params["retrieval.chroma_k"]
    assert chroma["value"] == 10
    assert chroma["default"] == 6
    assert chroma["overridden"] is True
    assert chroma["min_value"] == 3 and chroma["max_value"] == 20

    baseline = params["retrieval.top_k_baseline"]
    assert baseline["overridden"] is False
    assert baseline["value"] == baseline["default"]


# ==================== 校验逻辑 ====================


def _effective_defaults():
    with runtime_config._lock:
        return {k: PARAM_DEFS[k].default for k in PARAM_DEFS}


def test_validate_rejects_unknown_key():
    with pytest.raises(ValueError, match="未知的运行时配置参数"):
        _validate_and_coerce("not.registered", 1, _effective_defaults())


def test_validate_rejects_out_of_range():
    with pytest.raises(ValueError, match="超出范围"):
        _validate_and_coerce("retrieval.top_k_baseline", 99, _effective_defaults())
    with pytest.raises(ValueError, match="超出范围"):
        _validate_and_coerce("grader.min_relevance", 0.9, _effective_defaults())


def test_validate_rejects_wrong_type():
    with pytest.raises(ValueError, match="int 类型"):
        _validate_and_coerce("retrieval.top_k_baseline", "5", _effective_defaults())
    with pytest.raises(ValueError, match="int 类型"):
        _validate_and_coerce("retrieval.top_k_baseline", 5.5, _effective_defaults())
    with pytest.raises(ValueError, match="bool 类型"):
        _validate_and_coerce("retrieval.rerank_enabled", "yes", _effective_defaults())


def test_validate_coerces_bool_from_json_int():
    """bool 参数兼容 JSON 里的 0/1 输入"""
    effective = _effective_defaults()
    assert _validate_and_coerce("retrieval.rerank_enabled", 1, effective) is True
    assert _validate_and_coerce("retrieval.rerank_enabled", 0, effective) is False


def test_validate_confidence_thresholds_must_be_ordered():
    """置信度阈值组合校验：high > medium > low"""
    base = _effective_defaults()

    # high（范围内）低于当前 medium 应被拒绝
    with pytest.raises(ValueError, match="high > medium > low"):
        _validate_and_coerce(
            "grader.confidence_high", 0.5,
            {**base, "grader.confidence_medium": 0.6},
        )

    # medium（范围内）低于当前 low 应被拒绝
    with pytest.raises(ValueError, match="high > medium > low"):
        _validate_and_coerce(
            "grader.confidence_medium", 0.25,
            {**base, "grader.confidence_low": 0.3},
        )

    # 合法值通过
    assert _validate_and_coerce("grader.confidence_high", 0.9, base) == 0.9


# ==================== 读取点生效性 ====================


def test_planner_top_k_follows_runtime_baseline():
    """planner 的 top_k 必须基于运行时基准值（各类型策略偏移保留）"""
    from app.agentic.planner import planner, QueryType

    _set_override("retrieval.top_k_baseline", 10)

    plan_factual = planner.create_plan("什么是MVCC", "user-1")
    plan_explanatory = planner.create_plan("为什么需要消息队列", "user-1")
    plan_comparative = planner.create_plan("RDB 与 AOF 的区别", "user-1")

    assert plan_factual.top_k == 10
    assert plan_explanatory.top_k == 13      # baseline + 3
    assert plan_comparative.top_k == 15      # baseline + 5
    assert plan_factual.query_type is QueryType.FACTUAL


def test_grader_thresholds_follow_runtime_config():
    """证据评估阈值必须读取运行时配置"""
    from app.agentic.retrieval_grader import EvidenceGrader
    from app.rag.retrieval_service import Evidence

    grader = EvidenceGrader()

    def _ev(score):
        return Evidence(
            source_type="knowledge", source_id="d1", chunk_id="c1",
            title="t", content="c", score=score,
        )

    # 提高相关性阈值：原本 0.35 的证据从"相关"变为"不相关"
    _set_override("grader.min_relevance", 0.5)
    grading = grader.grade("查询", [_ev(0.35)])
    assert grading.confidence_level == "none"
    assert grading.is_sufficient is False

    # 降低阈值后同样的证据变为可用
    _set_override("grader.min_relevance", 0.1)
    grading = grader.grade("查询", [_ev(0.35)])
    assert grading.confidence_level in ("low", "medium", "high")
    assert grading.is_sufficient is True

    # 提高 high 阈值：原本 high 的置信度分级降级
    _set_override("grader.min_relevance", 0.1)
    _set_override("grader.confidence_high", 0.95)
    grading = grader.grade("查询", [_ev(0.9)])
    assert grading.confidence_level != "high"


def test_retrieval_candidate_multiplier_follows_runtime():
    """重排候选集倍数必须读取运行时配置"""
    import asyncio
    from unittest.mock import AsyncMock, patch
    from app.rag.retrieval_service import RetrievalService

    service = RetrievalService(user_id="user-1")

    captured = {}

    async def _fake_retrieve_knowledge(query, k, space_id, use_hyde):
        captured["candidate_k"] = k
        return []

    with patch.object(service, "_retrieve_knowledge", _fake_retrieve_knowledge), \
         patch.object(service, "_retrieve_notes", AsyncMock(return_value=[])):
        _set_override("retrieval.rerank_candidate_multiplier", 5)
        asyncio.run(service.retrieve("查询", scope="knowledge", top_k=4, use_rerank=True))

    assert captured["candidate_k"] == 20  # 4 × 5


def test_retrieval_rerank_disabled_skips_candidate_expansion():
    """全局关闭重排后不扩大候选集"""
    import asyncio
    from unittest.mock import AsyncMock, patch
    from app.rag.retrieval_service import RetrievalService

    service = RetrievalService(user_id="user-1")

    captured = {}

    async def _fake_retrieve_knowledge(query, k, space_id, use_hyde):
        captured["candidate_k"] = k
        return []

    with patch.object(service, "_retrieve_knowledge", _fake_retrieve_knowledge), \
         patch.object(service, "_retrieve_notes", AsyncMock(return_value=[])):
        _set_override("retrieval.rerank_enabled", False)
        asyncio.run(service.retrieve("查询", scope="knowledge", top_k=4, use_rerank=True))

    assert captured["candidate_k"] == 4  # 不扩候选
