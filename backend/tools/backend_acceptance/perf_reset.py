"""Reset the isolated acceptance data to the enterprise-v1 baseline.

Only the dedicated NB_ACCEPTANCE database is touched. Chroma/MD5 directories
are removed only when their absolute path contains the acceptance data root.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text

import app.models  # noqa: F401  (register every table on Base.metadata)
from app.db.db_config import async_engine
from app.models.chat_history import Base
from tools.backend_acceptance import perf_seed


async def reset(accounts: int, notes: int, links: int, documents: int,
                keep_chroma: bool = False) -> dict:
    if not os.environ.get("NB_ACCEPTANCE_RUN_ID"):
        raise SystemExit("refusing reset without NB_ACCEPTANCE_RUN_ID")
    if "nb_nb_it_" not in os.environ.get("MYSQL_DATABASE", ""):
        raise SystemExit("refusing reset outside the isolated acceptance database")
    async with async_engine.begin() as conn:
        await conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(text(f"TRUNCATE TABLE `{table.name}`"))
        await conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    if not keep_chroma:
        data_root = Path(os.environ["NB_ACCEPTANCE_DATA_DIR"]).resolve()
        for name in ("chromadb", "md5_hex_store"):
            target = (data_root / name).resolve()
            if data_root not in target.parents:
                raise SystemExit("refusing to remove a path outside the acceptance data root")
            shutil.rmtree(target, ignore_errors=True)
    return await perf_seed.seed(accounts, notes, links, documents)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--accounts", type=int, default=100)
    parser.add_argument("--notes-per-account", type=int, default=1000)
    parser.add_argument("--links-per-account", type=int, default=3000)
    parser.add_argument("--documents-per-account", type=int, default=1)
    # Keep the live Chroma directory when API/worker processes stay up between
    # performance scenarios; stale note vectors are owner-scoped and all
    # seeded note identities are deterministic.
    parser.add_argument("--keep-chroma", action="store_true")
    args = parser.parse_args()
    print(asyncio.run(reset(args.accounts, args.notes_per_account,
                            args.links_per_account, args.documents_per_account,
                            keep_chroma=args.keep_chroma)))
