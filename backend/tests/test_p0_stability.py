"""
P0 回归测试：
- init_db 不再 create_all
- Chroma 初始化失败不删库
- 显式 reset 才会删目录
- Alembic / slim 依赖清单存在
"""
import ast
import importlib.util
import inspect
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
APP_RAG = BACKEND_ROOT / "app" / "rag"


def _load_real_vector_store():
    """绕过 conftest 的 MagicMock，加载真实 vector_store；结束后恢复 sys.modules。"""
    if str(BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT))

    # 快照可能被我们改动的模块
    touch_keys = [
        "app.rag.vector_store",
        "app.utils.config",
        "app.utils.factory",
        "app.utils.path_tool",
        "app.core.logger_handler",
        "app.rag.retrievers.hybrid_retriever",
        "app.rag.md5_manager",
        "app.rag.document_handler",
        "app.utils.image_extractor",
        "langchain_chroma",
        "langchain_core.documents",
        "app.rag.vector_store_real_p0",
    ]
    saved = {k: sys.modules.get(k) for k in touch_keys}
    created = set()

    def _set_stub(name, module):
        if name not in sys.modules:
            created.add(name)
        sys.modules[name] = module

    try:
        # 轻量 stub，仅用于加载 vector_store 源码
        if "langchain_chroma" not in sys.modules or isinstance(sys.modules.get("langchain_chroma"), MagicMock):
            lc = ModuleType("langchain_chroma")
            lc.Chroma = MagicMock
            _set_stub("langchain_chroma", lc)

        if "langchain_core.documents" not in sys.modules or isinstance(
            sys.modules.get("langchain_core.documents"), MagicMock
        ):
            docs = ModuleType("langchain_core.documents")

            class Document:
                pass

            docs.Document = Document
            _set_stub("langchain_core.documents", docs)

        # 仅 stub vector_store 的直接依赖；不要永久污染 app.utils.factory 业务导出名
        cfg = ModuleType("app.utils.config")
        cfg.chroma_config = {"persist_directory": "/tmp/p0_chroma", "collection_name": "test"}
        _set_stub("app.utils.config", cfg)

        path_tool = ModuleType("app.utils.path_tool")
        path_tool.get_abstract_path = lambda x: x
        _set_stub("app.utils.path_tool", path_tool)

        factory = ModuleType("app.utils.factory")
        factory.embed_model = MagicMock()
        _set_stub("app.utils.factory", factory)

        logger_mod = ModuleType("app.core.logger_handler")
        logger_mod.logger = MagicMock()
        _set_stub("app.core.logger_handler", logger_mod)

        hr = ModuleType("app.rag.retrievers.hybrid_retriever")
        hr.HybridRetriever = MagicMock
        _set_stub("app.rag.retrievers.hybrid_retriever", hr)

        md5 = ModuleType("app.rag.md5_manager")
        md5.MD5Store = MagicMock
        _set_stub("app.rag.md5_manager", md5)

        dh = ModuleType("app.rag.document_handler")
        dh.DocumentProcessor = MagicMock
        _set_stub("app.rag.document_handler", dh)

        ie = ModuleType("app.utils.image_extractor")
        ie.delete_image_directory = MagicMock()
        ie.delete_user_all_images = MagicMock()
        _set_stub("app.utils.image_extractor", ie)

        # 确保不使用 conftest mock 的 vector_store
        sys.modules.pop("app.rag.vector_store", None)

        path = APP_RAG / "vector_store.py"
        spec = importlib.util.spec_from_file_location("app.rag.vector_store_real_p0", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["app.rag.vector_store_real_p0"] = mod
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        return mod
    finally:
        # 恢复快照，避免污染后续 test_factory_config / import_smoke
        for key, val in saved.items():
            if val is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = val
        for key in created:
            if key not in saved or saved[key] is None:
                # already handled by saved restore; double-safe
                pass


def test_init_db_does_not_call_create_all():
    from app.db import db_config

    tree = ast.parse(inspect.getsource(db_config))
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and (
            (isinstance(n.func, ast.Attribute) and n.func.attr == "create_all")
            or (isinstance(n.func, ast.Name) and n.func.id == "create_all")
        )
    ]
    assert calls == [], "db_config 中不应再调用 create_all"

    init_src = inspect.getsource(db_config.init_db)
    assert "SELECT 1" in init_src or "text(" in init_src


def test_vector_store_source_no_auto_rmtree_on_init():
    src = (APP_RAG / "vector_store.py").read_text(encoding="utf-8")
    assert "即将重置数据库" not in src
    assert "reset_chroma_db_explicit" in src
    assert "不会自动删除" in src or "不自动删除" in src or "禁止自动删除" in src
    assert "is_degraded" in src


def test_reset_chroma_db_explicit_deletes_only_when_called(tmp_path):
    vs = _load_real_vector_store()
    target = tmp_path / "chroma_data"
    target.mkdir()
    (target / "marker.txt").write_text("keep-me", encoding="utf-8")
    vs.reset_chroma_db_explicit(str(target))
    assert not target.exists()


def test_vector_store_init_failure_marks_degraded_and_keeps_dir(tmp_path, monkeypatch):
    vs = _load_real_vector_store()
    cls = vs.VectorStoreService
    cls._instance = None
    cls._initialized = False
    cls._degraded = False
    cls._degraded_reason = ""

    persist = tmp_path / "chroma_keep"
    persist.mkdir()
    marker = persist / "important.db"
    marker.write_text("data", encoding="utf-8")

    monkeypatch.setattr(vs, "get_abstract_path", lambda x: str(persist))
    monkeypatch.setattr(
        vs,
        "chroma_config",
        {"persist_directory": str(persist), "collection_name": "t"},
    )
    monkeypatch.setattr(vs, "_clear_chroma_cache", lambda: None)

    def boom(_self, _dir):
        raise RuntimeError("simulated chroma corruption")

    monkeypatch.setattr(cls, "_init_chroma", boom)

    with pytest.raises(RuntimeError):
        cls()

    assert cls.is_degraded() is True
    assert "simulated chroma corruption" in cls.degraded_reason()
    assert marker.exists(), "初始化失败时不得删除数据目录"


def test_alembic_initial_migration_exists():
    versions = BACKEND_ROOT / "alembic" / "versions"
    files = list(versions.glob("*.py"))
    assert files, "缺少 alembic versions 迁移文件"
    content = "\n".join(f.read_text(encoding="utf-8") for f in files)
    assert "001_initial" in content or "chat_sessions" in content
    assert (BACKEND_ROOT / "alembic.ini").exists()
    assert (BACKEND_ROOT / "alembic" / "env.py").exists()


def test_pyproject_has_alembic_and_optional_heavy_deps():
    text = (BACKEND_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "alembic" in text
    assert "local-embed" in text
    assert "sentence-transformers" in text
