"""v1/SSE 发布提交结果不确定的行为回归。

这些用例只包住 ``AsyncSessionLocal`` 的 session 边界：不 patch 发布、领取、
仓库或确认函数。提交拦截通过当前事务中已经 flush 的 superseded 清理任务
识别发布提交，避免依赖 session 序号。
"""

import hashlib
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.document_index import DocumentIndexStatus
from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
from app.repositories.document_index_repository import DocumentIndexRepository

from tests import test_closure_rework as closure  # noqa: E402

_patch_real_sse_event = closure._patch_real_sse_event
_seed_doc = closure._seed_doc
_sse_payload = closure._sse_payload
env = closure.env


async def _seed_v1_doc(env, user_id: str, content: str):
    """创建与真实上传内容匹配的 v1 document_index 行。"""
    doc_id, _ = await _seed_doc(env, user_id=user_id)
    md5 = hashlib.md5(content.encode("utf-8")).hexdigest()
    async with env.factory() as session:
        row = await DocumentIndexRepository(session).get_by_id(doc_id, user_id)
        row.md5 = md5
        await session.commit()
    return doc_id, md5


async def _read_publication_state(env, doc_id: str, user_id: str):
    from app.models.cleanup_task import DocumentCleanupTask

    async with env.factory() as session:
        row = await DocumentIndexRepository(session).get_by_id(doc_id, user_id)
        executions = list((await session.execute(
            select(DocumentIndexExecution).where(
                DocumentIndexExecution.document_id == doc_id,
                DocumentIndexExecution.user_id == user_id,
            )
        )).scalars().all())
        tasks = list((await session.execute(
            select(DocumentCleanupTask).where(
                DocumentCleanupTask.document_id == doc_id,
                DocumentCleanupTask.user_id == user_id,
                DocumentCleanupTask.reason == "superseded_generation",
            )
        )).scalars().all())
        return row, executions, tasks


class _PublicationCommitProbe:
    """识别并拦截本次发布 commit，同时记录原 session 释放顺序。"""

    def __init__(self, inner, *, stage, doc_id, generation, persist):
        self._inner = inner
        self._stage = stage
        self._doc_id = doc_id
        self._generation = generation
        self._persist = persist
        self.rollback_called = False
        self.close_called = False
        self.exited = False

    @property
    def released(self):
        return self.rollback_called and (self.close_called or self.exited)

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def __aenter__(self):
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *exc):
        try:
            return await self._inner.__aexit__(*exc)
        finally:
            self.exited = True

    async def execute(self, statement, *args, **kwargs):
        """确认读取必须发生在发布 session 已释放之后。"""
        publication = self._stage.get("publication_session")
        if publication is not None and publication is not self:
            sql = str(statement).lower().lstrip()
            if sql.startswith("select") and (
                "document_index" in sql
                or "document_cleanup_tasks" in sql
            ):
                self._stage["confirmation_read"] = True
                self._stage["released_before_confirmation"] = bool(
                    publication and publication.released
                )
                self._stage["rollback_before_confirmation"] = bool(
                    publication and publication.rollback_called
                )
                self._stage["close_before_confirmation"] = bool(
                    publication and publication.close_called
                )
        return await self._inner.execute(statement, *args, **kwargs)

    async def rollback(self):
        self.rollback_called = True
        return await self._inner.rollback()

    async def close(self):
        self.close_called = True
        return await self._inner.close()

    async def commit(self):
        from app.models.cleanup_task import DocumentCleanupTask

        # create_task() flushes the row before the enclosing publication commit.
        pending = (await self._inner.execute(
            select(DocumentCleanupTask.id).where(
                DocumentCleanupTask.document_id == self._doc_id,
                DocumentCleanupTask.generation == self._generation,
                DocumentCleanupTask.reason == "superseded_generation",
            )
        )).scalar_one_or_none()
        if pending is not None and not self._stage["publication_commit_hit"]:
            self._stage["publication_commit_hit"] = True
            self._stage["publication_session"] = self
            if self._persist:
                await self._inner.commit()
            raise RuntimeError("connection lost around publication commit")
        return await self._inner.commit()


class _PublicationCommitFactory:
    def __init__(self, original, *, stage, doc_id, generation, persist):
        self._original = original
        self._stage = stage
        self._doc_id = doc_id
        self._generation = generation
        self._persist = persist

    def __call__(self):
        return _PublicationCommitProbe(
            self._original(),
            stage=self._stage,
            doc_id=self._doc_id,
            generation=self._generation,
            persist=self._persist,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["v1", "sse"])
@pytest.mark.parametrize("commit_persisted", [False, True])
async def test_v1_and_sse_publish_commit_recovery(
    env, entrypoint, commit_persisted
):
    """发布 commit 前后连接丢失时，v1 与 SSE 共享同一确认契约。"""
    import app.db.db_config as db_cfg
    from app.router.knowledge_service import KnowledgeService, ProcessingState

    user_id = f"u-publication-{entrypoint}-{int(commit_persisted)}"
    content = "发布提交恢复"
    doc_id, md5 = await _seed_v1_doc(env, user_id, content)
    stage = {
        "publication_commit_hit": False,
        "publication_session": None,
        "confirmation_read": False,
        "released_before_confirmation": False,
        "rollback_before_confirmation": False,
        "close_before_confirmation": False,
    }

    patch = pytest.MonkeyPatch()
    original_factory = db_cfg.AsyncSessionLocal
    patch.setattr(
        db_cfg,
        "AsyncSessionLocal",
        _PublicationCommitFactory(
            original_factory,
            stage=stage,
            doc_id=doc_id,
            generation=1,
            persist=commit_persisted,
        ),
    )

    if entrypoint == "v1":
        async def fake_get_document(
            files=None,
            user_id=None,
            progress_callback=None,
            space_id="",
            generation=None,
            document_id=None,
        ):
            upload = files[0]
            raw = await upload.read()
            await upload.seek(0)
            env.vs.add_documents(
                [NS(page_content=raw.decode("utf-8"), metadata={
                    "user_id": user_id,
                    "md5": hashlib.md5(raw).hexdigest(),
                    "document_id": document_id,
                    "generation": int(generation),
                })],
                ids=[f"{user_id}:{document_id}:g{generation}:0"],
            )

        patch.setattr(env.store, "get_document", fake_get_document, raising=False)

        class Upload:
            filename = "publication-recovery.txt"
            size = len(content.encode("utf-8"))

            async def read(self, size=-1):
                return content.encode("utf-8")

            async def seek(self, position):
                return None

        service = KnowledgeService()
        caught = None
        result = None
        try:
            if commit_persisted:
                result = await service.handle_add_vector_single(
                    Upload(), user_id, space_id=""
                )
            else:
                with pytest.raises(HTTPException) as error:
                    await service.handle_add_vector_single(
                        Upload(), user_id, space_id=""
                    )
                caught = error.value
        finally:
            patch.undo()

        assert stage["publication_commit_hit"], \
            "每个 v1 参数都必须拦截到真实发布 commit"
        assert stage["confirmation_read"], \
            "提交异常后必须进入独立持久事实确认"
        assert stage["released_before_confirmation"], \
            "独立确认读取前原发布 session 必须已释放"
        assert stage["rollback_before_confirmation"], \
            "确认前必须先 rollback 原发布 session"
        assert stage["close_before_confirmation"], \
            "确认前必须 close 原发布 session"

        row, executions, tasks = await _read_publication_state(env, doc_id, user_id)
        assert executions, "v1 必须登记执行身份"
        execution = next(e for e in executions if e.generation == 1)

        if commit_persisted:
            assert result == Upload.filename
            assert row is not None
            assert row.status == DocumentIndexStatus.INDEXED
            assert row.active_generation == 1
            assert execution.status == ExecutionStatus.CLOSED
            assert any(t.generation == 1 for t in tasks), \
                "发布成功必须有 superseded 清理任务"
            assert f"{user_id}:{doc_id}:g1:0" in env.vs.docs, \
                "确认成功不得删除已发布向量"
        else:
            assert caught is not None
            assert caught.status_code == 503
            assert "publish_commit_uncertain" in str(caught.detail)
            assert execution.status == ExecutionStatus.UNKNOWN

    else:
        patch.setattr(
            db_cfg,
            "AsyncSessionLocal",
            _PublicationCommitFactory(
                original_factory,
                stage=stage,
                doc_id=doc_id,
                generation=1,
                persist=commit_persisted,
            ),
        )
        ks = _patch_real_sse_event(patch)
        from queue import Queue

        # app.rag.task_queue is replaced by a MagicMock in the shared conftest;
        # use the standard queue so get()/task_done() have real blocking semantics.
        queue = Queue()
        queue.put(NS(
            success=True,
            file_index=1,
            filename="publication-recovery.txt",
            documents=[NS(page_content=content, metadata={})],
            md5=md5,
            chunk_count=1,
        ))
        state = ProcessingState(total_files=1, total_valid=1)
        service = ks.KnowledgeService()
        try:
            events = [event async for event in service._process_slice_results(
                queue, 1, env.store, state, user_id
            )]
        finally:
            patch.undo()

        assert stage["publication_commit_hit"], \
            "每个 SSE 参数都必须拦截到真实发布 commit"
        assert stage["confirmation_read"], \
            "提交异常后必须进入独立持久事实确认"
        assert stage["released_before_confirmation"], \
            "独立确认读取前原发布 session 必须已释放"
        assert stage["rollback_before_confirmation"], \
            "确认前必须先 rollback 原发布 session"
        assert stage["close_before_confirmation"], \
            "确认前必须 close 原发布 session"

        payloads = [_sse_payload(event) for event in events]
        steps = [payload.get("step") for payload in payloads]
        row, executions, tasks = await _read_publication_state(env, doc_id, user_id)
        assert executions, "SSE 必须登记执行身份"
        execution = next(e for e in executions if e.generation == 1)

        if commit_persisted:
            assert "completed" in steps
            assert row is not None
            assert row.status == DocumentIndexStatus.INDEXED
            assert row.active_generation == 1
            assert execution.status == ExecutionStatus.CLOSED
            assert any(t.generation == 1 for t in tasks)
            assert f"{user_id}:{doc_id}:g1:0" in env.vs.docs
        else:
            assert "completed" not in steps
            assert any(payload.get("step") == "writing"
                       and payload.get("error_message")
                       for payload in payloads), \
                "SSE 提交不确定必须发送 write_error"
            assert execution.status == ExecutionStatus.UNKNOWN
