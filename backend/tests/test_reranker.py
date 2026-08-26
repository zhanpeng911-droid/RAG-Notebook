"""Reranker 单元测试 —— 降级路径、payload 构造与结果映射。

外部 HTTP 用 monkeypatch 替换 urllib.request.urlopen，全程不触网。
"""
import json

import pytest

import app.rag.reranker as reranker_module
from app.rag.reranker import RerankResult, Reranker


class _FakeResponse:
    def __init__(self, body: dict):
        self._body = body

    def read(self) -> bytes:
        return json.dumps(self._body).encode("utf-8")


@pytest.fixture()
def no_key_reranker():
    r = Reranker(api_key="placeholder")
    r.api_key = ""  # 强制走“未配置密钥”分支
    return r


@pytest.mark.asyncio
async def test_no_api_key_returns_empty_without_network(no_key_reranker):
    called = False

    def _fail(*a, **k):
        nonlocal called
        called = True
        raise AssertionError("不应发起网络请求")

    monkey = pytest.MonkeyPatch()
    monkey.setattr("urllib.request.urlopen", _fail)
    try:
        assert await no_key_reranker.rerank("q", ["doc"]) == []
    finally:
        monkey.undo()
    assert called is False


@pytest.mark.asyncio
async def test_empty_documents_short_circuits():
    r = Reranker(api_key="k")
    assert await r.rerank("q", []) == []


@pytest.mark.asyncio
async def test_success_maps_index_score_and_text(monkeypatch):
    captured = {}

    def fake_urlopen(req, *args, **kwargs):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({
            "output": {"results": [
                {"index": 2, "relevance_score": 0.9},
                {"index": 0, "relevance_score": 0.4},
            ]},
        })

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    r = Reranker(api_key="test-key")
    results = await r.rerank("查询", ["甲", "乙", "丙"], top_n=2)

    # 结果按 API 给定顺序返回，index 映射回原文
    assert [x.index for x in results] == [2, 0]
    assert [x.score for x in results] == [0.9, 0.4]
    assert isinstance(results[0], RerankResult)
    assert results[0].text == "丙"
    assert results[1].text == "甲"

    assert captured["url"].endswith("/text-rerank")
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["payload"]["input"]["query"] == "查询"
    assert captured["payload"]["input"]["top_n"] == 2
    assert captured["payload"]["parameters"]["return_documents"] is False


@pytest.mark.asyncio
async def test_top_n_omitted_when_none(monkeypatch):
    payload_box = {}

    def fake_urlopen(req, *args, **kwargs):
        payload_box["data"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({"output": {"results": []}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    r = Reranker(api_key="k")
    assert await r.rerank("q", ["d"]) == []
    assert "top_n" not in payload_box["data"]["input"]


@pytest.mark.asyncio
async def test_out_of_range_index_maps_to_empty_text(monkeypatch):
    def fake_urlopen(req, *args, **kwargs):
        return _FakeResponse({
            "output": {"results": [{"index": 99, "relevance_score": 1.0}]},
        })

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    r = Reranker(api_key="k")
    results = await r.rerank("q", ["only"])
    assert results[0].text == ""


@pytest.mark.asyncio
async def test_network_error_degrades_to_empty_list(monkeypatch):
    def boom(req, *args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    r = Reranker(api_key="k")
    assert await r.rerank("q", ["doc1", "doc2"]) == []


def test_default_model_and_singleton():
    # rerank 成功路径里分数经 float() 强转；dataclass 本身不转型
    assert reranker_module.reranker.model == "qwen3-vl-rerank"
