"""Supabase-JWT authentication dependency for the Landlord Compliance Agent API.

Resolves the caller of a request to the ``(user_id, org_id)`` pair every
org-scoped router needs, in two steps that fail differently on purpose:

* verify the bearer token (HS256 signature against ``SUPABASE_JWT_SECRET``,
  ``aud=authenticated``, unexpired, UUID ``sub``) -- anything wrong here is
  **401**, "you are not authenticated";
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

from src.db.models import User
from src.db.session import async_session_factory

#: The algorithm Supabase Auth signs access tokens with. Pinned to a
#: single-item list so a token whose header advertises anything else --
#: ``none`` above all -- is rejected outright.
_ALGORITHMS = ["HS256"]

#: The ``aud`` every Supabase *end-user* access token carries. Other tokens
#: are signed with the same secret (the legacy anon/service-role API keys
#: are themselves JWTs), so verifying the audience is what stops one of
#: those being accepted as a user.
_AUDIENCE = "authenticated"

#: Claims a token must carry to be usable at all: ``exp`` (a token with no
#: expiry would be valid forever) and ``sub`` (the user it speaks for).
#: ``aud`` is required implicitly by passing ``audience=`` to ``jwt.decode``.
_REQUIRED_CLAIMS = ["exp", "sub"]


def _jwt_secret() -> str:
    """Read ``SUPABASE_JWT_SECRET`` from the environment.

    :raises RuntimeError: if unset -- fail loudly rather than default,
        mirroring ``backend/src/db/session.py``. A missing secret must stop
        the process, not silently downgrade token verification.
    :returns: the HS256 secret Supabase Auth signs access tokens with.
    """
    secret = os.environ.get("SUPABASE_JWT_SECRET")
    if not secret:
        raise RuntimeError(
            "SUPABASE_JWT_SECRET is not set. Start the local Supabase stack "
            "(`supabase start` from the repo root) and run with "
            "`uv run --env-file ../.env ...`, or export SUPABASE_JWT_SECRET yourself."
        )
    return secret


#: Read at import time, so a deployment missing the secret fails on startup
#: rather than on its first authenticated request.
_SECRET = _jwt_secret()

#: ``auto_error=False`` so this module -- not FastAPI's version-dependent
#: default -- decides the status code for a missing or non-Bearer
#: ``Authorization`` header. It must be 401, because 403 means something
#: specific here (authenticated, but org-less).
_bearer_scheme = HTTPBearer(auto_error=False)


class AuthContext(NamedTuple):
    """The authenticated caller, as the ``(user_id, org_id)`` pair.

    A :class:`~typing.NamedTuple` so callers can either unpack it
    (``user_id, org_id = auth``) or read the fields by name.

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
        claims = jwt.decode(
            credentials.credentials,
            _SECRET,
            algorithms=_ALGORITHMS,
            audience=_AUDIENCE,
            options={"require": _REQUIRED_CLAIMS},
        )
    except jwt.InvalidTokenError as exc:
        # PyJWT's messages ("Signature has expired", "Invalid audience",
        # ...) say which check failed without revealing anything secret,
        # so pass them through rather than flattening every cause into one
        # opaque error.
        raise _unauthenticated(f"Invalid bearer token: {exc}") from exc

    try:
        user_id = uuid.UUID(claims["sub"])
    except (AttributeError, TypeError, ValueError) as exc:
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
