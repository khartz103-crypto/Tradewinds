"""Async SQLAlchemy engine, session factory, and init helpers."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.database_url, echo=settings.debug)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> "AsyncSession":
    """FastAPI dependency that yields an async database session."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Create all tables (for dev / bootstrapping)."""
    from app.models import Base  # noqa: F401 — registers all models

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
