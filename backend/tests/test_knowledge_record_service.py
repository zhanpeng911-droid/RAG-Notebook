"""
KnowledgeRecordService 测试 —— 验证 MD5 / 去重记录管理逻辑。

测试策略：
- Mock store_factory，验证延迟初始化和调用参数
- 覆盖风险点：
  - 创建实例时不调用 VectorStoreService
  - 调用具体方法时才调用 store_factory
  - clean_user_upload 调用 store.delete_user_documents
  - clear_user_md5 调用 store.delete_user_md5
  - delete_single_md5 调用 store.delete_single_md5 并返回 bool
  - delete_by_filename 调用 store.delete_by_filename 并返回 bool
  - get_md5_info 调用 store.get_md5_info 并返回 dict
  - get_all_md5_records 调用 store.get_all_md5_records 并返回 list
  - delete_documents=True/False 两种参数
  - user_id 不允许丢失
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.knowledge_record_service import KnowledgeRecordService


USER_A = "user-a-0000-0000-000000000001"


def _make_service():
    """创建 KnowledgeRecordService，注入 mock store_factory"""
    store_mock = MagicMock()
    store_mock.delete_user_documents = AsyncMock()
    store_mock.delete_user_md5 = AsyncMock()
    store_mock.delete_single_md5 = AsyncMock(return_value=True)
    store_mock.delete_by_filename = AsyncMock(return_value=True)
    store_mock.get_md5_info = AsyncMock(return_value={"md5": "abc123"})
    store_mock.get_all_md5_records = AsyncMock(return_value=[])

    factory_mock = MagicMock(return_value=store_mock)
    svc = KnowledgeRecordService(store_factory=factory_mock)
    return svc, factory_mock, store_mock


# ==================== 延迟初始化 ====================

def test_init_does_not_call_factory():
    """创建实例时不应该调用 store_factory"""
    factory_mock = MagicMock()
    _svc = KnowledgeRecordService(store_factory=factory_mock)
    factory_mock.assert_not_called()


# ==================== clean_user_upload ====================

@pytest.mark.asyncio
async def test_clean_user_upload_calls_store():
    svc, factory_mock, store_mock = _make_service()
    await svc.clean_user_upload(USER_A)
    factory_mock.assert_called_once()
    store_mock.delete_user_documents.assert_called_once_with(USER_A)


# ==================== clear_user_md5 ====================

@pytest.mark.asyncio
async def test_clear_user_md5_with_documents():
    svc, factory_mock, store_mock = _make_service()
    await svc.clear_user_md5(USER_A, delete_documents=True)
    factory_mock.assert_called_once()
    store_mock.delete_user_md5.assert_called_once_with(USER_A, True)


@pytest.mark.asyncio
async def test_clear_user_md5_without_documents():
    svc, factory_mock, store_mock = _make_service()
    await svc.clear_user_md5(USER_A, delete_documents=False)
    factory_mock.assert_called_once()
    store_mock.delete_user_md5.assert_called_once_with(USER_A, False)


# ==================== delete_single_md5 ====================

@pytest.mark.asyncio
async def test_delete_single_md5_returns_true():
    svc, factory_mock, store_mock = _make_service()
    store_mock.delete_single_md5 = AsyncMock(return_value=True)
    result = await svc.delete_single_md5(USER_A, "abc123", delete_documents=True)
    assert result is True
    factory_mock.assert_called_once()
    store_mock.delete_single_md5.assert_called_once_with(USER_A, "abc123", True)


@pytest.mark.asyncio
async def test_delete_single_md5_returns_false():
    svc, factory_mock, store_mock = _make_service()
    store_mock.delete_single_md5 = AsyncMock(return_value=False)
    result = await svc.delete_single_md5(USER_A, "abc123", delete_documents=False)
    assert result is False
    factory_mock.assert_called_once()
    store_mock.delete_single_md5.assert_called_once_with(USER_A, "abc123", False)


# ==================== delete_by_filename ====================

@pytest.mark.asyncio
async def test_delete_by_filename_returns_true():
    svc, factory_mock, store_mock = _make_service()
    store_mock.delete_by_filename = AsyncMock(return_value=True)
    result = await svc.delete_by_filename(USER_A, "test.pdf", delete_documents=True)
    assert result is True
    factory_mock.assert_called_once()
    store_mock.delete_by_filename.assert_called_once_with(USER_A, "test.pdf", True)


@pytest.mark.asyncio
async def test_delete_by_filename_returns_false():
    svc, factory_mock, store_mock = _make_service()
    store_mock.delete_by_filename = AsyncMock(return_value=False)
    result = await svc.delete_by_filename(USER_A, "test.pdf", delete_documents=False)
    assert result is False
    factory_mock.assert_called_once()
    store_mock.delete_by_filename.assert_called_once_with(USER_A, "test.pdf", False)


# ==================== get_md5_info ====================

@pytest.mark.asyncio
async def test_get_md5_info_returns_dict():
    svc, factory_mock, store_mock = _make_service()
    expected = {"md5": "abc123", "filename": "test.pdf"}
    store_mock.get_md5_info = AsyncMock(return_value=expected)
    result = await svc.get_md5_info(USER_A, "abc123")
    assert result == expected
    factory_mock.assert_called_once()
    store_mock.get_md5_info.assert_called_once_with(USER_A, "abc123")


# ==================== get_all_md5_records ====================

@pytest.mark.asyncio
async def test_get_all_md5_records_returns_list():
    svc, factory_mock, store_mock = _make_service()
    expected = [{"md5": "a"}, {"md5": "b"}]
    store_mock.get_all_md5_records = AsyncMock(return_value=expected)
    result = await svc.get_all_md5_records(USER_A)
    assert result == expected
    factory_mock.assert_called_once()
    store_mock.get_all_md5_records.assert_called_once_with(USER_A)


# ==================== user_id 不丢失 ====================

@pytest.mark.asyncio
async def test_all_methods_pass_user_id():
    """验证所有方法都正确传递 user_id"""
    svc, factory_mock, store_mock = _make_service()
    store_mock.delete_user_documents = AsyncMock()
    store_mock.delete_user_md5 = AsyncMock()
    store_mock.delete_single_md5 = AsyncMock(return_value=True)
    store_mock.delete_by_filename = AsyncMock(return_value=True)
    store_mock.get_md5_info = AsyncMock(return_value={})
    store_mock.get_all_md5_records = AsyncMock(return_value=[])

    await svc.clean_user_upload(USER_A)
    await svc.clear_user_md5(USER_A)
    await svc.delete_single_md5(USER_A, "md5")
    await svc.delete_by_filename(USER_A, "file")
    await svc.get_md5_info(USER_A, "md5")
    await svc.get_all_md5_records(USER_A)

    # 断言每个 store 方法的第一个参数都是 user_id
    store_mock.delete_user_documents.assert_called_with(USER_A)
    store_mock.delete_user_md5.assert_called_with(USER_A, True)
    store_mock.delete_single_md5.assert_called_with(USER_A, "md5", True)
    store_mock.delete_by_filename.assert_called_with(USER_A, "file", True)
    store_mock.get_md5_info.assert_called_with(USER_A, "md5")
    store_mock.get_all_md5_records.assert_called_with(USER_A)
