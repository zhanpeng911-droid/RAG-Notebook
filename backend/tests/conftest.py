"""
测试配置 —— 在导入业务模块前 mock 掉重依赖（LLM、ChromaDB 等），
避免测试启动时初始化真实的模型和向量库。
"""
import sys
from unittest.mock import MagicMock

# ==================== Mock 重依赖 ====================
# 在任何业务模块导入之前，注入 mock 模块

# Mock langchain 相关模块（避免加载真实模型）
for mod_name in [
    "langchain",
    "langchain.agents",
    "langchain.agents.middleware",
    "langgraph",
    "langgraph.runtime",
    "langchain_chroma",
    "langchain_core",
    "langchain_core.documents",
    "langchain_core.messages",
    "langchain_core.embeddings",
    "langchain_core.language_models",
    "langchain_core.tools",
    "langchain_core.prompts",
    "langchain_openai",
    "langchain_community",
    "langchain_community.chat_models",
    "langchain_community.chat_models.tongyi",
    "langchain_community.document_loaders",
    "langchain_classic",
    "langchain_classic.agents",
    "langchain_anthropic",
]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

# Mock ChromaDB
for mod_name in [
    "chromadb",
    "chromadb.config",
]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

# Mock dashscope（阿里云 SDK）
for mod_name in [
    "dashscope",
    "dashscope.multimodal",
]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

# Mock app.utils.config 中的配置
_mock_config = MagicMock()
_mock_config.chroma_config = {"persist_directory": "/tmp/test_chroma"}
sys.modules.setdefault("app.utils.config", _mock_config)

# Mock app.utils.path_tool
_mock_path = MagicMock()
_mock_path.get_abstract_path = lambda x: x
sys.modules.setdefault("app.utils.path_tool", _mock_path)

# Mock app.utils.prompt_loader
_mock_prompt = MagicMock()
_mock_prompt.load_prompt = MagicMock(return_value="test prompt")
sys.modules.setdefault("app.utils.prompt_loader", _mock_prompt)

# Mock app.cache.llm_cache
_mock_cache = MagicMock()
sys.modules.setdefault("app.cache.llm_cache", _mock_cache)

# Mock app.rag.rag_service
_mock_rag = MagicMock()
sys.modules.setdefault("app.rag.rag_service", _mock_rag)

# Mock app.rag.vector_store
_mock_vs = MagicMock()
sys.modules.setdefault("app.rag.vector_store", _mock_vs)

# Mock app.utils.file_handler
_mock_file_handler = MagicMock()
sys.modules.setdefault("app.utils.file_handler", _mock_file_handler)

# Mock app.rag.sse_models
_mock_sse = MagicMock()
sys.modules.setdefault("app.rag.sse_models", _mock_sse)

# Mock app.rag.task_queue
_mock_tq = MagicMock()
sys.modules.setdefault("app.rag.task_queue", _mock_tq)

# Mock app.utils.image_extractor
_mock_ie = MagicMock()
sys.modules.setdefault("app.utils.image_extractor", _mock_ie)

# Mock app.services.knowledge_file_validator
_mock_kfv = MagicMock()
_mock_kfv.MAX_FILE_SIZE = 20 * 1024 * 1024
_mock_kfv.safe_filename = lambda f: f.filename if hasattr(f, 'filename') else str(f)
_mock_kfv.validate_file_type = MagicMock(return_value=None)
_mock_kfv.validate_total_size = MagicMock(return_value=None)
sys.modules.setdefault("app.services.knowledge_file_validator", _mock_kfv)
