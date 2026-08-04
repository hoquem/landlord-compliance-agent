"""Tests for ``src.api.routers.documents`` -- signed download URLs.

The export buckets are private on purpose, so a browser cannot fetch a
generated return directly. This router is the way in, and the org-scoped
lookup is the *only* thing stopping one org signing a URL for another's
export: ``documents.storage_path`` is free text, and RLS is inert on the
API's superuser connection.

Runs against the live local stack, and the signed URL is actually fetched --
a URL that 403s would be indistinguishable from one that works if we only
checked its shape.
"""

import uuid

import httpx

from tests.api.conftest import OrgUser, as_user, call, db


async def seed_document(org_user: OrgUser, path: str) -> str:
    """Insert a ``documents`` row pointing at ``path``."""
    async with db() as conn:
        return str(
            await conn.fetchval(
                "insert into documents (org_id, storage_path, kind) "
                "values ($1, $2, 'export_pdf') returning id",
                org_user.org_id,
                path,
            )
        )


async def test_download_needs_a_token() -> None:
    resp = await call("GET", f"/documents/{uuid.uuid4()}/download")
    assert resp.status_code == 401


async def test_a_signed_url_actually_fetches_the_object(org_user: OrgUser) -> None:
    """End to end: export, then follow the URL and get the bytes back.

    Checking only the URL's shape would pass just as happily for a URL that
    403s, which is the failure this is really about.
    """
    resp = await as_user(
        org_user, "POST", "/entities", {"name": "Owner", "tax_regime": "mtd_itsa"}
    )
    assert resp.status_code == 201, resp.text
    entity_id = resp.json()["id"]

    resp = await as_user(
        org_user,
        "POST",
        "/exports/quarter",
        {"entity_id": entity_id, "tax_year": 2026, "quarter": 1},
    )
    assert resp.status_code == 201, resp.text
    document_id = next(
        d["id"] for d in resp.json()["documents"] if d["kind"] == "export_category_csv"
    )

    resp = await as_user(org_user, "GET", f"/documents/{document_id}/download")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["expires_in"] > 0

    async with httpx.AsyncClient(follow_redirects=True) as client:
        downloaded = await client.get(body["url"])
    assert downloaded.status_code == 200, downloaded.text
    assert "hmrc_category" in downloaded.text


async def test_another_orgs_document_cannot_be_signed(make_org_user) -> None:
    """The only thing preventing a cross-org download, with no backstop."""
    alice = await make_org_user()
    bob = await make_org_user()
    bobs_document = await seed_document(bob, f"{bob.org_id}/x/secret.csv")

    resp = await as_user(alice, "GET", f"/documents/{bobs_document}/download")

    assert resp.status_code == 404, resp.text


async def test_an_unknown_document_is_a_404(org_user: OrgUser) -> None:
    resp = await as_user(org_user, "GET", f"/documents/{uuid.uuid4()}/download")
    assert resp.status_code == 404, resp.text
