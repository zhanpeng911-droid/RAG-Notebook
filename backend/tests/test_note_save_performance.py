import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.models.note import Note
from app.schemas.models import NoteCreate, NoteUpdate
from app.services.note_service import NoteService


class _Nested:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *args):
        return False


class FakeSession:
    def __init__(self):
        self.added = []
        self.commit = AsyncMock()
        self.refresh = AsyncMock()
        self.flush = AsyncMock()

    def add(self, item):
        self.added.append(item)

    def begin_nested(self):
        return _Nested()


def _detached_service():
    service = NoteService()
    service._enqueue_vector_task = AsyncMock()
    service._sync_note_anchors = AsyncMock()
    service._sync_note_links = AsyncMock()
    return service


def test_create_note_uses_one_transaction_without_inline_vector_indexing():
    service = _detached_service()
    service.note_repo = SimpleNamespace(add=AsyncMock())
    service.note_index = Mock()
    db = FakeSession()

    result = asyncio.run(service.create_note(db, "user-1", NoteCreate(title="fast", content="save")))

    assert result.title == "fast"
    assert db.commit.await_count == 1
    assert len(db.added) == 1
    service.note_index.add_note.assert_not_called()
    service.note_index.update_note.assert_not_called()


def test_update_note_uses_one_transaction_without_inline_vector_indexing():
    service = _detached_service()
    note = Note(id="note-1", user_id="user-1", title="old", content="old body")
    service.note_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=note))
    service.note_index = Mock()
    db = FakeSession()

    result = asyncio.run(service.update_note(db, "note-1", "user-1", NoteUpdate(title="new", content="new body")))

    assert result.title == "new"
    assert result.content == "new body"
    assert db.commit.await_count == 1
    service.note_index.update_note.assert_not_called()


def test_delete_note_uses_one_transaction_without_inline_vector_cleanup(monkeypatch):
    import app.services.source_anchor_service as anchor_module
    import app.services.note_link_service as link_module
    monkeypatch.setattr(anchor_module, "invalidate_note", AsyncMock())
    monkeypatch.setattr(link_module, "remove_note_links", AsyncMock())
    service = _detached_service()
    service.note_repo = SimpleNamespace(delete_by_id=AsyncMock(return_value=True))
    service.note_index = Mock()
    db = FakeSession()

    deleted = asyncio.run(service.delete_note(db, "note-1", "user-1"))

    assert deleted is True
    assert db.commit.await_count == 1
    service.note_index.delete_note.assert_not_called()
