"""FastAPI application for the Landlord Compliance Agent backend.

Importing this module reads ``DATABASE_URL`` and ``SUPABASE_JWT_SECRET``
(via the router's auth dependency and DB session), and fails loudly if
either is missing -- a deployment without them must not start.
"""

from fastapi import FastAPI

from src.api.routers import (
    certificates,
    dashboard,
    documents,
    exports,
    imports,
    portfolio,
    transactions,
)

app = FastAPI()
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
