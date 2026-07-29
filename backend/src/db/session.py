"""Async SQLAlchemy engine/session factory for the Landlord Compliance Agent backend.

Connects using ``DATABASE_URL`` from the environment, rewritten to the
``postgresql+asyncpg://`` DSN form SQLAlchemy's async engine needs. Fails
loudly (not silently defaulting) when unset, mirroring
``backend/tests/db/conftest.py``'s ``_database_url()`` pattern.

:seealso: backend/src/db/models.py
"""

import os

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine


def _database_url() -> str:
    """Read ``DATABASE_URL`` from the environment as an asyncpg SQLAlchemy DSN.

    :raises RuntimeError: if unset -- fail loudly rather than default.
    :returns: a ``postgresql+asyncpg://`` connection string.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Start the local Supabase stack "
            "(`supabase start` from the repo root) and run with "
            "`uv run --env-file ../.env ...`, or export DATABASE_URL yourself."
        )
    return url.replace("postgresql://", "postgresql+asyncpg://", 1).replace(
        "postgres://", "postgresql+asyncpg://", 1
    )


engine: AsyncEngine = create_async_engine(_database_url())

#: Session factory for request/worker/test code. ``expire_on_commit=False``
#: so attributes remain readable after commit without triggering an
#: implicit re-select outside the transaction.
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
