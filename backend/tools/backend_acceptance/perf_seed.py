"""Seed the isolated enterprise-v1 load profile.

100 accounts x 1000 notes = 100000 notes, 3000 graph edges per account, plus
one queryable document/analysis source per account. Only generated test data.
Run inside the acceptance controller; never against a business database.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jose import jwt
from sqlalchemy import func, insert, select

from app.db.db_config import AsyncSessionLocal
from app.models.document_index import DocumentIndex, DocumentIndexStatus
from app.models.knowledge_insight import (
    KnowledgeArtifact, KnowledgeSourceVersion, SourceAnchor, SourceType,
)
from app.models.note import Note
from app.models.note_graph import NoteManualRelation

SECRET = os.environ["SECRET_KEY"]
RUN = os.environ["NB_ACCEPTANCE_RUN_ID"]
OUTPUT_ROOT = Path(os.environ.get("NB_PERF_DATA", f"/app/data/perf/{RUN}"))


def account_id(index: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{RUN}:account:{index}"))


def token_for(user_id: str, index: int) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({
        "user_id": user_id,
        "username": f"perf-{index}",
        "exp": now + timedelta(hours=12),
        "iat": now,
        "jti": str(uuid.uuid4()),
    }, SECRET, algorithm="HS256")


def note_id(account: str, index: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{account}:note:{index}"))


def _content(account: str, index: int, links: list[tuple[str, str]]) -> str:
    body = (
        f"Performance note {index} for account {account[:8]}.\n\n"
        "Retrieval evidence must remain owner-scoped. "
        "This generated body is intentionally stable for correctness checks. "
    )
    for target, title in links:
        body += f"Related [[note:{target}|{title}]]. "
    return body


async def _count_notes(session) -> int:
    return int((await session.execute(
        select(func.count(Note.id)).where(
            Note.user_id == account_id(0))
    )).scalar() or 0)


async def seed(accounts: int, notes_per_account: int, links_per_account: int,
               docs_per_account: int) -> dict:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    account_rows = []
    for index in range(accounts):
        uid = account_id(index)
        account_rows.append({"index": index, "user_id": uid,
                             "token": token_for(uid, index)})
    (OUTPUT_ROOT / "accounts.json").write_text(
        json.dumps(account_rows, indent=2), encoding="utf-8")

    started = time.monotonic()
    async with AsyncSessionLocal() as session:
        existing = await _count_notes(session)
    if existing >= notes_per_account:
        return {"status": "already_seeded", "existing_notes": existing,
                "accounts_file": str(OUTPUT_ROOT / "accounts.json")}

    rng = random.Random(20261027)
    note_ids: dict[str, list[str]] = {}
    file_meta = []
    for index, account in enumerate(account_rows):
        uid = account["user_id"]
        ids = [note_id(uid, n) for n in range(notes_per_account)]
        note_ids[uid] = ids
        # Reserve a deterministic sample for detail/local-graph reads.
        for offset in range(0, notes_per_account, 1000):
            batch = []
            for n in range(offset, min(offset + 1000, notes_per_account)):
                targets = []
                if n and n % 7 == 0:
                    target = ids[(n * 13) % notes_per_account]
                    if target != ids[n]:
                        targets.append((target, f"Performance note {(n * 13) % notes_per_account}"))
                tags = [["perf"], ["perf", "read"], ["perf", "graph"], []][n % 4]
                title = f"Performance note {n}"
                if n % 97 == 0:
                    title = "Duplicate performance title"
                batch.append({
                    "id": ids[n], "user_id": uid, "title": title,
                    "content": _content(uid, n, targets),
                    "tags": tags, "category": ["work", "study", "life", "project"][n % 4],
                })
            await session.execute(insert(Note), batch)
            await session.commit()

        # ~3000 unique manual relations per account.
        pairs = set()
        while len(pairs) < links_per_account:
            source = rng.randrange(notes_per_account)
            target = rng.randrange(notes_per_account)
            if source != target:
                pairs.add((min(source, target), max(source, target)))
        relation_rows = [{
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL,
                                 f"{uid}:relation:{a}:{b}")),
            "owner_user_id": uid,
            "source_note_id": ids[a], "target_note_id": ids[b],
            "relation_type": "manual",
            "reason": "generated performance edge",
            "created_by": uid,
        } for a, b in sorted(pairs)]
        for offset in range(0, len(relation_rows), 1000):
            await session.execute(insert(NoteManualRelation),
                                  relation_rows[offset:offset + 1000])
            await session.commit()

        for doc_index in range(docs_per_account):
            doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
                                    f"{uid}:document:{doc_index}"))
            version_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
                                        f"{uid}:version:{doc_index}"))
            path = OUTPUT_ROOT / f"doc-{uid[:8]}-{doc_index}.txt"
            path.write_text(
                "Performance source document. " * 200, encoding="utf-8")
            session.add(DocumentIndex(
                id=doc_id, user_id=uid, filename=path.name,
                original_filename=path.name, file_path=str(path),
                file_size=path.stat().st_size, file_type="txt", md5="0" * 32,
                status=DocumentIndexStatus.INDEXED, generation=1,
                active_generation=1, chunk_count=3,
            ))
            session.add(KnowledgeSourceVersion(
                id=version_id, owner_user_id=uid,
                source_type=SourceType.DOCUMENT, source_id=doc_id,
                generation=1, status="active", chunk_count=3))
            for chunk in range(3):
                quote = f"Performance source chunk {chunk}."
                session.add(SourceAnchor(
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL,
                                      f"{version_id}:anchor:{chunk}")),
                    source_version_id=version_id, owner_user_id=uid,
                    source_type=SourceType.DOCUMENT, source_id=doc_id,
                    generation=1, chunk_index=chunk,
                    quote_text=quote,
                    quote_hash=hashlib.sha256(quote.encode()).hexdigest(),
                    locator_kind="chunk",
                ))
            payload = {
                "schema_version": "1.0", "trust_status": "anchor_verified",
                "overview": {"text": "Generated performance summary.",
                             "anchor_ids": []},
                "key_points": [], "sections": [], "terms": [],
                "open_questions": [], "relations": [],
                "meta": {"prompt_version": "perf", "anchor_count": 3},
            }
            session.add(KnowledgeArtifact(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL,
                                  f"{version_id}:summary")),
                owner_user_id=uid, source_version_id=version_id,
                source_type=SourceType.DOCUMENT, source_id=doc_id,
                generation=1, artifact_type="summary",
                schema_version="1.0", payload_json=payload, status="active",
            ))
            file_meta.append({"account": uid, "document_id": doc_id})
        await session.commit()
        print(f"seeded account {index + 1}/{accounts}", flush=True)

    elapsed = time.monotonic() - started
    result = {
        "status": "seeded", "accounts": accounts,
        "notes": accounts * notes_per_account,
        "relations": accounts * links_per_account,
        "documents": len(file_meta), "elapsed_seconds": round(elapsed, 2),
        "accounts_file": str(OUTPUT_ROOT / "accounts.json"),
        "documents_file": str(OUTPUT_ROOT / "documents.json"),
    }
    (OUTPUT_ROOT / "documents.json").write_text(
        json.dumps(file_meta, indent=2), encoding="utf-8")
    (OUTPUT_ROOT / "seed-summary.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8")
    return result


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--accounts", type=int, default=100)
    parser.add_argument("--notes-per-account", type=int, default=1000)
    parser.add_argument("--links-per-account", type=int, default=3000)
    parser.add_argument("--documents-per-account", type=int, default=1)
    args = parser.parse_args()
    result = await seed(args.accounts, args.notes_per_account,
                        args.links_per_account, args.documents_per_account)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
