"""Shared fixtures for DB-backed tests.

Per REQUIREMENTS.md's testing-approach decision: integration tests run against a
real Postgres container (testcontainers), not SQLite — JSONB/numeric round-tripping
and role-based privilege behavior are Postgres-specific and wouldn't be exercised by
a lighter substitute.

Requires actual Docker daemon access. In this environment that means running pytest
wrapped in `sg docker -c "..."`, not directly — see AI_USAGE_LOG.md.

Session-scoped fixtures here are lazy: a test module that never requests db_engines
(e.g. test_hashing.py, test_health.py) never triggers the container to start, so
those stay fast.
"""

import os
import subprocess
import sys
from collections.abc import AsyncGenerator, Generator
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.community.postgres import PostgresContainer

PROJECT_ROOT = Path(__file__).resolve().parent.parent

PG_USER = "audit"
PG_PASSWORD = "audit"
PG_DB = "audit_log"


@dataclass
class DbEngines:
    admin: AsyncEngine
    app: AsyncEngine
    maintenance: AsyncEngine


@pytest.fixture(scope="session")
def postgres_admin_url() -> Generator[str]:
    with PostgresContainer(
        "postgres:16-alpine",
        username=PG_USER,
        password=PG_PASSWORD,
        dbname=PG_DB,
        driver="asyncpg",
    ) as container:
        admin_url = container.get_connection_url()

        # Run migrations as a subprocess with ADMIN_DATABASE_URL overridden, rather
        # than manipulating this process's already-imported Settings singleton —
        # core/db.py creates engines from settings.database_url at import time, and
        # by the time this fixture runs, other test modules may have already
        # imported audit_log_service.main (and therefore core/db.py) using the
        # local-dev default. A subprocess gets a fresh interpreter with no such
        # baked-in state.
        result = subprocess.run(
            ["uv", "run", "alembic", "upgrade", "head"],
            cwd=PROJECT_ROOT,
            env={**os.environ, "ADMIN_DATABASE_URL": admin_url},
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(result.stdout, file=sys.stderr)
            print(result.stderr, file=sys.stderr)
            raise RuntimeError("Alembic migration failed against the test container")

        yield admin_url


@pytest_asyncio.fixture(scope="session")
async def db_engines(postgres_admin_url: str) -> AsyncGenerator[DbEngines]:
    base = postgres_admin_url.rsplit("@", 1)[1]  # "host:port/db"
    engines = DbEngines(
        admin=create_async_engine(postgres_admin_url),
        app=create_async_engine(f"postgresql+asyncpg://app_role:app_role_dev_pw@{base}"),
        maintenance=create_async_engine(
            f"postgresql+asyncpg://maintenance_role:maintenance_role_dev_pw@{base}"
        ),
    )
    yield engines
    await engines.admin.dispose()
    await engines.app.dispose()
    await engines.maintenance.dispose()


@pytest_asyncio.fixture
async def clean_db(db_engines: DbEngines) -> AsyncGenerator[None]:
    """Truncates audit_events before each test — requires the admin connection,
    since neither app_role nor maintenance_role has DELETE/TRUNCATE (by design).
    sequence_number is app-computed, not a DB sequence object, so there's nothing
    else to reset.
    """
    async with db_engines.admin.begin() as conn:
        await conn.execute(text("TRUNCATE audit_events"))
    yield


@pytest_asyncio.fixture
async def app_session(db_engines: DbEngines, clean_db: None) -> AsyncGenerator[AsyncSession]:
    session_local = async_sessionmaker(db_engines.app, expire_on_commit=False)
    async with session_local() as session:
        yield session


@pytest_asyncio.fixture
async def maintenance_session(
    db_engines: DbEngines, clean_db: None
) -> AsyncGenerator[AsyncSession]:
    session_local = async_sessionmaker(db_engines.maintenance, expire_on_commit=False)
    async with session_local() as session:
        yield session


@pytest_asyncio.fixture
async def admin_session(db_engines: DbEngines, clean_db: None) -> AsyncGenerator[AsyncSession]:
    """For simulating direct-datastore tampering that bypasses both app_role and
    maintenance_role (req 9's validation scenario), and for reads that don't care
    about privilege scoping.
    """
    session_local = async_sessionmaker(db_engines.admin, expire_on_commit=False)
    async with session_local() as session:
        yield session
