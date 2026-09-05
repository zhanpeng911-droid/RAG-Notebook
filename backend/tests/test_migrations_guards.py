"""S07 迁移回退守卫回归 —— 危险 downgrade 必须在 DDL 前拒绝。

006：存在 deleting 行时 downgrade 必须拒绝（第一轮已加，回归保护）。
007：存在 tombstone（deleted_at 非空）时 downgrade 直接 DROP COLUMN
会丢失删除判定依据（S07），必须在删除列前检查并拒绝。
"""
import re
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _source(revision_prefix: str) -> str:
    matches = list(VERSIONS.glob(f"{revision_prefix}*.py"))
    assert len(matches) == 1, f"预期唯一 {revision_prefix} 迁移文件"
    return matches[0].read_text(encoding="utf-8")


def test_006_downgrade_rejects_when_deleting_rows_exist():
    src = _source("006")
    down = src.split("def downgrade()", 1)[1]
    assert "deleting" in down, "006 downgrade 必须先检查 deleting 行"
    assert "RuntimeError" in down or "raise" in down, "006 downgrade 必须拒绝执行"


def test_007_downgrade_rejects_when_tombstones_exist():
    src = _source("007")
    down = src.split("def downgrade()", 1)[1]
    # 必须检查 deleted_at 非空行，且在 drop_column 之前拒绝
    assert "deleted_at" in down, "007 downgrade 缺少 tombstone 检查（S07）"
    assert re.search(r"raise\s+\w+", down), "007 downgrade 必须在 DDL 前拒绝执行"
    assert "drop_column" in down
    # 检查必须发生在 drop 之前
    assert down.index("deleted_at") < down.index("drop_column")


def test_revision_chain_intact():
    src6 = _source("006")
    src7 = _source("007")
    rev6 = re.search(r'revision:\s*str\s*=\s*"([^"]+)"', src6).group(1)
    down7 = re.search(r'down_revision[^=]*=\s*"([^"]+)"', src7).group(1)
    assert down7 == rev6
