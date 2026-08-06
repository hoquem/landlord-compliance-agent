"""Database engines and session factories, one per privilege level.

**Three connections, because three callers need different things**, and until
2026-08-06 all of them shared the `postgres` superuser -- which bypasses RLS,
so the policies in ``0002_rls.sql`` protected nothing on any path the product
actually uses.

===================== ============= ===========================================
factory               role          who
===================== ============= ===========================================
``org_session``       ``app_api``   the API. **RLS applies.** Sees only the org
                                    named in the claim it sets per transaction.
``worker_session_``   ``app_worker`` the worker. BYPASSRLS, because it reads
``factory``                         across orgs by design.
``async_session_``    ``postgres``  tests, migrations, ``seed_org.py`` --
``factory``                         everything that must arrange state it is
                                    not a tenant of.
===================== ============= ===========================================

The asymmetry is deliberate and is where the remaining risk sits. The API is
~26 query sites across seven routers, written by whoever adds a feature; the
worker is one file whose whole tenant boundary is a mutation-tested ``org_id``
taken from the claimed job row. Putting the unguarded credential in the
smaller, better-watched place is the trade.

:seealso: supabase/migrations/0006_least_privilege_roles.sql;
    backend/tests/db/test_rls_enforced.py (the proof this is not decorative).
"""

import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def _dsn(variable: str) -> str:
    """Read a connection URL from the environment as an asyncpg SQLAlchemy DSN.

    :param variable: the environment variable to read.
    :raises RuntimeError: if unset -- fail loudly rather than default. A
        missing URL must stop the process, not silently fall back to a
        more-privileged connection, which is the one mistake here that would
        undo the whole point of separate roles.
    :returns: a ``postgresql+asyncpg://`` connection string.
    """
    url = os.environ.get(variable)
    if not url:
        raise RuntimeError(
            f"{variable} is not set. Start the local Supabase stack "
            "(`supabase start` from the repo root) and run with "
            f"`uv run --env-file ../.env ...`, or export {variable} yourself. "
            "It must NOT be pointed at the postgres superuser: that bypasses "
            "row-level security and is exactly what the separate roles exist "
            "to stop."
        )
    return url.replace("postgresql://", "postgresql+asyncpg://", 1).replace(
        "postgres://", "postgresql+asyncpg://", 1
    )


# Created at import so a deployment missing any of them fails on startup
# rather than on its first request. See the note in `_dispose_app_engine`
# about pooled connections and event loops -- there are three pools now.
engine: AsyncEngine = create_async_engine(_dsn("DATABASE_URL"))
api_engine: AsyncEngine = create_async_engine(_dsn("API_DATABASE_URL"))
worker_engine: AsyncEngine = create_async_engine(_dsn("WORKER_DATABASE_URL"))

#: Superuser. Tests, migrations and setup scripts only -- never a request.
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

#: The API's RLS-enforced factory. Not used directly: go through
#: :func:`org_session`, which is what supplies the claim policies read.
api_session_factory = async_sessionmaker(api_engine, expire_on_commit=False)

#: The worker's factory. BYPASSRLS by design.
worker_session_factory = async_sessionmaker(worker_engine, expire_on_commit=False)


@asynccontextmanager
async def org_session(user_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """Open a session the database will scope to one org, and prove it took.

    Sets ``request.jwt.claims`` for the transaction, which is exactly what
    ``auth.uid()`` reads (verified against the running stack, not assumed) --
    so every policy in ``0002_rls.sql`` works unchanged. There is no second
    definition of "which org" anywhere in this codebase, which is the point.

    **Transaction-local, deliberately.** ``set_config(..., true)`` dies with
    the transaction, so a pooled connection cannot carry one request's org
    into the next. Session-local would be a cross-tenant leak with a very
    long fuse.

    **The assertion is not defensive noise.** ``set_config(..., true)``
    outside a transaction silently does nothing, and the symptom would be
    every query returning zero rows -- which reads as "no data yet", not as
    "authentication is broken". Checking once here turns the worst failure
    mode available into a loud one.

    **One transaction per session.** Committing inside the block ends the
    transaction and with it the claim; a query afterwards would see nothing.
    Every router opens a session, does its work and commits once, which is
    the shape this assumes.

    :param user_id: the authenticated caller's id, from a verified token.
    :raises RuntimeError: if the claim did not stick.
    :yields: a session scoped to that user's org by the database itself.
    """
    async with api_session_factory() as session:
        await session.execute(
            text("select set_config('request.jwt.claims', :claims, true)"),
            {"claims": json.dumps({"sub": str(user_id)})},
        )
        applied = await session.scalar(
            text("select current_setting('request.jwt.claims', true)")
        )
        if not applied:
            raise RuntimeError(
                "request.jwt.claims did not stick, so every query in this "
                "session would silently return nothing. This happens when "
                "set_config runs outside a transaction."
            )
        yield session
