"""Tests for the JWKS key cache.

The cache exists because verification stopped being a pure function. It used
to be a string in the environment; now it depends on a key published by
Supabase Auth over HTTP, on a key that rotates. Three properties matter and
each has a way of going wrong that is invisible until it isn't:

* **fail closed** -- no key means reject, never "allow and hope";
* **survive an outage** -- a warm cache must keep working when the JWKS
  endpoint is down, or every user is locked out by someone else's downtime;
* **do not be a hammer** -- an unknown ``kid`` has to trigger a refetch (keys
  rotate) but an attacker can put any ``kid`` in a token, so unbounded
  refetching turns any request into a request against Supabase.

Nothing here touches the network: the fetch and the clock are both injected,
which is the whole reason they are parameters rather than imports.
"""

import pytest

from src.api.jwks import JwksCache, UnknownSigningKeyError


class FakeClock:
    """A monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeJwks:
    """Stands in for the HTTP fetch, counting how often it is called."""

    def __init__(self, *keys: str) -> None:
        self.keys = list(keys)
        self.calls = 0
        self.fails = False

    async def __call__(self) -> dict:
        self.calls += 1
        if self.fails:
            raise ConnectionError("jwks endpoint unreachable")
        # Shaped like a real Supabase response; only `kid` is read by these
        # tests, because key parsing is PyJWT's job and is covered where a
        # real token is verified.
        return {"keys": [{"kid": kid, "kty": "EC", "alg": "ES256"} for kid in self.keys]}


def cache(fetch, clock, cooldown: float = 30.0) -> JwksCache:
    return JwksCache(fetch=fetch, clock=clock, cooldown_seconds=cooldown)


async def test_a_known_key_is_fetched_once_and_then_cached() -> None:
    """The happy path must not put an HTTP round-trip in front of every request."""
    fetch, clock = FakeJwks("kid-a"), FakeClock()
    keys = cache(fetch, clock)

    first = await keys.jwk_for("kid-a")
    second = await keys.jwk_for("kid-a")

    assert first["kid"] == "kid-a"
    assert second["kid"] == "kid-a"
    assert fetch.calls == 1, "a cached key must not re-fetch"


async def test_an_unknown_key_refetches_once_then_refuses() -> None:
    """Keys rotate, so an unknown ``kid`` is a reason to look again -- once.

    ``cooldown=0`` isolates the question. With a cooldown the frozen clock
    would block the refetch and this would pass for the wrong reason; the
    cooldown gets its own tests below.
    """
    fetch, clock = FakeJwks("kid-a"), FakeClock()
    keys = cache(fetch, clock, cooldown=0.0)
    await keys.jwk_for("kid-a")

    with pytest.raises(UnknownSigningKeyError):
        await keys.jwk_for("kid-unknown")

    assert fetch.calls == 2, "an unknown kid should have prompted exactly one refetch"


async def test_a_rotated_key_is_picked_up_without_a_restart() -> None:
    """The point of refetching: Supabase rotates, and we must follow."""
    fetch, clock = FakeJwks("kid-old"), FakeClock()
    keys = cache(fetch, clock, cooldown=0.0)
    await keys.jwk_for("kid-old")

    fetch.keys.append("kid-new")

    assert (await keys.jwk_for("kid-new"))["kid"] == "kid-new"


async def test_repeated_unknown_kids_do_not_hammer_the_endpoint() -> None:
    """**The denial-of-service guard.**

    A ``kid`` is whatever the caller put in the token. Without a cooldown,
    anyone can turn every request they make into a request we make against
    Supabase Auth, which is both a bill and an outage.
    """
    fetch, clock = FakeJwks("kid-a"), FakeClock()
    keys = cache(fetch, clock, cooldown=30.0)
    await keys.jwk_for("kid-a")

    for attempt in range(20):
        with pytest.raises(UnknownSigningKeyError):
            await keys.jwk_for(f"made-up-{attempt}")

    assert fetch.calls == 1, (
        "twenty forged kids inside the window must cost nothing -- the only "
        "fetch here is the one that populated the cache"
    )


async def test_the_cooldown_expires_so_rotation_is_not_missed_forever() -> None:
    """The cooldown must delay a refetch, never prevent one.

    A rotation during the cooldown would otherwise lock every user out until
    the process restarted.
    """
    fetch, clock = FakeJwks("kid-a"), FakeClock()
    keys = cache(fetch, clock, cooldown=30.0)
    await keys.jwk_for("kid-a")
    with pytest.raises(UnknownSigningKeyError):
        await keys.jwk_for("kid-b")
    assert fetch.calls == 1, "refused from cache, without going out"

    clock.advance(31)
    fetch.keys.append("kid-b")

    assert (await keys.jwk_for("kid-b"))["kid"] == "kid-b"
    assert fetch.calls == 2


async def test_an_unreachable_endpoint_with_a_cold_cache_refuses() -> None:
    """**Fail closed.** A key we could not fetch is not a key we can trust."""
    fetch, clock = FakeJwks("kid-a"), FakeClock()
    fetch.fails = True
    keys = cache(fetch, clock)

    with pytest.raises(UnknownSigningKeyError):
        await keys.jwk_for("kid-a")


async def test_an_unreachable_endpoint_with_a_warm_cache_keeps_working() -> None:
    """Availability, and the reason the cache is not merely an optimisation.

    Supabase Auth being briefly down must not sign every user out. The keys
    we already hold are still the keys that signed their tokens.
    """
    fetch, clock = FakeJwks("kid-a"), FakeClock()
    keys = cache(fetch, clock, cooldown=0.0)
    await keys.jwk_for("kid-a")
    fetch.fails = True

    assert (await keys.jwk_for("kid-a"))["kid"] == "kid-a"

    # ...but an unknown one is still refused rather than guessed at.
    with pytest.raises(UnknownSigningKeyError):
        await keys.jwk_for("kid-b")
