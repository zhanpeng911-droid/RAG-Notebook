"""core/audit 直接单测 —— 审计日志写入（成功静默 flush / 失败仅告警）。"""

import pytest


@pytest.mark.asyncio
async def test_write_audit_log_success(monkeypatch):
    from app.core.audit import write_audit_log

    added = []

    class FakeDB:
        async def flush(self):
            return None

        def add(self, obj):
            added.append(obj)

    await write_audit_log(FakeDB(), "u1", "create", "note", "n1",
                          org_id="org-1", detail={"t": 1})
    assert len(added) == 1
    assert added[0].action == "create"
    assert added[0].resource_type == "note"


@pytest.mark.asyncio
async def test_write_audit_log_flush_error_no_raise(monkeypatch):
    from app.core.audit import write_audit_log

    class BoomDB:
        def add(self, obj):
            return None

        async def flush(self):
            raise RuntimeError("写入失败")

    # 失败只记 warning，不抛异常
    await write_audit_log(BoomDB(), "u1", "delete", "space", "s1")
