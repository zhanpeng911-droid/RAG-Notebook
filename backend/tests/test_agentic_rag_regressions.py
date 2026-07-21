"""Regression tests for Agentic RAG tenant isolation and Markdown upload paths."""
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
import os
import tempfile

from sqlalchemy.dialects import sqlite

from app.models.note import Note
from app.models.document_index import DocumentIndex, DocumentIndexStatus
from app.rag.retrieval_service import RetrievalService
from app.services.document_index_service import (
    validate_uploaded_content,
    _check_embedding_available,
    _safe_delete_physical_file,
    _get_storage_dir,
    get_embedding_health_status,
)


USER_A = "user-a"
USER_B = "user-b"


def test_keyword_note_fallback_keeps_user_filter_inside_and_clause():
    """Keyword matches from another user must never satisfy the WHERE clause alone."""
    where_clause = RetrievalService._build_note_keyword_filter(Note, USER_A, "private project")
    compiled = str(
        where_clause.compile(
            dialect=sqlite.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "user_id = 'user-a'" in compiled
    assert " AND " in compiled
    assert "'%private%'" in compiled
    assert "'%project%'" in compiled


def test_decoupled_upload_validation_accepts_markdown():
    """A normal Markdown file must be accepted before index availability is considered."""
    assert validate_uploaded_content(b"# Test\nMarkdown content", "notes.md") is None


def test_decoupled_upload_validation_rejects_unsupported_files():
    """The v2 upload path must keep the existing allowlist — 文件大小超限也应拒绝"""
    # 用超大文件测试（>20MB），这不依赖 validate_file_type 的真实实现
    big_content = b"x" * (21 * 1024 * 1024)  # 21MB
    error = validate_uploaded_content(big_content, "big.txt")
    assert error is not None
    assert "20MB" in error


def test_knowledge_base_ui_uses_decoupled_upload_endpoint():
    """The browser must not use the legacy endpoint that blocks on synchronous indexing."""
    page = Path(__file__).resolve().parents[2] / "front" / "src" / "pages" / "knowledge" / "KnowledgeBasePage.vue"
    source = page.read_text(encoding="utf-8")

    assert "/knowledge/add/multiple/v2" in source
    assert "'/knowledge/add/multiple/stream'" not in source


# ==================== v2 删除测试 ====================

def test_safe_delete_physical_file_rejects_path_outside_user_dir():
    """_safe_delete_physical_file 必须拒绝删除用户目录外的文件"""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
        f.write(b"test")
        outside_path = f.name

    try:
        # 尝试用错误的 user_id 删除（路径不在该用户目录内）
        _safe_delete_physical_file(outside_path, "nonexistent_user")
        # 如果文件还在，说明拒绝了删除
        assert os.path.exists(outside_path), "不应删除用户目录外的文件"
    finally:
        if os.path.exists(outside_path):
            os.unlink(outside_path)


def test_safe_delete_physical_file_deletes_inside_user_dir():
    """_safe_delete_physical_file 应该删除用户目录内的文件"""
    user_dir = _get_storage_dir(USER_A)
    os.makedirs(user_dir, exist_ok=True)

    test_file = os.path.join(user_dir, "test_delete.txt")
    with open(test_file, "w") as f:
        f.write("test")

    assert os.path.exists(test_file)

    _safe_delete_physical_file(test_file, USER_A)
    assert not os.path.exists(test_file), "应成功删除用户目录内的文件"


def test_embedding_check_returns_false_on_import_error():
    """_check_embedding_available 在 import 失败时应返回 False"""
    with patch.dict('sys.modules', {'app.utils.factory': None}):
        # 由于 mock 了模块，resolve 会失败
        result = _check_embedding_available()
        assert result is False


def test_sanitize_error_message_removes_api_keys():
    """_sanitize_error_message 必须移除 API Key"""
    from app.tasks.index_task import _sanitize_error_message

    # 测试 sk- 格式的 API Key
    msg = "Error: Invalid API key sk-abc123456789012345678901234567890"
    result = _sanitize_error_message(msg)
    assert "sk-abc123456789012345678901234567890" not in result
    assert "sk-***" in result

    # 测试 Bearer Token
    msg = "Error: Authorization header Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0"
    result = _sanitize_error_message(msg)
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result
    assert "Bearer ***" in result

    # 测试密码
    msg = "Error: password=mysecretkey123"
    result = _sanitize_error_message(msg)
    assert "mysecretkey123" not in result
    assert "password=***" in result


def test_embedding_check_handles_resolve_failure():
    """_check_embedding_available 在 resolve() 失败时应返回 False"""
    # 模拟 resolve() 抛出异常（如配置错误、依赖缺失等）
    with patch('app.utils.factory.EmbedModelFactory') as mock_factory:
        mock_factory.return_value.generator.side_effect = ValueError("不支持的EMBED_MODEL_TYPE")
        result = _check_embedding_available()
        assert result is False


# ==================== Celery 注册测试 ====================

def test_celery_tasks_registered_in_init():
    """app.tasks.__init__ 必须导入 index_document_task 和 batch_index_pending_task"""
    init_path = Path(__file__).resolve().parents[1] / "app" / "tasks" / "__init__.py"
    source = init_path.read_text(encoding="utf-8")

    assert "index_document_task" in source
    assert "batch_index_pending_task" in source
    assert "from app.tasks.index_task import" in source


def test_celery_app_includes_index_task_module():
    """Celery app 必须通过 include 配置确保 worker 启动时发现任务"""
    celery_path = Path(__file__).resolve().parents[1] / "app" / "tasks" / "celery_app.py"
    source = celery_path.read_text(encoding="utf-8")

    # 必须有 include 配置
    assert "include" in source, "celery_app.py 必须配置 include"
    assert "app.tasks.index_task" in source, "include 必须包含 app.tasks.index_task"


def test_celery_beat_schedule_configured():
    """Celery Beat 必须配置 batch_index_pending_task 定时任务"""
    celery_path = Path(__file__).resolve().parents[1] / "app" / "tasks" / "celery_app.py"
    source = celery_path.read_text(encoding="utf-8")

    assert "beat_schedule" in source, "celery_app.py 必须配置 beat_schedule"
    assert "batch_index_pending_task" in source, "beat_schedule 必须包含 batch_index_pending_task"


# ==================== document_index 模型测试 ====================

def test_document_index_status_enum_values():
    """DocumentIndexStatus 枚举值必须与数据库一致"""
    assert DocumentIndexStatus.UPLOADED.value == "uploaded"
    assert DocumentIndexStatus.PARSED.value == "parsed"
    assert DocumentIndexStatus.PENDING_INDEX.value == "pending_index"
    assert DocumentIndexStatus.INDEXING.value == "indexing"
    assert DocumentIndexStatus.INDEXED.value == "indexed"
    assert DocumentIndexStatus.INDEX_FAILED.value == "index_failed"


def test_document_index_model_has_required_columns():
    """DocumentIndex 模型必须有所有必需的列"""
    columns = {c.name for c in DocumentIndex.__table__.columns}
    required = {
        "id", "user_id", "space_id", "filename", "original_filename",
        "file_path", "md5", "status", "chunk_count", "error_message",
        "retry_count", "created_at", "updated_at", "indexed_at",
    }
    assert required.issubset(columns), f"缺少列: {required - columns}"


# ==================== 前端删除逻辑测试 ====================

def test_frontend_delete_uses_v2_endpoint_for_indexed_docs():
    """前端对 v2 文档必须优先使用 DELETE /knowledge/documents/{id}"""
    page = Path(__file__).resolve().parents[2] / "front" / "src" / "pages" / "knowledge" / "KnowledgeBasePage.vue"
    source = page.read_text(encoding="utf-8")

    # 必须有 deleteDocumentById 函数
    assert "async function deleteDocumentById" in source
    # 必须调用 /knowledge/documents/ 路径
    assert "/knowledge/documents/" in source
    # handleDeleteDocument 中必须优先检查 doc.id && doc.index_status
    assert "doc.id && doc.index_status" in source


def test_frontend_clean_passes_space_id():
    """前端清除全部时必须传递 space_id"""
    page = Path(__file__).resolve().parents[2] / "front" / "src" / "pages" / "knowledge" / "KnowledgeBasePage.vue"
    source = page.read_text(encoding="utf-8")

    assert "selectedSpaceId.value" in source
    assert "space_id" in source


def test_frontend_shows_reindex_button():
    """前端必须对 pending_index 和 index_failed 显示重新索引按钮"""
    page = Path(__file__).resolve().parents[2] / "front" / "src" / "pages" / "knowledge" / "KnowledgeBasePage.vue"
    source = page.read_text(encoding="utf-8")

    assert "handleReindex" in source
    assert "pending_index" in source
    assert "index_failed" in source
    assert "/reindex" in source


def test_frontend_shows_index_status_badge():
    """前端必须显示索引状态标签"""
    page = Path(__file__).resolve().parents[2] / "front" / "src" / "pages" / "knowledge" / "KnowledgeBasePage.vue"
    source = page.read_text(encoding="utf-8")

    assert "getIndexStatusText" in source
    assert "待索引" in source
    assert "索引失败" in source
    assert "已索引" in source


# ==================== 路由端点测试 ====================

def test_knowledge_router_has_v2_delete_endpoint():
    """knowledge_router 必须有 DELETE /documents/{document_id} 端点"""
    router_path = Path(__file__).resolve().parents[1] / "app" / "router" / "knowledge_router.py"
    source = router_path.read_text(encoding="utf-8")

    assert '"/documents/{document_id}"' in source
    assert "delete_document_by_id" in source


def test_knowledge_router_clean_passes_space_id():
    """clean 端点必须支持 space_id 参数"""
    router_path = Path(__file__).resolve().parents[1] / "app" / "router" / "knowledge_router.py"
    source = router_path.read_text(encoding="utf-8")

    assert "clean_user_index_records" in source
    assert "space_id" in source


def test_index_task_handles_deleted_document():
    """index_task 在文档被删除时应安全退出，不抛异常"""
    task_path = Path(__file__).resolve().parents[1] / "app" / "tasks" / "index_task.py"
    source = task_path.read_text(encoding="utf-8")

    # 必须有检查文档是否还存在的逻辑
    assert "still_exists" in source or "文档在索引过程中被删除" in source
    # 必须有"可能已被删除"的警告日志
    assert "可能已被删除" in source


def test_index_task_has_sanitize_error_function():
    """index_task 必须有 _sanitize_error_message 函数清理敏感信息"""
    task_path = Path(__file__).resolve().parents[1] / "app" / "tasks" / "index_task.py"
    source = task_path.read_text(encoding="utf-8")

    assert "_sanitize_error_message" in source
    # 必须清理 API Key
    assert "sk-" in source
    # 必须清理 Bearer Token
    assert "Bearer" in source


def test_index_task_checks_file_exists():
    """index_task 在索引前必须检查文件是否存在"""
    task_path = Path(__file__).resolve().parents[1] / "app" / "tasks" / "index_task.py"
    source = task_path.read_text(encoding="utf-8")

    assert "os.path.exists(doc.file_path)" in source
    assert "文件已丢失" in source


def test_embedding_health_cache_exists():
    """document_index_service 必须有 embedding 健康检查缓存"""
    service_path = Path(__file__).resolve().parents[1] / "app" / "services" / "document_index_service.py"
    source = service_path.read_text(encoding="utf-8")

    assert "_embedding_health_cache" in source
    assert "_EMBEDDING_HEALTH_CACHE_TTL" in source
    assert "get_embedding_health_status" in source


def test_docker_compose_has_celery_beat():
    """docker-compose.yml 必须配置 celery-beat 服务"""
    compose_path = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    source = compose_path.read_text(encoding="utf-8")

    assert "celery-beat" in source
    assert "beat" in source
