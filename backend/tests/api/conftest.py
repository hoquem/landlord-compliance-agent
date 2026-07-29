"""Shared fixtures/helpers for the ``backend/tests/api`` suite.

Any router test in this directory needs the same three things: env vars read
loudly (``RuntimeError``, never a skip or a default), a direct RLS-bypassing
DB connection to arrange and assert state with, and a real org + Supabase
Auth user to act as. They live here once rather than being copied into each
router's test module. So far ``test_auth.py`` is the only consumer; the five
router suites of Tasks 13b-17 are what this was extracted for.

Tokens are minted here with PyJWT rather than fetched from Supabase Auth's
password grant: the auth dependency only ever *verifies* a token, so signing
our own is both faster and the only way to produce the negative cases (wrong
secret, wrong audience, already expired) at all. The signing secret is the
real ``SUPABASE_JWT_SECRET`` the local stack runs with and the claim shape
matches a real Supabase access token, so the happy path still exercises the
exact verification the frontend's tokens will hit --
``tests/db/test_rls.py`` covers real end-to-end Supabase-issued tokens.

The whole suite runs against the live local Supabase stack (``supabase
start`` from the repo root)::

    uv run --env-file ../.env pytest tests/api/

Note that only ``org_user`` and ``_dispose_app_engine`` are pytest fixtures,
resolved by argument name. The rest are plain helpers and have to be
imported package-qualified, e.g.::

    from tests.api.conftest import get_whoami, mint_token
"""

import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import asyncpg
import httpx
import jwt
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.auth import CurrentAuth
from src.db.session import engine

# ---------------------------------------------------------------------------
# Environment and direct DB access (loud reads -- no defaults, no skips).
# ---------------------------------------------------------------------------
_RUN_HINT = (
    "Start the local Supabase stack (`supabase start` from the repo root) and run "
    "this test with `uv run --env-file ../.env pytest tests/api/`."
)


def _env(name: str) -> str:
    """Read a required environment variable.

    :param name: the variable to read.
    :raises RuntimeError: if unset or empty -- fail loudly rather than
        default or skip (house rule).
    :returns: the variable's value.
    """
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set. {_RUN_HINT}")
    return value


@asynccontextmanager
async def db() -> AsyncIterator[asyncpg.Connection]:
    """Open a direct (service-role equivalent, RLS-bypassing) connection, and close it.

    :yields: a connected :class:`asyncpg.Connection` to ``DATABASE_URL``.
    """
    conn = await asyncpg.connect(_env("DATABASE_URL"))
    try:
        yield conn
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Test app: a single route that does nothing but expose what the dependency
# resolved. Any test that just needs *some* authenticated request -- rather
# than a particular router's endpoint -- can go through this one.
# ---------------------------------------------------------------------------
app = FastAPI()


@app.get("/whoami")
async def whoami(auth: CurrentAuth) -> dict[str, str]:
    """Echo the resolved identity.

    :param auth: injected by the auth dependency.
    :returns: the resolved user and org ids, as strings.
    """
    return {"user_id": str(auth.user_id), "org_id": str(auth.org_id)}


def mint_token(
    subject: uuid.UUID | str | int | None,
    *,
    secret: str | None = None,
    algorithm: str = "HS256",
    audience: str | None = "authenticated",
    expires_in: int | None = 3600,
) -> str:
    """Sign a token shaped like a Supabase Auth access token.

    :param subject: value for the ``sub`` claim. A ``uuid.UUID`` is
        stringified; anything else is put in the claim unchanged, so a
        non-string ``sub`` can be minted. ``None`` omits the claim.
    :param secret: signing secret; ``None`` means the real
        ``SUPABASE_JWT_SECRET``. Pass ``""`` for ``algorithm="none"``, which
        PyJWT refuses to encode with any key at all.
    :param algorithm: JWS algorithm to sign with; only ``HS256`` is a token
        the dependency should ever accept.
    :param audience: value for the ``aud`` claim; ``None`` omits the claim.
    :param expires_in: seconds until ``exp``; pass a negative value for an
        already-expired token, or ``None`` to omit the claim entirely.
    :returns: the encoded JWT.
    """
    now = int(time.time())
    claims: dict[str, object] = {"iat": now, "role": "authenticated"}
    if expires_in is not None:
        claims["exp"] = now + expires_in
    if subject is not None:
        claims["sub"] = str(subject) if isinstance(subject, uuid.UUID) else subject
    if audience is not None:
        claims["aud"] = audience
    key = secret if secret is not None else _env("SUPABASE_JWT_SECRET")
    return jwt.encode(claims, key, algorithm=algorithm)


async def call_whoami(headers: dict[str, str] | None = None) -> httpx.Response:
    """Call ``/whoami`` in-process, sending ``headers`` verbatim.

    :param headers: request headers, or ``None`` to send none -- which is how
        the tests about the ``Authorization`` header itself reach the
        dependency with nothing, or with another scheme, to authenticate.
    :returns: the raw response.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        return await client.get("/whoami", headers=headers)


async def get_whoami(token: str) -> tuple[int, dict]:
    """Call ``/whoami`` in-process with ``token`` as the bearer credentials.

    :param token: the token to send.
    :returns: the response status code and the decoded JSON body.
    """
    resp = await call_whoami({"Authorization": f"Bearer {token}"})
    return resp.status_code, resp.json()


@dataclass(frozen=True)
class OrgUser:
    """One org plus one auth user mirrored into ``public.users``.

    :ivar user_id: the ``auth.users.id`` / ``public.users.id`` value, which
        is what a Supabase access token carries in ``sub``.
    :ivar org_id: the ``orgs.id`` the user's ``public.users`` row points at.
    """

    user_id: uuid.UUID
    org_id: uuid.UUID


@pytest.fixture
async def org_user():
    """Create an org + a real Supabase Auth user mirrored into ``public.users``, then clean up.

    The org and the ``public.users`` row go in over the direct
    (service-role equivalent) DB connection, bypassing RLS by design --
    the same service-connection path the backend itself uses. The
    ``auth.users`` row is created through the Auth admin API rather than by
    hand-inserting into ``auth.users``, because that table's columns are
    Supabase's business, not ours; ``public.users.id`` FKs
    ``auth.users(id)``, so the row has to exist. This mirrors
    ``tests/db/test_rls.py``'s setup, minus the password grant that suite
    needs and this one doesn't.

    Teardown deletes the auth user (cascading ``public.users``) and then the
    org -- org FKs are ``NO ACTION``, not ``CASCADE``, so the order
    matters. Nothing is wrapped in try/except: a cleanup failure must fail
    the test loudly rather than leave silent litter behind.

    :yields: an :class:`OrgUser`.
    """
    supabase_url = _env("SUPABASE_URL")
    service_key = _env("SUPABASE_SERVICE_ROLE_KEY")
    admin_headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}"}

    suffix = uuid.uuid4().hex
    email = f"api-auth-test-{suffix}@example.com"

    async with db() as conn:
        org_id = await conn.fetchval(
            "insert into orgs (name) values ($1) returning id", f"API auth test org {suffix}"
        )

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{supabase_url}/auth/v1/admin/users",
            headers=admin_headers,
            json={"email": email, "password": f"Test-Password-{suffix}!", "email_confirm": True},
        )
        assert resp.status_code == 200, f"admin create user failed: {resp.status_code} {resp.text}"
        user_id = uuid.UUID(resp.json()["id"])

    async with db() as conn:
        await conn.execute(
            "insert into users (id, org_id, email) values ($1, $2, $3)", user_id, org_id, email
        )

    yield OrgUser(user_id=user_id, org_id=org_id)

    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            f"{supabase_url}/auth/v1/admin/users/{user_id}", headers=admin_headers
        )
        assert resp.status_code == 200, f"admin delete user failed: {resp.status_code} {resp.text}"

    async with db() as conn:
        await conn.execute("delete from orgs where id = $1", org_id)


@pytest.fixture(autouse=True)
async def _dispose_app_engine():
    """Drop the app engine's pooled connections after every test in ``tests/api``.

    ``src.db.session.engine`` is a module-level engine with a normal
    connection pool, while pytest-asyncio's auto mode gives each test its
    own event loop -- so without this the second DB-touching test in a run
    checks out a connection bound to the first test's (now closed) loop.
    Disposing afterwards is the fix ``src/db/session.py``'s own docstring
    points at; reshaping the app-level session module around the test
    harness is explicitly not wanted.

    Autouse, and in the shared conftest, so every test module in this
    directory gets it without having to remember to ask.
    """
    yield
    await engine.dispose()
