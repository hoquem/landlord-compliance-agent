"""Tests for the browser's side of the API boundary.

**These are the only tests in the suite that a browser's rules apply to.**
Every other API test goes through ``ASGITransport`` without an ``Origin``
header, so it never triggers a preflight and never has a response withheld —
which is exactly why the API shipped with no CORS configuration at all and
576 tests stayed green while the Flutter app could not make a single call.
Found on 2026-08-05 by loading the app: the dashboard rendered
``ClientException: Failed to fetch`` with no status code, the signature of a
response the browser refused to hand over.

Starlette's ``CORSMiddleware`` runs in the ASGI stack, so sending an
``Origin`` header through ``ASGITransport`` does exercise it for real. The
browser is not being simulated; the middleware that answers the browser is.

:seealso: backend/src/api/main.py.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import allowed_origins

APP_ORIGIN = "http://127.0.0.1:3000"
STRANGER = "https://not-our-app.example.com"


async def request(method: str, path: str, headers: dict[str, str]):
    """Make one in-process request against a freshly imported app."""
    from src.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.request(method, path, headers=headers)


async def test_the_app_origin_gets_a_preflight_it_can_use() -> None:
    """Without this, the browser never sends the real request at all.

    A preflight is not optional for a request carrying ``Authorization``:
    the browser asks first, and a 405 — which is what an app with no CORS
    middleware answers — means it stops there.
    """
    resp = await request(
        "OPTIONS",
        "/dashboard",
        {
            "Origin": APP_ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )

    assert resp.status_code == 200, resp.text
    assert resp.headers["access-control-allow-origin"] == APP_ORIGIN
    assert "authorization" in resp.headers["access-control-allow-headers"].lower()


async def test_a_real_response_is_released_to_the_app_origin() -> None:
    """The preflight passing is not enough; the actual response needs the header too."""
    resp = await request("GET", "/health", {"Origin": APP_ORIGIN})

    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == APP_ORIGIN


async def test_another_origin_is_not_allowed_to_read_anything() -> None:
    """**The point of the allowlist.**

    A user signed into this app is one click from any other page. Without
    this, a page that got them to visit it could read their tax figures
    using their own live session — the browser would hand the response over
    because we said anyone could have it.
    """
    resp = await request("GET", "/health", {"Origin": STRANGER})

    assert "access-control-allow-origin" not in resp.headers


async def test_credentials_are_not_invited() -> None:
    """This API authenticates by ``Authorization`` header, never by cookie.

    ``allow_credentials=True`` would let a browser attach ambient cookies to
    cross-origin calls, which is the ingredient CSRF needs and which nothing
    here wants.
    """
    resp = await request("GET", "/health", {"Origin": APP_ORIGIN})

    assert "access-control-allow-credentials" not in resp.headers


def test_the_allowlist_is_configuration_and_refuses_to_be_guessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset means stop, not "allow everything" and not "allow localhost".

    Which origins may read a tax ledger is a deployment decision. A default
    would be wrong in one of two directions and silent in both: permissive
    enough to leak, or localhost-only and mystifying in production.

    Tested through the function rather than by reloading the app module: a
    reload swaps the ``app`` object the rest of the suite is holding, and a
    test that leaves a landmine for whatever runs next is worse than the bug
    it guards.
    """
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)

    with pytest.raises(RuntimeError, match="CORS_ALLOWED_ORIGINS"):
        allowed_origins()


def test_the_allowlist_is_split_and_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env files are hand-edited, so a stray space must not create an origin.

    ``"a, b"`` has to mean two origins, not one origin and one called
    ``" b"`` — which would never match and would be invisible until a browser
    refused a response in production.
    """
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", f" {APP_ORIGIN} , http://localhost:3000 ")

    assert allowed_origins() == [APP_ORIGIN, "http://localhost:3000"]


def test_a_wildcard_allowlist_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """``*`` is never right for this API, so it must not be settable by accident.

    It is the one value that looks like it works everywhere and quietly makes
    every origin a reader of somebody's tax figures.
    """
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "*")

    with pytest.raises(RuntimeError, match="wildcard"):
        allowed_origins()
