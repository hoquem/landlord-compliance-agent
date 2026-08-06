"""Supabase-JWT authentication dependency for the Landlord Compliance Agent API.

Resolves the caller of a request to the ``(user_id, org_id)`` pair every
org-scoped router needs, in two steps that fail differently on purpose:

* verify the bearer token (signature, ``aud=authenticated``, unexpired, UUID
  ``sub``) -- anything wrong here is **401**, "you are not authenticated".
  Signatures are checked **ES256 against the project's published JWKS**, which
  is how Supabase signs end-user tokens; the legacy HS256 path against
  ``SUPABASE_JWT_SECRET`` stays available only where that secret is set. See
  :func:`_verified_claims` for why the algorithm is bound to the key source
  rather than read from the token;
* look the ``sub`` up in ``public.users`` to find its ``org_id`` -- a valid
  token for a user with no row there is **403**, "you are authenticated but
  not provisioned into an org".

The 403 is the load-bearing half. Every table in this schema is org-scoped
(see ``supabase/migrations/0002_rls.sql``), so a request that got this far
with no org is a provisioning bug, and the only safe thing to do with it is
refuse it loudly -- never to yield a blank/placeholder ``org_id`` and let
the request run on.

The org is read from the database on every request rather than trusted from
a token claim, so de-provisioning a user takes effect immediately instead of
whenever their current token happens to expire.

:seealso: backend/src/db/models.py (``User``); backend/tests/api/test_auth.py.
"""

import os
import uuid
from typing import Annotated, NamedTuple

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from src.api.jwks import JwksCache, UnknownSigningKeyError
from src.db.models import User
from src.db.session import async_session_factory

#: The algorithm Supabase Auth signs **end-user access tokens** with today:
#: ES256, against a rotating key published at the project's JWKS endpoint.
_ES256 = ["ES256"]

#: The legacy symmetric algorithm. Still in use for the anon and service-role
#: API keys, which are themselves JWTs signed with the shared secret, and
#: still how this repo's tests mint tokens offline.
_HS256 = ["HS256"]

#: The ``aud`` every Supabase *end-user* access token carries. Other tokens
#: are signed with the same secret (the legacy anon/service-role API keys
#: are themselves JWTs), so verifying the audience is what stops one of
#: those being accepted as a user.
_AUDIENCE = "authenticated"

#: Claims a token must carry to be usable at all: ``exp`` (a token with no
#: expiry would be valid forever) and ``sub`` (the user it speaks for).
#: ``aud`` is required implicitly by passing ``audience=`` to ``jwt.decode``.
#:
#: The ``sub`` entry does more than turn a subject-less token into a 401: it
#: is the only thing that guarantees ``claims["sub"]`` in ``require_auth``
#: exists. Drop it and a token without a subject becomes a ``KeyError`` --
#: an unhandled 500 -- rather than a 401.
_REQUIRED_CLAIMS = ["exp", "sub"]


def _optional_jwt_secret() -> str | None:
    """Read ``SUPABASE_JWT_SECRET``, which is now optional.

    **Its presence is the switch for the legacy HS256 path.** Supabase has
    moved end-user tokens to ES256, so the shared secret is no longer needed
    to authenticate a user -- but it still signs the anon and service-role
    keys, and it is how this repo's tests mint tokens without a network. A
    deployment that omits it is ES256-only, which is the intended shape for
    the cloud project.

    It is deliberately *not* required, unlike ``DATABASE_URL``: requiring it
    would force every deployment to hold a credential that can mint a token
    for any user, purely to satisfy a check it no longer performs.

    :returns: the secret, or ``None`` to disable HS256 entirely.
    """
    return os.environ.get("SUPABASE_JWT_SECRET") or None


def _supabase_url() -> str:
    """Read ``SUPABASE_URL``, which the JWKS endpoint hangs off.

    :raises RuntimeError: if unset. Unlike the secret this one *is* required:
        without it there is no way to fetch a signing key, and every request
        would 401 for a reason that looks like a token problem.
    :returns: the project base URL.
    """
    url = os.environ.get("SUPABASE_URL")
    if not url:
        raise RuntimeError(
            "SUPABASE_URL is not set, so the JWKS endpoint that publishes the "
            "token signing keys cannot be located. Start the local Supabase "
            "stack and run with `uv run --env-file ../.env ...`."
        )
    return url


#: Read at import time, so a deployment missing the URL fails on startup
#: rather than on its first authenticated request.
_SECRET = _optional_jwt_secret()
_jwks = JwksCache.for_project(_supabase_url())

#: ``auto_error=False`` so this module -- not FastAPI's version-dependent
#: default -- decides the status code for a missing or non-Bearer
#: ``Authorization`` header. It must be 401, because 403 means something
#: specific here (authenticated, but org-less).
_bearer_scheme = HTTPBearer(auto_error=False)


class AuthContext(NamedTuple):
    """The authenticated caller, as the ``(user_id, org_id)`` pair.

    :ivar user_id: ``public.users.id``, i.e. the token's ``sub`` -- the
        Supabase Auth user id.
    :ivar org_id: the org that user belongs to; every org-scoped query must
        filter on this.
    """

    user_id: uuid.UUID
    org_id: uuid.UUID


def _unauthenticated(detail: str) -> HTTPException:
    """Build the 401 used for every "this token cannot be trusted" case.

    :param detail: what specifically was wrong, for the client's benefit.
    :returns: a 401 carrying the ``WWW-Authenticate`` header RFC 6750
        requires of a bearer-token challenge.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _verified_claims(token: str) -> dict:
    """Verify a bearer token's signature and standard claims.

    **The algorithm is bound to the key source, never taken from the token.**
    This is the whole of the security argument here, and getting it wrong is
    a total bypass rather than a bug: the EC public key is *published* in the
    JWKS, so if a token's ``alg`` header were allowed to select the
    verification method, an attacker could sign ``alg: HS256`` using those
    public bytes as the HMAC secret and be verified as any user. Hence two
    closed branches:

    * a key from the JWKS may verify **ES256 only**;
    * the shared secret may verify **HS256 only**.

    The header's ``alg`` chooses *which branch*, which is safe precisely
    because neither branch can be reached with the other's key material.
    Pinned by ``test_the_public_key_cannot_be_used_as_an_hmac_secret``.

    :param token: the raw bearer credentials.
    :raises jwt.InvalidTokenError: for anything unverifiable -- including an
        unreadable header, an unknown ``kid``, and an algorithm this
        deployment does not accept. The caller turns all of it into one 401,
        because distinguishing them for a client would only help an attacker.
    :returns: the verified claims.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise jwt.InvalidTokenError(f"unreadable token header: {exc}") from exc

    algorithm = header.get("alg")

    if algorithm == "ES256":
        kid = header.get("kid")
        if not kid:
            raise jwt.InvalidTokenError("ES256 token carries no kid, so no key can be chosen")
        try:
            jwk = await _jwks.jwk_for(kid)
        except UnknownSigningKeyError as exc:
            raise jwt.InvalidTokenError(str(exc)) from exc
        key = jwt.PyJWK(jwk, algorithm="ES256").key
        algorithms = _ES256
    elif algorithm == "HS256" and _SECRET is not None:
        key = _SECRET
        algorithms = _HS256
    else:
        # Covers `none`, HS512-with-the-real-secret, RS256, and HS256 on a
        # deployment that has switched the legacy path off.
        raise jwt.InvalidTokenError(f"The specified alg value is not allowed: {algorithm!r}")

    return jwt.decode(
        token,
        key,
        algorithms=algorithms,
        audience=_AUDIENCE,
        options={"require": _REQUIRED_CLAIMS},
    )


async def require_auth(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> AuthContext:
    """Authenticate a request and resolve it to its user and org.

    :param credentials: the parsed ``Authorization: Bearer <token>`` header,
        or ``None`` when it is absent or uses another scheme.
    :raises HTTPException: 401 if the token is missing, unverifiable,
        expired, wrongly addressed, or doesn't name a UUID user; 403 if the
        token is valid but its ``sub`` has no ``public.users`` row.
    :returns: the caller's :class:`AuthContext`.
    """
    if credentials is None:
        raise _unauthenticated("Missing bearer token.")

    try:
        claims = await _verified_claims(credentials.credentials)
    except jwt.InvalidTokenError as exc:
        # PyJWT's messages ("Signature has expired", "Invalid audience",
        # ...) say which check failed without revealing anything secret, so
        # pass them through.
        raise _unauthenticated(f"Invalid bearer token: {exc}") from exc

    # ``_REQUIRED_CLAIMS`` guarantees ``sub`` is present, and PyJWT's
    # ``verify_sub`` check (on by default in the pinned pyjwt) has already
    # rejected a non-string ``sub`` as an ``InvalidSubjectError`` -- an
    # ``InvalidTokenError``, so a 401 above. ``claims["sub"]`` is therefore a
    # ``str``, whose only remaining failure mode here is being malformed:
    # ``ValueError``. That upstream guarantee is pinned by
    # ``test_token_with_non_string_subject_is_401``; without it a non-string
    # ``sub`` would reach this line and 500.
    try:
        user_id = uuid.UUID(claims["sub"])
    except ValueError as exc:
        raise _unauthenticated(f"Bearer token subject is not a UUID: {claims['sub']!r}") from exc

    async with async_session_factory() as session:
        # ``users.org_id`` is NOT NULL, so a NULL result can only mean
        # "no such row" -- no need to distinguish the two cases.
        org_id = await session.scalar(select(User.org_id).where(User.id == user_id))

    if org_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Authenticated user {user_id} does not belong to an org "
                "(no public.users row). The account has not been provisioned."
            ),
        )

    return AuthContext(user_id=user_id, org_id=org_id)


#: The annotation routers should use: ``auth: CurrentAuth`` in a path
#: operation is all it takes to require authentication and get the caller's
#: ``(user_id, org_id)``.
CurrentAuth = Annotated[AuthContext, Depends(require_auth)]
