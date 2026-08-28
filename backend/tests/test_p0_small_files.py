"""R3-P0A.3 小文件清零 —— sse_models / llm_cache / path_tool / config 系。

conftest 预注册了 app.cache.llm_cache 与 app.utils.path_tool 的 mock，
用 importlib 直载真实实现（无 app 内依赖或依赖可注入），全程零全局污染。
"""
import importlib.util
import json

import pytest


def _load_real(name, path, register=False):
    import sys
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _p(rel):
    from pathlib import Path
    return str(Path(__file__).resolve().parent.parent / rel)


_sse = _load_real("app.rag.sse_models", _p("app/rag/sse_models.py"))
SSEEvent = _sse.SSEEvent
SliceResult = _sse.SliceResult
EVENT_RESPONSE, EVENT_ERROR, EVENT_DONE = (
    _sse.EVENT_RESPONSE, _sse.EVENT_ERROR, _sse.EVENT_DONE)



# ---------- SSE 事件模型 ----------

def test_sse_event_to_sse_format():
    ev = SSEEvent(event_type="response", message="处理中",
                  total_files=3, file_index=1, filename="a.pdf",
                  step="slicing", progress=33)
    raw = ev.to_sse()
    assert raw.startswith("event: progress\n")
    body = json.loads(raw.split("data: ", 1)[1].strip())
    assert body["filename"] == "a.pdf"
    assert body["step"] == "slicing"
    # None 字段被过滤
    assert "error_message" not in body and "chunk_count" not in body


def test_sse_event_with_all_fields():
    ev = SSEEvent(event_type=EVENT_DONE, message="完成", success_count=2,
                  failed_count=1, slice_success_count=5, chunk_count=4,
                  error_message="部分失败")
    payload = json.loads(ev.to_sse().split("data: ", 1)[1].strip())
    assert payload["event_type"] == "done"
    assert payload["failed_count"] == 1
    assert payload["error_message"] == "部分失败"
    assert EVENT_RESPONSE == "response" and EVENT_ERROR == "error"


def test_slice_result_success_factory():
    r = SliceResult.success_result(2, "b.txt", ["c1", "c2"], "MD5X")
    assert r.success is True and r.chunk_count == 2
    assert r.file_index == 2 and r.md5 == "MD5X"
    d = r.to_dict()
    assert d["success"] is True and d["documents"] == ["c1", "c2"]


def test_slice_result_error_factory():
    r = SliceResult.error_result(0, "bad.pdf", "解析失败")
    assert r.success is False
    assert r.error == "解析失败"
    assert r.chunk_count == 0


# ---------- llm_cache ----------

def test_llm_cache_build_key():
    llm_cache = _load_real("app.cache.llm_cache", _p("app/cache/llm_cache.py"))
    k1 = llm_cache._build_cache_key("同一提示", "model-a")
    k2 = llm_cache._build_cache_key("同一提示", "model-a")
    k3 = llm_cache._build_cache_key("另一提示", "model-a")
    assert k1 == k2 and k1 != k3
    assert k1.startswith("llm:model-a:")


@pytest.mark.asyncio
async def test_llm_cache_hit_and_miss(monkeypatch):
    llm_cache = _load_real("app.cache.llm_cache", _p("app/cache/llm_cache.py"))
    import app.db.redis_config as rc

    async def fake_get(k):
        return "缓存内容"

    async def fake_set(k, v, expire=3600):
        return True

    monkeypatch.setattr(rc, "get_redis_cache_str", fake_get)
    monkeypatch.setattr(rc, "set_redis_cache", fake_set)
    got = await llm_cache.get_cached_llm_response("p", "m")
    assert got == "缓存内容"

    async def fake_miss(k):
        return None
    monkeypatch.setattr(rc, "get_redis_cache_str", fake_miss)
    assert await llm_cache.get_cached_llm_response("p", "m") is None

    ok = await llm_cache.set_cached_llm_response("p", "m", "响应")
    assert ok is True


@pytest.mark.asyncio
async def test_llm_cache_redis_failure_is_silent(monkeypatch):
    llm_cache = _load_real("app.cache.llm_cache", _p("app/cache/llm_cache.py"))
    import app.db.redis_config as rc
    def boom(*a, **k):
        raise RuntimeError("redis down")
    monkeypatch.setattr(rc, "get_redis_cache_str", boom)
    monkeypatch.setattr(rc, "set_redis_cache", boom)
    assert await llm_cache.get_cached_llm_response("p", "m") is None
    assert await llm_cache.set_cached_llm_response("p", "m", "r") is False


# ---------- path_tool ----------

def test_path_tool_resolution():
    pt = _load_real("app.utils.path_tool", _p("app/utils/path_tool.py"))
    root = pt.get_project_root()
    assert root.endswith("backend")
    abs_p = pt.get_abstract_path("app/config/chroma.yaml")
    assert abs_p.endswith(("app/config/chroma.yaml", "chroma.yaml"))
    assert pt.get_data_path().endswith("data")
    # 相对路径归一化
    assert pt.get_abstract_path("../x") != "../x"


# ---------- config 系 load_config ----------

def test_load_config_from_yaml(tmp_path):
    cfg_mod = _load_real("app.utils.config_handler", _p("app/utils/config_handler.py"))
    f = tmp_path / "c.yaml"
    f.write_text("key: value\nnum: 1\n", encoding="utf-8")
    out = cfg_mod.load_config(str(f))
    assert out == {"key": "value", "num": 1}


def test_utils_config_loads_real_chroma_yaml():
    cfg_mod = _load_real("app.utils.config", _p("app/utils/config.py"))
    assert isinstance(cfg_mod.chroma_config, dict)
    assert "collection_name" in cfg_mod.chroma_config
