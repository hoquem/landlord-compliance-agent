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

import base64
import hashlib
import hmac
import json
import time
import uuid

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.security import HTTPAuthorizationCredentials

from src.api import auth as auth_module
from src.api.auth import AuthContext, require_auth
from src.api.jwks import UnknownSigningKeyError
from tests.api.conftest import (
    OrgUser,
    call_whoami,
    db,
    get_whoami,
    mint_token,
    real_access_token,
)


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


# ---------------------------------------------------------------------------
# ES256 -- how Supabase actually signs an access token.
#
# Everything above this line uses a hand-minted HS256 token against the shared
# secret, which is fast, offline, and produces the negative cases. None of it
# noticed that the live stack had moved to ES256 and that every real token was
# being refused with a 401. These tests are the ones that would have.
#
# The key material here is generated in-process and handed to the app through
# a stub JWKS cache, so there is still no network -- but the *algorithm* and
# the key *type* are the real ones.
# ---------------------------------------------------------------------------
ES256_KID = "test-signing-key"


def es256_keypair():
    """A throwaway P-256 pair, the curve Supabase signs with."""
    private = ec.generate_private_key(ec.SECP256R1())
    jwk = pyjwt.algorithms.ECAlgorithm.to_jwk(private.public_key(), as_dict=True)
    jwk.update({"kid": ES256_KID, "alg": "ES256", "use": "sig"})
    return private, jwk


def mint_es256(private_key, subject, *, kid: str = ES256_KID, **overrides) -> str:
    """Sign a token the way Supabase Auth does: ES256, with a ``kid`` header."""
    now = int(time.time())
    claims = {
        "sub": str(subject),
        "aud": "authenticated",
        "role": "authenticated",
        "iat": now,
        "exp": now + 3600,
    }
    claims.update(overrides)
    return pyjwt.encode(claims, private_key, algorithm="ES256", headers={"kid": kid})


class StubJwks:
    """Serves a fixed JWK, so these tests need no Supabase and no network."""

    def __init__(self, jwk: dict | None) -> None:
        self._jwk = jwk

    async def jwk_for(self, kid: str) -> dict:
        if self._jwk is None or kid != self._jwk["kid"]:
            raise UnknownSigningKeyError(f"no signing key {kid!r}")
        return self._jwk


@pytest.fixture
def es256(monkeypatch: pytest.MonkeyPatch):
    """Point the auth dependency at a JWKS we control, and hand back the signer."""
    private, jwk = es256_keypair()
    monkeypatch.setattr(auth_module, "_jwks", StubJwks(jwk))
    return private


async def test_a_real_shaped_es256_token_is_accepted(org_user: OrgUser, es256) -> None:
    """**The blocker this change exists for.**

    Supabase signs end-user tokens ES256 against a rotating key. Before this,
    the API accepted HS256 only and answered every real session with
    ``Invalid bearer token: The specified alg value is not allowed``.
    """
    status_code, body = await get_whoami(mint_es256(es256, org_user.user_id))

    assert status_code == 200, body
    assert body["user_id"] == str(org_user.user_id)
    assert body["org_id"] == str(org_user.org_id)


async def test_a_token_signed_by_a_different_key_is_401(org_user: OrgUser, es256) -> None:
    """Right algorithm, right ``kid``, wrong private key."""
    impostor = ec.generate_private_key(ec.SECP256R1())

    status_code, _ = await get_whoami(mint_es256(impostor, org_user.user_id))

    assert status_code == 401


async def test_a_token_with_an_unknown_kid_is_401(org_user: OrgUser, es256) -> None:
    """An unpublished key id cannot be verified, so it is not trusted."""
    status_code, _ = await get_whoami(
        mint_es256(es256, org_user.user_id, kid="some-other-key")
    )

    assert status_code == 401


async def test_an_es256_token_with_no_kid_is_401(org_user: OrgUser, es256) -> None:
    """Without a ``kid`` there is no way to choose a key, and guessing is not one."""
    now = int(time.time())
    token = pyjwt.encode(
        {"sub": str(org_user.user_id), "aud": "authenticated", "exp": now + 3600},
        es256,
        algorithm="ES256",
    )

    assert (await get_whoami(token))[0] == 401


async def test_the_public_key_cannot_be_used_as_an_hmac_secret(
    org_user: OrgUser, es256
) -> None:
    """**The algorithm-confusion attack, and the reason for strict binding.**

    The EC public key is public -- it is published in the JWKS. If the token's
    ``alg`` header were allowed to choose the verification method, an attacker
    could take those public bytes, sign ``alg: HS256`` with them as the shared
    secret, and be verified as anyone. The defence is that a JWKS key may only
    ever verify ES256 and the shared secret may only ever verify HS256; the
    two key sources never cross.

    **The token is forged by hand, not with PyJWT.** PyJWT refuses to *encode*
    with an asymmetric key under HS256 ("should not be used as an HMAC
    secret") -- welcome defence in depth, but an attacker has no such
    scruples, and a test that leaned on it would be asserting PyJWT's
    behaviour rather than ours.
    """
    public_pem = es256.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    def b64(raw: bytes) -> bytes:
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    now = int(time.time())
    header = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": ES256_KID}).encode())
    payload = b64(
        json.dumps(
            {"sub": str(org_user.user_id), "aud": "authenticated", "exp": now + 3600}
        ).encode()
    )
    signing_input = header + b"." + payload
    signature = b64(hmac.new(public_pem, signing_input, hashlib.sha256).digest())
    forged = (signing_input + b"." + signature).decode()

    status_code, _ = await get_whoami(forged)

    assert status_code == 401, "the public key must never be usable as an HMAC secret"


async def test_hs256_still_works_while_the_shared_secret_is_configured(
    org_user: OrgUser, es256
) -> None:
    """Both paths live at once, which is what keeps the rest of this suite offline.

    Supabase is mid-migration: the anon and service-role keys are still HS256
    JWTs signed with this secret. Local and CI set it; a cloud deployment that
    omits it gets ES256 only.
    """
    status_code, body = await get_whoami(mint_token(org_user.user_id))

    assert status_code == 200, body


async def test_hs256_is_refused_when_no_shared_secret_is_configured(
    org_user: OrgUser, es256, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitting the secret is how a deployment turns the legacy path off.

    Without this, "we are ES256-only in production" would be a claim with
    nothing behind it.
    """
    token = mint_token(org_user.user_id)
    monkeypatch.setattr(auth_module, "_SECRET", None)

    status_code, _ = await get_whoami(token)

    assert status_code == 401


async def test_a_token_supabase_actually_issued_is_accepted(org_user: OrgUser) -> None:
    """**The test whose absence let the app ship unable to authenticate.**

    No stub: a real password grant against the running stack, verified
    against the real JWKS endpoint over real HTTP. Every other test in this
    file mints its own token, which is why all seventeen of them stayed green
    while the live stack moved to ES256 and refused every browser session.

    The algorithm is asserted rather than assumed. If Supabase moves again,
    this test should fail loudly on the *reason* rather than quietly keep
    passing against something else.
    """
    token = await real_access_token(org_user)
    header = pyjwt.get_unverified_header(token)
    assert header["alg"] == "ES256", f"Supabase is now issuing {header['alg']}"

    status_code, body = await get_whoami(token)

    assert status_code == 200, body
    assert body["user_id"] == str(org_user.user_id)
    assert body["org_id"] == str(org_user.org_id)
