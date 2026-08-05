"""FastAPI application for the Landlord Compliance Agent backend.

Importing this module reads ``DATABASE_URL``, ``SUPABASE_JWT_SECRET`` (via
the router's auth dependency and DB session) and ``CORS_ALLOWED_ORIGINS``,
and fails loudly if any is missing -- a deployment without them must not
start.

**The CORS allowlist is the browser's half of the tenant boundary**, and it
was missing entirely until 2026-08-05. Every API test runs through
``ASGITransport`` with no ``Origin`` header, so nothing in the suite ever
triggered a preflight; the Flutter app meanwhile could not complete a single
call, and said so as ``ClientException: Failed to fetch`` with no status
code. The gap was structural, not an oversight in any one test -- see
``tests/api/test_cors.py``, which is the only place a browser's rules apply.
"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routers import (
    certificates,
    dashboard,
    documents,
    exports,
    imports,
    portfolio,
    transactions,
)

#: Header the app authenticates with. Listed explicitly because a preflight
#: for a request carrying it is refused unless the server names it.
_ALLOWED_HEADERS = ["authorization", "content-type"]

#: Everything the routers expose. ``OPTIONS`` is answered by the middleware.
_ALLOWED_METHODS = ["GET", "POST", "PATCH", "PUT", "DELETE"]


def allowed_origins() -> list[str]:
    """Read the CORS allowlist from ``CORS_ALLOWED_ORIGINS``.

    Comma-separated, whitespace-trimmed. Required, and deliberately without
    a default: which origins may read a tax ledger is a deployment decision,
    and any default is wrong in one of two silent directions -- permissive
    enough to leak, or localhost-only and baffling in production.

    :raises RuntimeError: if unset or empty; or if set to ``*``, which is the
        one value that looks universally correct and quietly lets any page a
        signed-in user visits read their figures using their own session.
    :returns: the allowed origins, in the order given.
    """
    raw = os.environ.get("CORS_ALLOWED_ORIGINS", "")
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    if not origins:
        raise RuntimeError(
            "CORS_ALLOWED_ORIGINS is not set. It must list the exact origins the "
            "web app is served from, comma-separated -- e.g. "
            "'http://127.0.0.1:3000,http://localhost:3000' for local development. "
            "Run with `uv run --env-file ../.env ...`, which sets it."
        )
    if "*" in origins:
        raise RuntimeError(
            "CORS_ALLOWED_ORIGINS must not contain the wildcard '*'. This API "
            "serves one org's tax figures to a browser holding that user's "
            "session; name the origins instead."
        )
    return origins


app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_methods=_ALLOWED_METHODS,
    allow_headers=_ALLOWED_HEADERS,
    # Off, and it matters. This API authenticates by `Authorization` header,
    # never by cookie, so nothing needs ambient credentials attached to a
    # cross-origin request -- and inviting them is the ingredient CSRF wants.
    allow_credentials=False,
)
app.include_router(certificates.router)
app.include_router(dashboard.router)
app.include_router(documents.router)
app.include_router(exports.router)
app.include_router(imports.router)
app.include_router(portfolio.router)
app.include_router(transactions.router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint.

    :return: Status response.
    """
    return {"status": "ok"}
