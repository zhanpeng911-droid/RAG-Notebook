"""
Import smoke tests —— 验证关键模块能真实导入，不是 MagicMock。

目标：
- 确保 conftest.py 没有过度 mock 整个业务模块
- 确保阶段 5A/5B/6A 的拆分在真实 import 层面可验证
- 不连接真实 ChromaDB / LLM / MySQL
"""
from unittest.mock import MagicMock



def _is_real_module(mod) -> bool:
    """判断模块是否是真实模块（不是 MagicMock）"""
    return not isinstance(mod, MagicMock)


# ==================== factory 模块 ====================

def test_import_factory_real_module():
    """app.utils.factory 应是真实模块，关键导出存在"""
    import app.utils.factory as factory
    assert _is_real_module(factory), "factory 模块被 mock 了，应该是真实模块"
    assert hasattr(factory, "ChatModelFactory")
    assert hasattr(factory, "EmbedModelFactory")
    assert hasattr(factory, "VisionModelFactory")
    assert hasattr(factory, "create_chat_model_from_settings")
    assert hasattr(factory, "create_chat_model_from_config")
    assert hasattr(factory, "_validate_llm_base_url")
    assert hasattr(factory, "_normalize_base_url")
    assert hasattr(factory, "chat_model")
    assert hasattr(factory, "embed_model")
    assert hasattr(factory, "vision_model")


def test_factory_create_chat_model_from_settings_callable():
    """create_chat_model_from_settings 应是可调用函数"""
    from app.utils.factory import create_chat_model_from_settings
    assert callable(create_chat_model_from_settings)


def test_factory_validate_llm_base_url_callable():
    """_validate_llm_base_url 应是可调用函数"""
    from app.utils.factory import _validate_llm_base_url
    assert callable(_validate_llm_base_url)


# ==================== knowledge_service 模块 ====================

def test_import_knowledge_service_real_module():
    """app.router.knowledge_service 应是真实模块，关键导出存在"""
    import app.router.knowledge_service as ks
    assert _is_real_module(ks), "knowledge_service 模块被 mock 了，应该是真实模块"
    assert hasattr(ks, "KnowledgeService")
    assert hasattr(ks, "ProcessingState")
    assert hasattr(ks, "get_knowledge_service")
    assert hasattr(ks, "_sync_slice_file")


def test_knowledge_service_wrappers_exist():
    """KnowledgeService 应有阶段 5A/5B 的 wrapper 方法"""
    from app.router.knowledge_service import KnowledgeService
    svc = KnowledgeService()
    # 5A wrappers
    assert callable(getattr(svc, "_yield_start_event", None))
    assert callable(getattr(svc, "_yield_finish_event", None))
    assert callable(getattr(svc, "_yield_size_error_event", None))
    assert callable(getattr(svc, "_yield_validation_error_event", None))
    # 5B wrappers
    assert callable(getattr(svc, "clean_user_upload", None))
    assert callable(getattr(svc, "handle_clear_user_md5", None))
    assert callable(getattr(svc, "handle_delete_single_md5", None))
    assert callable(getattr(svc, "handle_get_all_md5_records", None))


def test_knowledge_service_has_record_service():
    """KnowledgeService 应持有 record_service 实例"""
    from app.router.knowledge_service import KnowledgeService
    svc = KnowledgeService()
    assert hasattr(svc, "record_service")
    from app.services.knowledge_record_service import KnowledgeRecordService
    assert isinstance(svc.record_service, KnowledgeRecordService)


# ==================== agent 模块 ====================

def test_import_agent_real_module():
    """app.agent.agent 应是真实模块"""
    import app.agent.agent as agent_mod
    assert _is_real_module(agent_mod), "agent 模块被 mock 了，应该是真实模块"
    assert hasattr(agent_mod, "AgentFactory")
    assert hasattr(agent_mod, "get_agent_response")
    assert hasattr(agent_mod, "get_agent_stream_response")


def test_agent_factory_delegates_to_factory():
    """AgentFactory._create_chat_model 应委托给 factory.create_chat_model_from_settings"""
    from app.agent.agent import AgentFactory
    from unittest.mock import patch

    with patch("app.utils.factory.create_chat_model_from_settings") as mock_create:
        mock_create.return_value = object()
        af = AgentFactory(
            default_tools=[],
            default_middleware=[],
            default_system_prompt="x",
        )
        result = af._create_chat_model("custom-model")
        assert result is mock_create.return_value
        mock_create.assert_called_once_with("custom-model")


# ==================== note 模块 ====================

def test_import_note_vector_index_real_module():
    """app.services.note_vector_index 应是真实模块"""
    import app.services.note_vector_index as nvi
    assert _is_real_module(nvi), "note_vector_index 模块被 mock 了，应该是真实模块"
    assert hasattr(nvi, "NoteVectorIndex")


def test_import_note_repository_real_module():
    """app.repositories.note_repository 应是真实模块"""
    import app.repositories.note_repository as nr
    assert _is_real_module(nr), "note_repository 模块被 mock 了，应该是真实模块"
    assert hasattr(nr, "NoteRepository")


# ==================== knowledge 子模块 ====================

def test_import_knowledge_file_validator_real_module():
    """app.services.knowledge_file_validator 应是真实模块"""
    import app.services.knowledge_file_validator as kv
    assert _is_real_module(kv), "knowledge_file_validator 模块被 mock 了，应该是真实模块"
    assert hasattr(kv, "safe_filename")
    assert hasattr(kv, "detect_file_type")
    assert hasattr(kv, "ALLOWED_EXTENSIONS")
    assert hasattr(kv, "MAX_FILE_SIZE")


def test_import_knowledge_sse_events_real_module():
    """app.services.knowledge_sse_events 应是真实模块"""
    import app.services.knowledge_sse_events as se
    assert _is_real_module(se), "knowledge_sse_events 模块被 mock 了，应该是真实模块"
    assert hasattr(se, "build_start_event")
    assert hasattr(se, "build_finish_event")


def test_import_knowledge_record_service_real_module():
    """app.services.knowledge_record_service 应是真实模块"""
    import app.services.knowledge_record_service as kr
    assert _is_real_module(kr), "knowledge_record_service 模块被 mock 了，应该是真实模块"
    assert hasattr(kr, "KnowledgeRecordService")
