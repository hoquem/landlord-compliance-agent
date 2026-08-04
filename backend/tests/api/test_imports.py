"""Tests for ``src.api.routers.imports`` -- statement upload and parsing.

Runs against the live local Supabase stack, through the real mounted app,
with real Supabase-shaped JWTs and the real ``statements`` storage bucket
created by ``supabase/migrations/0003_storage.sql``::

    uv run --env-file ../.env pytest tests/api/test_imports.py

**The two things here that are not ordinary CRUD.**

*The storage path is a security boundary.* Objects live at
``statements/{org_id}/...`` and ``0003_storage.sql``'s policies enforce that
prefix -- but only on the direct-from-Flutter path. The API connects as the
``postgres`` superuser and bypasses RLS, so on this path the prefix is only
as trustworthy as the code that builds it. It must come from
``auth.org_id``, never from the uploaded filename, or a crafted name walks
out of the org's folder.

*The sign convention changes representation here.* ``core.parser`` emits
**signed** amounts (negative means money out); the database stores a
**magnitude** plus a ``direction`` enum. This module is where the two meet,
so an inversion introduced here would be invisible at both ends.
"""

import uuid
from decimal import Decimal

import pytest

from tests.api.conftest import OrgUser, as_user, call, db

GENERIC_CSV = (
    b"Date,Description,Amount,Balance\n"
    b"01/07/2026,RENT 106 SAMPLE CRES,1350.00,1350.00\n"
    b"03/07/2026,B&Q LUTON,-84.99,1265.01\n"
)

MALFORMED_CSV = (
    b"Date,Description,Amount,Balance\n"
    b"01/07/2026,GOOD ROW,10.00,10.00\n"
    b"not-a-date,BAD ROW,-5.00,5.00\n"
)


async def upload(
    org_user: OrgUser,
    *,
    content: bytes = GENERIC_CSV,
    filename: str = "statement.csv",
    bank: str = "generic",
    entity_id: str | None = None,
) -> object:
    """Upload one statement through the API.

    :param org_user: the caller.
    :param content: raw CSV bytes.
    :param filename: the client-supplied filename.
    :param bank: which registered format to parse as.
    :param entity_id: the owning entity; created on demand when omitted.
    :returns: the raw response.
    """
    if entity_id is None:
        resp = await as_user(
            org_user, "POST", "/entities", {"name": "Owner", "tax_regime": "mtd_itsa"}
        )
        assert resp.status_code == 201, resp.text
        entity_id = resp.json()["id"]
    return await as_user(
        org_user,
        "POST",
        "/imports",
        files={"file": (filename, content, "text/csv")},
        data={"entity_id": entity_id, "source_bank": bank},
    )


async def rows(table: str, org_id: uuid.UUID) -> list[dict]:
    """Read an org's rows from ``table`` straight from the database.

    :param table: table name.
    :param org_id: the owning org.
    :returns: one dict per row, oldest first.
    """
    async with db() as conn:
        records = await conn.fetch(
            f"select * from {table} where org_id = $1 order by created_at",
            org_id,
        )
    return [dict(r) for r in records]


# ---------------------------------------------------------------------------
# Auth -- every route, no exceptions. Copied from test_portfolio.py, where it
# is the only guard that catches a route added without `auth: CurrentAuth`.
# ---------------------------------------------------------------------------
def import_routes() -> list[tuple[str, str]]:
    """Enumerate every ``(method, path)`` the imports router exposes.

    :returns: one pair per route/method, path params filled with a random id.
    """
    from src.api.routers import imports as imports_router

    out: list[tuple[str, str]] = []
    for route in imports_router.router.routes:
        path = route.path.replace("{import_id}", str(uuid.uuid4()))
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            out.append((method, path))
    return out


@pytest.mark.parametrize(("method", "path"), import_routes())
async def test_every_import_route_requires_authentication(method: str, path: str) -> None:
    """A route shipped without the auth dependency is a public data leak."""
    resp = await call(method, path)
    assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"


# ---------------------------------------------------------------------------
# The happy path.
# ---------------------------------------------------------------------------
async def test_upload_parses_and_records_import_transactions_and_job(
    org_user: OrgUser,
) -> None:
    """One upload produces one parsed import, its transactions, and one job."""
    resp = await upload(org_user)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "parsed"
    assert body["error_detail"] is None
    assert body["source_bank"] == "generic"

    imports = await rows("imports", org_user.org_id)
    assert len(imports) == 1
    assert imports[0]["status"] == "parsed"

    txns = await rows("transactions", org_user.org_id)
    assert len(txns) == 2, "one transaction per parsed line"
    assert {t["status"] for t in txns} == {"unclassified"}, "nothing is categorised yet"
    assert all(t["import_id"] == imports[0]["id"] for t in txns)

    jobs = await rows("job_queue", org_user.org_id)
    assert len(jobs) == 1
    assert jobs[0]["type"] == "categorise"
    assert jobs[0]["status"] == "queued"
    assert str(imports[0]["id"]) in str(jobs[0]["payload"])


async def test_signed_parser_amounts_become_magnitude_plus_direction(
    org_user: OrgUser,
) -> None:
    """The parser signs amounts; the database stores magnitude + direction.

    This module is the only place the two representations meet, so an
    inversion here would look correct at both ends -- the CSV still says
    -84.99 and the row still says 84.99. Task 14 Step 1b.
    """
    resp = await upload(org_user)
    assert resp.status_code == 201, resp.text

    txns = {t["description"]: t for t in await rows("transactions", org_user.org_id)}
    money_in = txns["RENT 106 SAMPLE CRES"]
    money_out = txns["B&Q LUTON"]

    assert money_in["direction"] == "in"
    assert money_in["amount"] == Decimal("1350.00")
    assert money_out["direction"] == "out"
    # Compared as Decimal, not float: the column is numeric and this test is
    # about an exact money value, so a float comparison would be both the
    # wrong type (asyncpg returns Decimal) and the wrong kind of assertion.
    assert money_out["amount"] == Decimal("84.99"), "stored as magnitude, not -84.99"
    assert money_out["amount"] > 0, "a negative magnitude would double-count the sign"


# ---------------------------------------------------------------------------
# Failure is recorded, not swallowed and not rolled back.
# ---------------------------------------------------------------------------
async def test_unparseable_file_records_a_failed_import_with_no_transactions(
    org_user: OrgUser,
) -> None:
    """A bad row fails the whole import loudly, and leaves no partial data.

    The import row itself must survive: "the upload failed and here is which
    row" is the product's failure UX, and rolling the row back would leave
    the user with nothing to look at.
    """
    resp = await upload(org_user, content=MALFORMED_CSV)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "failed"
    assert body["error_detail"] is not None
    assert body["error_detail"]["row_number"] == 3, "the offending physical row is named"
    assert "unparseable date" in body["error_detail"]["message"]

    imports = await rows("imports", org_user.org_id)
    assert len(imports) == 1
    assert imports[0]["status"] == "failed"
    assert await rows("transactions", org_user.org_id) == [], "no partial ingest"
    assert await rows("job_queue", org_user.org_id) == [], "nothing to categorise"


async def test_file_that_is_not_from_the_named_bank_is_refused(org_user: OrgUser) -> None:
    """Declaring the wrong bank must not silently mis-parse into wrong money."""
    resp = await upload(org_user, content=b"Totally,Different,Header\n1,2,3\n")
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "failed"
    assert await rows("transactions", org_user.org_id) == []


async def test_unknown_bank_name_is_a_422_not_a_failed_import(org_user: OrgUser) -> None:
    """An unregistered bank is a client error, not a parse failure.

    Distinct from the test above: there the file was wrong, here the request
    is. Recording it as a failed *import* would imply we accepted a file we
    never could have read.
    """
    resp = await upload(org_user, bank="not-a-bank")
    assert resp.status_code == 422, resp.text
    assert await rows("imports", org_user.org_id) == []


# ---------------------------------------------------------------------------
# The storage path is a security boundary.
# ---------------------------------------------------------------------------
async def test_stored_path_is_prefixed_with_the_callers_org(org_user: OrgUser) -> None:
    """The org prefix is what ``0003_storage.sql``'s policies key on."""
    resp = await upload(org_user)
    assert resp.status_code == 201, resp.text
    assert resp.json()["file_path"].startswith(f"{org_user.org_id}/")


@pytest.mark.parametrize(
    "hostile",
    ["../../etc/passwd", "..%2f..%2fescape.csv", "/absolute.csv", "a/b/nested.csv"],
    ids=["traversal", "encoded-traversal", "absolute", "nested"],
)
async def test_client_filename_cannot_escape_the_org_prefix(
    org_user: OrgUser, hostile: str
) -> None:
    """A crafted filename must not walk out of the caller's folder.

    The stored path is built server-side from ``auth.org_id``; the client's
    filename contributes at most a leaf name. Without this, one org could
    write into another's prefix -- and the storage policies would be
    powerless, because the API bypasses them.
    """
    resp = await upload(org_user, filename=hostile)
    assert resp.status_code == 201, resp.text
    stored = resp.json()["file_path"]
    assert stored.startswith(f"{org_user.org_id}/")
    assert ".." not in stored
    assert not stored.startswith("/")


# ---------------------------------------------------------------------------
# Tenant isolation. RLS is inert here, so these are the whole boundary.
# ---------------------------------------------------------------------------
async def test_entity_belonging_to_another_org_is_refused(make_org_user) -> None:
    """An entity_id is client input and must be checked against the caller's org."""
    org_a = await make_org_user()
    org_b = await make_org_user()

    resp = await as_user(org_b, "POST", "/entities", {"name": "B", "tax_regime": "mtd_itsa"})
    assert resp.status_code == 201, resp.text
    b_entity = resp.json()["id"]

    resp = await upload(org_a, entity_id=b_entity)
    assert resp.status_code == 404, resp.text
    assert await rows("imports", org_a.org_id) == []


async def test_org_a_cannot_list_org_bs_imports(make_org_user) -> None:
    org_a = await make_org_user()
    org_b = await make_org_user()

    assert (await upload(org_b)).status_code == 201

    resp = await as_user(org_a, "GET", "/imports")
    assert resp.status_code == 200, resp.text
    assert resp.json() == [], "org A must not see org B's imports"

    resp = await as_user(org_b, "GET", "/imports")
    assert len(resp.json()) == 1, "positive control: org B sees its own"


async def test_get_imports_lists_with_status(org_user: OrgUser) -> None:
    assert (await upload(org_user)).status_code == 201
    assert (await upload(org_user, content=MALFORMED_CSV)).status_code == 201

    resp = await as_user(org_user, "GET", "/imports")
    assert resp.status_code == 200, resp.text
    assert {row["status"] for row in resp.json()} == {"parsed", "failed"}
