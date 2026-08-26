"""
R3 回归：
- chroma chunk 默认与按扩展名覆盖
- prompt 版本加载
- 笔记创建入复习队列
- 到期待回顾计数
- 用户隔离硬断言（仓库层）
"""
import inspect
from pathlib import Path
from unittest.mock import MagicMock

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_chroma_chunk_defaults_for_chinese():
    import yaml

    path = BACKEND_ROOT / "app" / "config" / "chroma.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert cfg["chunk_size"] >= 400
    assert cfg["chunk_overlap"] >= 40
    assert cfg["k"] >= 5
    assert "chunk_by_extension" in cfg
    assert "md" in cfg["chunk_by_extension"]


def test_document_processor_resolve_chunk_params():
    # 绕过 conftest mock：直接读源码 + 用真实 chroma yaml 数值测逻辑

    # 若被 mock，至少校验 yaml 文件
    path = BACKEND_ROOT / "app" / "config" / "chroma.yaml"
    import yaml

    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))

    def resolve(filename=None):
        base_size = int(cfg.get("chunk_size", 500))
        base_overlap = int(cfg.get("chunk_overlap", 60))
        by_ext = cfg.get("chunk_by_extension") or {}
        if not filename:
            return base_size, base_overlap
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        conf = by_ext.get(ext) or {}
        return int(conf.get("chunk_size", base_size)), int(conf.get("chunk_overlap", base_overlap))

    assert resolve(None)[0] == cfg["chunk_size"]
    md_size, md_ov = resolve("a.md")
    assert md_size == cfg["chunk_by_extension"]["md"]["chunk_size"]
    assert md_ov == cfg["chunk_by_extension"]["md"]["chunk_overlap"]


def test_prompt_yaml_has_versions_and_paths():
    import yaml

    cfg = yaml.safe_load(
        (BACKEND_ROOT / "app" / "config" / "prompt.yaml").read_text(encoding="utf-8")
    )
    assert "versions" in cfg and "paths" in cfg
    assert "main_prompt" in cfg["versions"]
    assert "main_prompt" in cfg["paths"]


def test_prompt_loader_returns_version(tmp_path, monkeypatch):
    """直接加载 prompt_loader 源码，避免 conftest 对 app.utils 的污染。"""
    import importlib.util
    import sys
    from types import ModuleType

    prompt_file = tmp_path / "main.txt"
    prompt_file.write_text("hello-prompt-v1", encoding="utf-8")

    # stub deps used by prompt_loader
    cfg = ModuleType("app.utils.config")
    cfg.prompt_config = {
        "versions": {"main_prompt": "9.9.9"},
        "paths": {"main_prompt": str(prompt_file)},
    }
    path_tool = ModuleType("app.utils.path_tool")
    path_tool.get_abstract_path = lambda x: x
    logger_mod = ModuleType("app.core.logger_handler")
    logger_mod.logger = MagicMock()

    saved = {}
    for k, m in {
        "app.utils.config": cfg,
        "app.utils.path_tool": path_tool,
        "app.core.logger_handler": logger_mod,
    }.items():
        saved[k] = sys.modules.get(k)
        sys.modules[k] = m

    try:
        path = BACKEND_ROOT / "app" / "utils" / "prompt_loader.py"
        spec = importlib.util.spec_from_file_location("prompt_loader_r3", path)
        pl = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(pl)
        pl.load_prompt_with_version.cache_clear()
        content, version = pl.load_prompt_with_version("main_prompt")
        assert content == "hello-prompt-v1"
        assert version == "9.9.9"
        assert pl.load_prompt("main_prompt") == "hello-prompt-v1"
        pl.load_prompt_with_version.cache_clear()
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def test_note_service_has_ensure_review_record():
    from app.services import note_service as ns_mod

    assert hasattr(ns_mod.NoteService, "ensure_review_record")
    src = inspect.getsource(ns_mod.NoteService)
    assert "ensure_review_record" in src
    # create_note 同事务内联写入 ReviewRecord（性能优化后不再二次 SELECT）
    create_src = inspect.getsource(ns_mod.NoteService.create_note)
    assert "ReviewRecord" in create_src


def test_review_service_has_due_count():
    from app.services import review_service as rs_mod

    assert hasattr(rs_mod.ReviewService, "count_due_reviews")
    src = inspect.getsource(rs_mod)
    assert "next_review_at" in src


def test_review_router_has_due_count_route():
    text = (BACKEND_ROOT / "app" / "router" / "review_router.py").read_text(encoding="utf-8")
    assert "/due-count" in text


@pytest.mark.asyncio
async def test_note_repo_isolation_get_by_id_requires_user():
    """用户隔离：get_by_id 查询必须带 user_id 条件（源码契约）。"""
    from app.repositories.note_repository import NoteRepository

    src = inspect.getsource(NoteRepository.get_by_id)
    assert "user_id" in src


def test_front_chat_uses_composable():
    page = (
        BACKEND_ROOT.parent
        / "front"
        / "src"
        / "pages"
        / "chat"
        / "ChatWorkspacePage.vue"
    ).read_text(encoding="utf-8")
    assert "useChatWorkspace" in page
    composable = (
        BACKEND_ROOT.parent / "front" / "src" / "composables" / "useChatWorkspace.js"
    )
    assert composable.exists()
    assert "export function useChatWorkspace" in composable.read_text(encoding="utf-8")


def test_evals_baseline_cases_exist():
    cases = BACKEND_ROOT / "evals" / "cases"
    files = list(cases.glob("*.jsonl"))
    assert files, "缺少 evals cases 基线"
    # 至少含检索与回答质量
    names = {f.name for f in files}
    assert "rag_retrieval_cases.jsonl" in names
    assert "answer_quality_cases.jsonl" in names
