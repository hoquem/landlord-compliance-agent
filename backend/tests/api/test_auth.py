"""Tests for ``src.api.auth`` -- the Supabase-JWT -> ``(user_id, org_id)`` dependency.

Runs against the live local Supabase stack (``supabase start`` from the repo
root) and, like ``backend/tests/db``, reads its env vars loudly
(``RuntimeError``) rather than skipping when they are missing::

    uv run --env-file ../.env pytest tests/api/

Tokens are minted here with PyJWT rather than fetched from Supabase Auth's
password grant: the dependency only ever *verifies* a token, so signing our
own is both faster and the only way to produce the negative cases (wrong
secret, wrong audience, already expired) at all. The signing secret is the
real ``SUPABASE_JWT_SECRET`` the local stack runs with and the claim shape
matches a real Supabase access token, so the happy path still exercises the
exact verification the frontend's tokens will hit --
``tests/db/test_rls.py`` covers real end-to-end Supabase-issued tokens.

Everything lives in this one file, with no ``tests/api/conftest.py``: with
no ``__init__.py`` anywhere under ``tests/``, a second ``conftest.py``
is imported under the same top-level module name ``conftest`` as
``tests/db/conftest.py`` and silently shadows it, breaking
``tests/db/test_schema.py``'s ``from conftest import ...``. Extract a
shared conftest only alongside the ``__init__.py`` files that would make it
safe, and only once a second ``tests/api`` module actually needs it.
"""

import os
import time
import uuid
from dataclasses import dataclass

import asyncpg
import httpx
import jwt
import pytest
from fastapi import FastAPI
from fastapi.security import HTTPAuthorizationCredentials
from httpx import ASGITransport, AsyncClient

from src.api.auth import AuthContext, CurrentAuth, require_auth
from src.db.session import engine

# ---------------------------------------------------------------------------
# Environment (loud reads -- no defaults, no skips).
# ---------------------------------------------------------------------------
#: Every helper below raises the same shape of error, so build the tail of
#: the message once.
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


# ---------------------------------------------------------------------------
# Test app: a single route that does nothing but expose what the dependency
# resolved. The routers that will really use ``CurrentAuth`` don't exist yet
# (Tasks 14+), and this suite is about the dependency, not any endpoint.
# ---------------------------------------------------------------------------
app = FastAPI()


@app.get("/whoami")
async def whoami(auth: CurrentAuth) -> dict[str, str]:
    """Echo the resolved identity.

    :param auth: injected by the dependency under test.
    :returns: the resolved user and org ids, as strings.
    """
    return {"user_id": str(auth.user_id), "org_id": str(auth.org_id)}


def mint_token(
    subject: uuid.UUID | str | None,
    *,
    secret: str | None = None,
    audience: str | None = "authenticated",
    expires_in: int = 3600,
) -> str:
    """Sign an HS256 token shaped like a Supabase Auth access token.

    :param subject: value for the ``sub`` claim; ``None`` omits the claim.
    :param secret: signing secret, defaulting to the real
        ``SUPABASE_JWT_SECRET``.
    :param audience: value for the ``aud`` claim; ``None`` omits the claim.
    :param expires_in: seconds until ``exp``; pass a negative value for an
        already-expired token.
    :returns: the encoded JWT.
    """
    now = int(time.time())
    claims: dict[str, object] = {"iat": now, "exp": now + expires_in, "role": "authenticated"}
    if subject is not None:
        claims["sub"] = str(subject)
    if audience is not None:
        claims["aud"] = audience
    return jwt.encode(claims, secret or _env("SUPABASE_JWT_SECRET"), algorithm="HS256")


async def get_whoami(token: str) -> tuple[int, dict]:
    """Call ``/whoami`` in-process with ``token`` as the bearer credentials.

    The two tests about the ``Authorization`` header itself (absent, or a
    different scheme) build their own request instead -- they have nothing
    to put here.

    :param token: the token to send.
    :returns: the response status code and the decoded JSON body.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    return resp.status_code, resp.json()


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------
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
    database_url = _env("DATABASE_URL")
    supabase_url = _env("SUPABASE_URL")
    service_key = _env("SUPABASE_SERVICE_ROLE_KEY")
    admin_headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}"}

    suffix = uuid.uuid4().hex
    email = f"api-auth-test-{suffix}@example.com"

    conn = await asyncpg.connect(database_url)
    try:
        org_id = await conn.fetchval(
            "insert into orgs (name) values ($1) returning id", f"API auth test org {suffix}"
        )
    finally:
        await conn.close()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{supabase_url}/auth/v1/admin/users",
            headers=admin_headers,
            json={"email": email, "password": f"Test-Password-{suffix}!", "email_confirm": True},
        )
        assert resp.status_code == 200, f"admin create user failed: {resp.status_code} {resp.text}"
        user_id = uuid.UUID(resp.json()["id"])

    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute(
            "insert into users (id, org_id, email) values ($1, $2, $3)", user_id, org_id, email
        )
    finally:
        await conn.close()

    yield OrgUser(user_id=user_id, org_id=org_id)

    # --- Cleanup (no try/except -- a failure here must fail loudly). ---
    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            f"{supabase_url}/auth/v1/admin/users/{user_id}", headers=admin_headers
        )
        assert resp.status_code == 200, f"admin delete user failed: {resp.status_code} {resp.text}"

    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute("delete from orgs where id = $1", org_id)
    finally:
        await conn.close()


@pytest.fixture(autouse=True)
async def _dispose_app_engine():
    """Drop the app engine's pooled connections after every test in this module.

    ``src.db.session.engine`` is a module-level engine with a normal
    connection pool, while pytest-asyncio's auto mode gives each test its
    own event loop -- so without this the second DB-touching test in a run
    checks out a connection bound to the first test's (now closed) loop.
    Disposing afterwards is the fix ``src/db/session.py``'s own docstring
    points at; reshaping the app-level session module around the test
    harness is explicitly not wanted.
    """
    yield
    await engine.dispose()


# ---------------------------------------------------------------------------
# 401 -- nothing usable to authenticate with.
# ---------------------------------------------------------------------------
async def test_request_without_bearer_is_401() -> None:
    """No ``Authorization`` header at all must be 401, never 403.

    FastAPI's ``HTTPBearer(auto_error=True)`` has historically raised 403
    here, which would collide with the org-less 403 below and leave clients
    unable to tell "log in" from "your account isn't provisioned". The
    dependency owns this status code itself; this test pins it, along with
    the ``WWW-Authenticate`` challenge RFC 6750 requires of a 401.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get("/whoami")
    assert resp.status_code == 401, resp.text
    assert resp.headers["WWW-Authenticate"] == "Bearer"


async def test_non_bearer_authorization_scheme_is_401() -> None:
    """A non-Bearer scheme is as unauthenticated as no header at all."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get("/whoami", headers={"Authorization": "Basic Zm9vOmJhcg=="})
    assert resp.status_code == 401, resp.text


async def test_garbage_token_is_401() -> None:
    """A token that isn't even a JWT must be rejected, not blow up as a 500."""
    status_code, body = await get_whoami("not-a-jwt")
    assert status_code == 401, body


async def test_token_signed_with_wrong_secret_is_401() -> None:
    """Signature verification must actually happen -- 401, not a decoded-anyway 200."""
    token = mint_token(uuid.uuid4(), secret="not-the-supabase-jwt-secret-but-long-enough")
    status_code, body = await get_whoami(token)
    assert status_code == 401, body


async def test_token_with_wrong_audience_is_401() -> None:
    """``aud`` must be ``authenticated``.

    Supabase signs other token kinds with the same secret (the legacy
    anon/service-role API keys are themselves JWTs), so the audience check
    is what stops a service-role key being waved through as an end user.
    """
    token = mint_token(uuid.uuid4(), audience="service_role")
    status_code, body = await get_whoami(token)
    assert status_code == 401, body


async def test_token_without_audience_is_401() -> None:
    """A missing ``aud`` claim must fail the audience check, not skip it."""
    token = mint_token(uuid.uuid4(), audience=None)
    status_code, body = await get_whoami(token)
    assert status_code == 401, body


async def test_expired_token_is_401() -> None:
    """An expired but otherwise perfectly valid token must be rejected."""
    token = mint_token(uuid.uuid4(), expires_in=-60)
    status_code, body = await get_whoami(token)
    assert status_code == 401, body


async def test_token_without_subject_is_401() -> None:
    """A correctly signed token with no ``sub`` identifies nobody -- 401."""
    token = mint_token(None)
    status_code, body = await get_whoami(token)
    assert status_code == 401, body


async def test_token_with_non_uuid_subject_is_401() -> None:
    """A ``sub`` that isn't a UUID can't be a Supabase user id -- 401, not a 500."""
    token = mint_token("definitely-not-a-uuid")
    status_code, body = await get_whoami(token)
    assert status_code == 401, body


# ---------------------------------------------------------------------------
# 200 -- valid token for a provisioned user.
# ---------------------------------------------------------------------------
async def test_valid_token_resolves_user_and_org(org_user: OrgUser) -> None:
    """A valid token whose ``sub`` has a ``public.users`` row resolves to that row's org."""
    status_code, body = await get_whoami(mint_token(org_user.user_id))
    assert status_code == 200, body
    assert body == {"user_id": str(org_user.user_id), "org_id": str(org_user.org_id)}


async def test_dependency_yields_user_id_and_org_id_pair(org_user: OrgUser) -> None:
    """The dependency's return value is the ``(user_id, org_id)`` pair, as UUIDs.

    Called directly rather than through a route: the tuple contract (later
    routers may well unpack it) and the ``uuid.UUID`` -- not ``str`` --
    types are both invisible through JSON.
    """
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials=mint_token(org_user.user_id)
    )
    auth = await require_auth(credentials)

    assert isinstance(auth, AuthContext)
    user_id, org_id = auth
    assert (user_id, org_id) == (org_user.user_id, org_user.org_id)
    assert isinstance(user_id, uuid.UUID)
    assert isinstance(org_id, uuid.UUID)


# ---------------------------------------------------------------------------
# 403 -- authenticated, but not a member of any org.
# ---------------------------------------------------------------------------
async def test_authenticated_user_without_users_row_is_403() -> None:
    """An authenticated token whose ``sub`` has no ``public.users`` row must 403, loudly.

    A random ``sub`` needs no ``auth.users`` row to set this up, precisely
    because the dependency deliberately resolves the org from
    ``public.users`` alone: membership of an org is what authorises a
    request here, and existing in Supabase Auth is not membership. The
    failure mode this guards against is the silent one -- yielding a blank
    ``org_id`` and letting the request proceed org-less.
    """
    status_code, body = await get_whoami(mint_token(uuid.uuid4()))
    assert status_code == 403, body
    assert "org" in body["detail"].lower(), f"403 detail should explain the problem: {body}"


async def test_valid_token_is_403_once_the_users_row_is_gone(org_user: OrgUser) -> None:
    """Deleting the ``public.users`` row flips a previously-working token from 200 to 403.

    The org lookup must happen per request against the database, not be
    read off the token's own claims -- otherwise a de-provisioned user
    keeps their access until their token happens to expire.
    """
    token = mint_token(org_user.user_id)
    assert (await get_whoami(token))[0] == 200

    conn = await asyncpg.connect(_env("DATABASE_URL"))
    try:
        await conn.execute("delete from users where id = $1", org_user.user_id)
    finally:
        await conn.close()

    status_code, body = await get_whoami(token)
    assert status_code == 403, body
