"""Cache of the signing keys Supabase Auth publishes at its JWKS endpoint.

Supabase signs end-user access tokens with a rotating **ES256** key and
publishes the public half at ``{SUPABASE_URL}/auth/v1/.well-known/jwks.json``.
That makes token verification depend on a network fetch, where it used to
depend on a string in the environment, and this module is the whole of that
difference.

Three properties, each with a failure mode that stays invisible until it
matters:

* **Fail closed.** A key we could not fetch is not a key we can trust. Every
  path out of here either returns a key or raises; there is no branch that
  proceeds without one.
* **Survive an outage.** Keys already held still verify the tokens they
  signed, so a brief Supabase Auth outage must not sign every user out. This
  is why the cache is not merely an optimisation.
* **Do not become a hammer.** An unknown ``kid`` is a reason to look again --
  keys rotate -- but ``kid`` is whatever the caller put in the token. Without
  a cooldown, anyone could turn each request they make into a request we make
  against Supabase. :data:`DEFAULT_COOLDOWN_SECONDS` bounds that to one
  refetch per window however many forged ``kid``s arrive.

``PyJWKClient`` from PyJWT does the same job and is deliberately not used: it
is synchronous, so a cache miss inside the auth dependency would block the
event loop for a whole HTTP round-trip and serialise every other request
behind it. ``httpx`` is already a dependency and already async.

:seealso: backend/src/api/auth.py (the only caller);
    backend/tests/api/test_jwks.py.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

import httpx

#: Minimum seconds between fetches prompted by an unknown ``kid``. Short
#: enough that a real rotation is picked up within a minute without a
#: restart; long enough that forged ``kid``s cost nothing.
DEFAULT_COOLDOWN_SECONDS = 30.0

#: Seconds to wait on the JWKS endpoint. It sits in the request path, so a
#: hung fetch would hold a request open; failing fast and rejecting is the
#: better answer, and a warm cache makes it rare.
DEFAULT_TIMEOUT_SECONDS = 5.0


class UnknownSigningKeyError(LookupError):
    """Raised when no trusted key matches the token's ``kid``.

    Deliberately the same outcome whether the key is genuinely unknown, the
    endpoint is unreachable, or the cache is cold: in all three cases we
    cannot verify the signature, and "cannot verify" has exactly one safe
    response.
    """


def jwks_url(supabase_url: str) -> str:
    """Build the JWKS URL for a Supabase project.

    :param supabase_url: the project's base URL.
    :returns: the well-known JWKS endpoint.
    """
    return f"{supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"


async def _fetch_over_http(url: str, timeout: float) -> dict:
    """Fetch a JWKS document.

    :param url: the JWKS endpoint.
    :param timeout: seconds to wait.
    :raises httpx.HTTPError: if the endpoint cannot be reached or refuses.
    :returns: the parsed document.
    """
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


class JwksCache:
    """Keys by ``kid``, refetched on a miss, at most once per cooldown.

    :param fetch: coroutine returning a JWKS document. Injected so tests need
        no network, and so the transport is replaceable without touching the
        caching rules -- which are the part worth testing.
    :param clock: monotonic time source, injected for the same reason.
    :param cooldown_seconds: minimum gap between miss-driven refetches.
    """

    def __init__(
        self,
        fetch: Callable[[], Awaitable[dict]],
        *,
        clock: Callable[[], float] = time.monotonic,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
    ) -> None:
        self._fetch = fetch
        self._clock = clock
        self._cooldown = cooldown_seconds
        self._keys: dict[str, dict] = {}
        #: ``None`` until the first attempt, so a cold cache always fetches.
        self._last_attempt: float | None = None

    @classmethod
    def for_project(
        cls,
        supabase_url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        **kwargs: object,
    ) -> JwksCache:
        """Build a cache pointed at a Supabase project's JWKS endpoint.

        :param supabase_url: the project's base URL.
        :param timeout: seconds to wait on each fetch.
        :returns: a cache using real HTTP.
        """
        url = jwks_url(supabase_url)

        async def fetch() -> dict:
            return await _fetch_over_http(url, timeout)

        return cls(fetch, **kwargs)  # type: ignore[arg-type]

    async def jwk_for(self, kid: str) -> dict:
        """Return the JWK with this ``kid``, refetching if it is unknown.

        :param kid: the key id from the token's header.
        :raises UnknownSigningKeyError: if no such key is held and one could
            not be fetched -- whether because it does not exist, the endpoint
            is down, or the cooldown has not elapsed. All three mean the same
            thing to a caller: this signature cannot be verified.
        :returns: the JWK, as published.
        """
        cached = self._keys.get(kid)
        if cached is not None:
            return cached

        if not self._may_refetch():
            raise UnknownSigningKeyError(
                f"no signing key {kid!r}, and the last lookup was too recent to try again"
            )

        await self._refresh()

        refreshed = self._keys.get(kid)
        if refreshed is None:
            raise UnknownSigningKeyError(f"no signing key {kid!r} is published")
        return refreshed

    def _may_refetch(self) -> bool:
        """Whether enough time has passed since the last attempt."""
        if self._last_attempt is None:
            return True
        return (self._clock() - self._last_attempt) >= self._cooldown

    async def _refresh(self) -> None:
        """Replace the cached keys, or leave them alone if the fetch fails.

        The timestamp is recorded **before** the fetch, so a failing endpoint
        is still rate-limited rather than retried on every request.

        A failure is swallowed on purpose: with a warm cache the right
        behaviour is to keep serving keys we already trust, and with a cold
        one :meth:`jwk_for` raises anyway because the key it wants is absent.
        Turning a fetch error into its own exception here would collapse
        "endpoint down" and "key genuinely unknown" into different outcomes,
        when they need the same one.
        """
        self._last_attempt = self._clock()
        try:
            document = await self._fetch()
        except Exception:  # noqa: BLE001 -- see the docstring: any failure to
            # reach the endpoint means "no new keys", and the caller's own
            # miss handling is what turns that into a refusal.
            return

        self._keys = {
            key["kid"]: key
            for key in document.get("keys", [])
            if isinstance(key, dict) and key.get("kid")
        }
