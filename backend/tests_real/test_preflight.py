"""Strict preflight; run only inside the isolated Compose controller."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest
import redis
from sqlalchemy import text


@pytest.mark.asyncio
async def test_mysql_migration_and_connections(mysql_sessions):
    async with mysql_sessions() as first, mysql_sessions() as second:
        one = (await first.execute(text("SELECT CONNECTION_ID()"))).scalar_one()
        two = (await second.execute(text("SELECT CONNECTION_ID()"))).scalar_one()
        version = (await first.execute(text("SELECT VERSION()"))).scalar_one()
        assert one != two
        print(f"MySQL={version}; independent connection IDs={one},{two}")


def test_redis_target_and_ping():
    for key, expected in {"REDIS_HOST": "redis", "REDIS_PORT": "6379", "REDIS_DB": "3"}.items():
        assert os.environ.get(key) == expected, f"unsafe or missing target: {key}"
    password = os.environ.get("REDIS_PASSWORD")
    assert password, "isolated Redis password missing"
    client = redis.Redis(host="redis", port=6379, db=3, password=password, socket_timeout=5)
    try:
        assert client.ping()
        print(f"Redis={client.info('server')['redis_version']} database=3")
    finally:
        client.close()


def test_worker_and_controller_share_the_isolated_broker():
    from app.tasks.celery_app import celery_app

    assert os.environ["REDIS_HOST"] == "redis"
    assert os.environ["REDIS_DB"] == "3"
    assert "redis" in celery_app.conf.broker_url
    replies = celery_app.control.ping(timeout=8)
    assert replies, "no Celery worker answered on the isolated broker"
    workers = sorted(name for reply in replies for name in reply)
    print(f"Celery broker={celery_app.conf.broker_url.split('@')[-1]} workers={workers}")


def test_real_module_locations():
    root = Path("/app/app").resolve()
    for name in ("app.repositories.document_index_repository", "app.rag.vector_store"):
        spec = importlib.util.find_spec(name)
        assert spec and spec.origin
        path = Path(spec.origin).resolve()
        assert path.is_relative_to(root), f"unexpected module location: {name}: {path}"
        print(f"{name}={path}")


def test_chroma_persistence_from_new_process():
    """The second process reopens actual on-disk Chroma, not a fake store."""
    run_id = os.environ.get("NB_ACCEPTANCE_RUN_ID", "")
    assert run_id.startswith("nb-it-")
    directory = Path("/app/data/chromadb")
    assert directory.is_absolute() and directory.resolve() == directory
    collection = "preflight_" + uuid.uuid4().hex
    script = """
import chromadb, json, sys
client = chromadb.PersistentClient(path=sys.argv[1])
collection = client.get_or_create_collection(sys.argv[2])
if sys.argv[3] == 'write':
    collection.add(ids=['probe'], embeddings=[[1.0, 0.0, 0.0]],
                   documents=['isolated probe'], metadatas=[{'kind': 'preflight'}])
else:
    result = collection.get(ids=['probe'], include=['documents', 'metadatas'])
    print(json.dumps({'ids': result['ids'], 'documents': result['documents'],
                      'metadatas': result['metadatas'], 'version': chromadb.__version__}))
"""
    def invoke(mode):
        return subprocess.run(
            [sys.executable, "-c", script, str(directory), collection, mode],
            check=True, capture_output=True, text=True, timeout=60,
        )
    invoke("write")
    result = json.loads(invoke("read").stdout.strip().splitlines()[-1])
    assert result["ids"] == ["probe"]
    assert result["documents"] == ["isolated probe"]
    assert result["metadatas"] == [{"kind": "preflight"}]
    print(f"Chroma={result['version']} persisted collection={collection} path={directory}")
