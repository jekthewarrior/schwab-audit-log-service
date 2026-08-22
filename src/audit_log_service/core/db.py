from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from audit_log_service.core.config import settings

engine = create_async_engine(settings.database_url)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

# maintenance_role: SELECT, INSERT, and column-scoped UPDATE — used only by
# redaction (B1) and retention (B2) services, never by the general read/write path.
maintenance_engine = create_async_engine(settings.maintenance_database_url)
MaintenanceSessionLocal = async_sessionmaker(maintenance_engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncGenerator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def get_maintenance_session() -> AsyncGenerator[AsyncSession]:
    async with MaintenanceSessionLocal() as session:
        yield session


# Shared FastAPI dependency annotations — `session: SessionDep` in a route
# signature, avoids repeating `Depends(get_session)` (and the resulting lint noise)
# everywhere.
SessionDep = Annotated[AsyncSession, Depends(get_session)]
MaintenanceSessionDep = Annotated[AsyncSession, Depends(get_maintenance_session)]
