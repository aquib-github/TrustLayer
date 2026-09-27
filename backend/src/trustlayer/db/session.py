"""
SQLAlchemy async engine and session factory.

Usage::

    from trustlayer.db.session import async_session

    async with async_session() as session:
        result = await session.execute(...)
"""

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from trustlayer.config import settings

# ── Engine ────────────────────────────────────────────────────────────────
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=5,
    max_overflow=10,
)

async_session = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
async_session_factory = async_session


async def get_db_session():
    """FastAPI dependency that yields an async database session."""
    async with async_session() as session:
        yield session
