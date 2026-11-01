"""Separate process fixtures for the isolated acceptance database.

Never load the ordinary tests/conftest.py: it installs fake modules globally.
The Compose controller supplies an isolated, per-run database. Missing or
inconsistent configuration is an error, not a skipped test.
"""
import os
import re
import sys
from pathlib import Path

# Make the backend root importable for tools/backend_acceptance in host mode.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
import pytest_asyncio
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture(autouse=True)
async def _fresh_app_pool():
    """pytest uses a new event loop per test; the app's global pool does too."""
    from app.db.db_config import dispose_stale_connections
    await dispose_stale_connections()
    yield
    await dispose_stale_connections()


@pytest_asyncio.fixture
async def mysql_sessions():
    run_id = os.environ.get("NB_ACCEPTANCE_RUN_ID", "")
    if not re.fullmatch(r"nb-it-[a-z0-9-]+", run_id):
        pytest.fail("NB_ACCEPTANCE_RUN_ID must name an isolated acceptance run")
    expected_database = os.environ.get("NB_ACCEPTANCE_DATABASE") or (
        "nb_" + run_id.replace("-", "_"))
    from app.config.validator import get_settings
    settings = get_settings()
    host = os.environ.get("MYSQL_HOST") or settings.MYSQL_HOST
    port = os.environ.get("MYSQL_PORT") or str(settings.MYSQL_PORT)
    user = os.environ.get("MYSQL_USER") or settings.MYSQL_USER
    if not host or not user:
        pytest.fail("isolated MySQL host/user is missing")
    if not re.fullmatch(r"[a-z0-9_]+", expected_database):
        pytest.fail("unsafe acceptance database name")
    if os.environ.get("MYSQL_DATABASE") != expected_database:
        pytest.fail("MYSQL_DATABASE does not match the isolated run database")
    password = os.environ.get("MYSQL_PASSWORD") or settings.MYSQL_PASSWORD
    if not password:
        pytest.fail("isolated database password is missing")
    url = URL.create(
        "mysql+aiomysql", username=user, password=password,
        host=host, port=int(port), database=expected_database,
    )
    engine = create_async_engine(
        url, pool_pre_ping=True, pool_size=4,
        isolation_level="READ COMMITTED",
        connect_args={"init_command": "SET time_zone = '+00:00'"},
    )
    try:
        async with engine.connect() as connection:
            database = (await connection.execute(text("SELECT DATABASE()"))).scalar_one()
            if database != expected_database:
                pytest.fail("connected database does not match this run")
            # Migration is a prerequisite; never replace it with create_all.
            revision = (await connection.execute(
                text("SELECT version_num FROM alembic_version")
            )).scalar_one()
            ini = os.environ.get("NB_ACCEPTANCE_ALEMBIC_INI") or str(
                Path(__file__).resolve().parents[1] / "alembic.ini")
            head = ScriptDirectory.from_config(Config(ini)).get_current_head()
            if revision != head:
                pytest.fail(f"database migration revision {revision} differs from head {head}")
            print(f"database={database} alembic_revision={revision}")
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
