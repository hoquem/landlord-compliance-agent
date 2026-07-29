"""Tests for ``src.api.auth`` -- the Supabase-JWT -> ``(user_id, org_id)`` dependency.

Runs against the live local Supabase stack (``supabase start`` from the repo
root) and, like ``backend/tests/db``, reads its env vars loudly
(``RuntimeError``) rather than skipping when they are missing::

    uv run --env-file ../.env pytest tests/api/

The fixtures and helpers used here -- the ``/whoami`` test app the requests
go through, ``mint_token``, and the ``org_user`` fixture -- live in
``tests/api/conftest.py``, to be shared with the router suites Tasks 13b-17
add. See that module's docstring for why tokens are hand-minted rather than
fetched from Supabase Auth's password grant.
"""

import uuid

from fastapi.security import HTTPAuthorizationCredentials

from src.api.auth import AuthContext, require_auth
from tests.api.conftest import OrgUser, call_whoami, db, get_whoami, mint_token


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
    resp = await call_whoami()
    assert resp.status_code == 401, resp.text
    assert resp.headers["WWW-Authenticate"] == "Bearer"


async def test_non_bearer_authorization_scheme_is_401() -> None:
    """A non-Bearer scheme is as unauthenticated as no header at all."""
    resp = await call_whoami({"Authorization": "Basic Zm9vOmJhcg=="})
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


async def test_unsigned_alg_none_token_is_401() -> None:
    """``alg: none`` must be rejected on the algorithm allowlist, before any signature check.

    The classic JWT forgery: strip the signature, claim the token needs none.
    ``_ALGORITHMS`` is what refuses it, and nothing else in this suite fails
    if ``"none"`` is added to that list.
    """
    token = mint_token(uuid.uuid4(), secret="", algorithm="none")
    status_code, body = await get_whoami(token)
    assert status_code == 401, body


async def test_hs512_token_signed_with_the_real_secret_is_401() -> None:
    """The allowlist pins one algorithm, not the HS family.

    A caller who knows the secret can sign HS512 as easily as HS256, so
    widening ``_ALGORITHMS`` to the family would let a token the dependency
    never issued through verification -- the algorithm header must not be the
    client's choice.

    (Minting this emits PyJWT's ``InsecureKeyLengthWarning``: the local
    ``SUPABASE_JWT_SECRET`` is 55 bytes, under RFC 7518's 64-byte
    recommendation for SHA512. Irrelevant here -- the token is meant to be
    rejected -- and no reason to lengthen the real secret.)
    """
    token = mint_token(uuid.uuid4(), algorithm="HS512")
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


async def test_token_without_expiry_is_401() -> None:
    """A token with no ``exp`` at all must be rejected, not treated as never expiring.

    ``exp`` is only checked if it is there, so requiring the claim is a
    separate guarantee from ``test_expired_token_is_401``: drop ``"exp"`` from
    ``_REQUIRED_CLAIMS`` and an omitted expiry silently becomes an
    everlasting token.
    """
    token = mint_token(uuid.uuid4(), expires_in=None)
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


async def test_token_with_non_string_subject_is_401() -> None:
    """A ``sub`` that isn't even a string must be 401, not a 500.

    This pins an assumption ``require_auth`` makes about PyJWT rather than
    about its own code: ``verify_sub`` (on by default) rejects a non-string
    ``sub``, which is why the ``uuid.UUID(claims["sub"])`` call below it
    catches ``ValueError`` alone. Should that upstream default ever change,
    an integer ``sub`` would reach ``uuid.UUID`` and raise ``AttributeError``
    -- unhandled, a 500 -- and this test is the only thing that would say so.
    """
    token = mint_token(12345)
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
    """The dependency's return value carries the user and org as ``uuid.UUID`` fields.

    Called directly rather than through a route, because the
    ``uuid.UUID`` -- not ``str`` -- types are invisible through JSON.
    Deliberately asserts on the fields by name and not by unpacking: the
    field names are the contract routers should depend on, so that adding a
    third field to ``AuthContext`` doesn't break every call site.
    """
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials=mint_token(org_user.user_id)
    )
    auth = await require_auth(credentials)

    assert isinstance(auth, AuthContext)
    assert auth.user_id == org_user.user_id
    assert auth.org_id == org_user.org_id
    assert isinstance(auth.user_id, uuid.UUID)
    assert isinstance(auth.org_id, uuid.UUID)


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
    status_code, body = await get_whoami(token)
    assert status_code == 200, body

    async with db() as conn:
        await conn.execute("delete from users where id = $1", org_user.user_id)

    status_code, body = await get_whoami(token)
    assert status_code == 403, body
