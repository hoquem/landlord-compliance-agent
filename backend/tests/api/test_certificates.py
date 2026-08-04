"""Tests for ``src.api.routers.certificates`` -- the compliance certificates CRUD.

Runs against the live local Supabase stack, through the real mounted app::

    uv run --env-file ../.env pytest tests/api/test_certificates.py

**Property is the aggregate root** (``docs/domain/compliance.md``), and the
invariant it protects is "a certificate belongs to exactly one property, in
one org". Routes are flat -- one canonical URL per certificate -- and that
invariant is upheld by the org-scoped property lookup on create and on any
PATCH that moves a certificate. ``0002_rls.sql``'s composite foreign keys
already make a cross-org write *impossible*, so those lookups are not the
tenant boundary; they are what makes the answer a 404 instead of a 500.
The tests below say which is which.

**Status is derived on every read and never stored.** The exact boundaries
(expiring today, exactly 60 days out, 61 days out) are pinned in
``tests/core/test_certificates.py`` against fixed dates -- there, not here,
because a boundary case exercised through the API would be a flake.
Everything below uses generous offsets and the router's own
:func:`~src.core.certificates.uk_today`, so no offset is close enough to an
edge for a clock to matter, including a run that crosses midnight.
"""

import datetime
import uuid

import pytest

from src.api.routers import certificates as certificates_router
from src.core.certificates import uk_today
from src.db import models
from tests.api.conftest import (
    OrgUser,
    as_user,
    assert_not_nullable_matches_schema,
    call,
    db,
    mint_token,
)

TODAY = uk_today()


def days(offset: int) -> str:
    """Return an ISO date ``offset`` days from today.

    Offsets are deliberately generous everywhere in this module -- never
    0, 60 or 61 -- so that nothing here can flip on a clock. The edges are
    ``tests/core/test_certificates.py``'s business.
    """
    return (TODAY + datetime.timedelta(days=offset)).isoformat()


async def make_property(org_user: OrgUser, line1: str = "106 Sample Cres") -> str:
    """Create a property through the API and return its id."""
    resp = await as_user(
        org_user,
        "POST",
        "/properties",
        {
            "address_line1": line1,
            "city": "Luton",
            "postcode": "LU1 1AA",
            "finance_cost_classification": "residential",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def make_document(org_user: OrgUser) -> str:
    """Insert a ``documents`` row directly and return its id.

    Written directly because **nothing in the API creates a document for a
    certificate** -- the only writer is the export endpoint, and the only
    buckets are ``statements`` and ``exports``. Certificate file upload
    needs a bucket and an endpoint that Task 17 does not cover; this
    endpoint validates the reference, which is what the plan asked for.
    """
    async with db() as conn:
        return str(
            await conn.fetchval(
                "insert into documents (org_id, storage_path, kind) "
                "values ($1, 'seeded/cert.pdf', 'certificate') returning id",
                org_user.org_id,
            )
        )


async def create_certificate(
    org_user: OrgUser,
    *,
    property_id: str | None = None,
    certificate_type: str = "gas_safety",
    expiry_date: str | None = None,
    **extra: object,
) -> dict:
    """Create one certificate through the API and return the response body."""
    if property_id is None:
        property_id = await make_property(org_user)
    body = {
        "property_id": property_id,
        "certificate_type": certificate_type,
        "expiry_date": expiry_date or days(200),
        **extra,
    }
    resp = await as_user(org_user, "POST", "/certificates", body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def rows(table: str, org_id: uuid.UUID) -> list[dict]:
    """Read an org's rows from ``table`` straight from the database."""
    async with db() as conn:
        records = await conn.fetch(
            f"select * from {table} where org_id = $1 order by created_at", org_id
        )
    return [dict(r) for r in records]


async def audit_actions(org_id: uuid.UUID) -> list[str]:
    """Return an org's audit actions."""
    async with db() as conn:
        records = await conn.fetch("select action from audit_log where org_id = $1", org_id)
    return [r["action"] for r in records]


# ---------------------------------------------------------------------------
# Auth -- every route.
# ---------------------------------------------------------------------------
def certificate_routes() -> list[tuple[str, str]]:
    """Enumerate every ``(method, path)`` the certificates router exposes.

    Derived from the router rather than hand-listed, so a route added
    without auth fails here instead of shipping open.
    """
    found = []
    for route in certificates_router.router.routes:
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            found.append((method, route.path.replace("{certificate_id}", str(uuid.uuid4()))))
    return found


@pytest.mark.parametrize(("method", "path"), certificate_routes())
async def test_every_route_requires_a_token(method: str, path: str) -> None:
    """No credentials must never reach a handler."""
    resp = await call(method, path, json={})
    assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"


# ---------------------------------------------------------------------------
# Create and read.
# ---------------------------------------------------------------------------
async def test_creating_a_certificate_stores_it_and_derives_its_status(
    org_user: OrgUser,
) -> None:
    """The row lands, and the response carries a status no column holds."""
    property_id = await make_property(org_user)

    created = await create_certificate(
        org_user,
        property_id=property_id,
        expiry_date=days(200),
        issue_date=days(-165),
        certificate_ref="GS-2026-0042",
    )

    assert created["certificate_type"] == "gas_safety"
    assert created["status"] == "valid"
    stored = await rows("compliance_certificates", org_user.org_id)
    assert len(stored) == 1
    assert stored[0]["certificate_ref"] == "GS-2026-0042"
    assert "status" not in stored[0], "status must be derived, never stored"


@pytest.mark.parametrize(
    ("offset", "expected"),
    [(-10, "expired"), (10, "expiring"), (400, "valid")],
)
async def test_status_is_derived_from_the_expiry_date(
    org_user: OrgUser, offset: int, expected: str
) -> None:
    """All three states reachable through the API, at safe distances from the edges."""
    created = await create_certificate(org_user, expiry_date=days(offset))
    assert created["status"] == expected


async def test_reading_one_certificate_derives_its_status_too(org_user: OrgUser) -> None:
    """Not just the create response -- every read recomputes.

    A status computed once at creation would be right for a day and wrong
    forever after, which is the whole reason it is not a column.
    """
    created = await create_certificate(org_user, expiry_date=days(-10))

    resp = await as_user(org_user, "GET", f"/certificates/{created['id']}")

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "expired"


async def test_every_certificate_type_is_accepted(org_user: OrgUser) -> None:
    """All five, so a type the schema allows cannot be refused by the API."""
    property_id = await make_property(org_user)
    for certificate_type in ["gas_safety", "eicr", "epc", "hmo_licence", "selective_licence"]:
        created = await create_certificate(
            org_user, property_id=property_id, certificate_type=certificate_type
        )
        assert created["certificate_type"] == certificate_type


async def test_an_unknown_certificate_type_is_refused(org_user: OrgUser) -> None:
    """422, not an enum error from Postgres."""
    property_id = await make_property(org_user)
    resp = await as_user(
        org_user,
        "POST",
        "/certificates",
        {
            "property_id": property_id,
            "certificate_type": "boiler_warranty",
            "expiry_date": days(100),
        },
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# The grouped list.
# ---------------------------------------------------------------------------
async def test_the_list_groups_certificates_by_property(org_user: OrgUser) -> None:
    """One group per property that has certificates, in property order.

    Properties with no certificates are **not** included: this endpoint's
    subject is certificates. "Which property is missing a gas safety
    certificate?" cannot be answered here at all, because nothing records
    which types a property requires -- that is a dashboard question, and it
    joins this with ``GET /properties``.
    """
    first = await make_property(org_user, "1 First Street")
    second = await make_property(org_user, "2 Second Street")
    empty = await make_property(org_user, "3 Empty Street")
    await create_certificate(org_user, property_id=first, certificate_type="epc")
    await create_certificate(org_user, property_id=second, certificate_type="eicr")

    resp = await as_user(org_user, "GET", "/certificates")

    assert resp.status_code == 200, resp.text
    groups = resp.json()
    assert [g["property_id"] for g in groups] == [first, second]
    assert empty not in [g["property_id"] for g in groups]
    assert [c["certificate_type"] for c in groups[0]["certificates"]] == ["epc"]


async def test_certificates_within_a_group_are_soonest_expiry_first(
    org_user: OrgUser,
) -> None:
    """The order a compliance screen needs: what lapses next, first."""
    property_id = await make_property(org_user)
    await create_certificate(
        org_user, property_id=property_id, certificate_type="epc", expiry_date=days(300)
    )
    await create_certificate(
        org_user, property_id=property_id, certificate_type="eicr", expiry_date=days(-5)
    )
    await create_certificate(
        org_user, property_id=property_id, certificate_type="gas_safety", expiry_date=days(20)
    )

    resp = await as_user(org_user, "GET", "/certificates")

    assert resp.status_code == 200, resp.text
    group = resp.json()[0]
    assert [c["certificate_type"] for c in group["certificates"]] == [
        "eicr",
        "gas_safety",
        "epc",
    ]
    assert [c["status"] for c in group["certificates"]] == ["expired", "expiring", "valid"]


# ---------------------------------------------------------------------------
# Validation.
# ---------------------------------------------------------------------------
async def test_an_issue_date_after_the_expiry_date_is_refused(org_user: OrgUser) -> None:
    """A transposed pair would make a valid certificate read as expired.

    The most plausible data-entry error on this form, and the only one that
    silently inverts the answer the page exists to give.
    """
    property_id = await make_property(org_user)
    resp = await as_user(
        org_user,
        "POST",
        "/certificates",
        {
            "property_id": property_id,
            "certificate_type": "gas_safety",
            "issue_date": days(100),
            "expiry_date": days(10),
        },
    )
    assert resp.status_code == 422, resp.text
    assert "issue" in resp.text.lower()


async def test_an_issue_date_equal_to_the_expiry_date_is_allowed(org_user: OrgUser) -> None:
    """Same-day is degenerate but not wrong, and refusing it would be a guess."""
    created = await create_certificate(
        org_user, expiry_date=days(10), issue_date=days(10)
    )
    assert created["issue_date"] == days(10)


async def test_a_patch_cannot_transpose_the_dates_either(org_user: OrgUser) -> None:
    """The guard has to see the *resulting* row, not just the request.

    Patching only ``issue_date`` has to be checked against the **stored**
    expiry date -- a validator that only looks at the fields present would
    wave this through.
    """
    created = await create_certificate(org_user, expiry_date=days(10))

    resp = await as_user(
        org_user, "PATCH", f"/certificates/{created['id']}", {"issue_date": days(100)}
    )

    assert resp.status_code == 422, resp.text


async def test_a_missing_expiry_date_is_refused(org_user: OrgUser) -> None:
    """Required -- a certificate without one cannot answer the only question."""
    property_id = await make_property(org_user)
    resp = await as_user(
        org_user,
        "POST",
        "/certificates",
        {"property_id": property_id, "certificate_type": "epc"},
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Update and delete.
# ---------------------------------------------------------------------------
async def test_patching_a_certificate_changes_only_what_was_named(
    org_user: OrgUser,
) -> None:
    """RFC 7386: an absent key leaves the stored value alone."""
    created = await create_certificate(
        org_user, expiry_date=days(200), certificate_ref="GS-1"
    )

    resp = await as_user(
        org_user, "PATCH", f"/certificates/{created['id']}", {"expiry_date": days(5)}
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "expiring"
    assert resp.json()["certificate_ref"] == "GS-1"


@pytest.mark.parametrize("field", ["issue_date", "certificate_ref", "document_id"])
async def test_a_nullable_field_can_be_cleared(org_user: OrgUser, field: str) -> None:
    """An explicit null wipes it -- mis-entered paperwork must be removable."""
    document_id = await make_document(org_user)
    created = await create_certificate(
        org_user,
        issue_date=days(-100),
        certificate_ref="GS-1",
        document_id=document_id,
    )
    assert created[field] is not None

    resp = await as_user(org_user, "PATCH", f"/certificates/{created['id']}", {field: None})

    assert resp.status_code == 200, resp.text
    assert resp.json()[field] is None


@pytest.mark.parametrize("field", ["property_id", "certificate_type", "expiry_date"])
async def test_a_not_null_field_cannot_be_cleared(org_user: OrgUser, field: str) -> None:
    """422, never an IntegrityError reaching the client as a 500."""
    created = await create_certificate(org_user)

    resp = await as_user(org_user, "PATCH", f"/certificates/{created['id']}", {field: None})

    assert resp.status_code == 422, resp.text


async def test_an_empty_patch_is_refused(org_user: OrgUser) -> None:
    """200 would tell a caller its mistaken update had been applied."""
    created = await create_certificate(org_user)

    resp = await as_user(org_user, "PATCH", f"/certificates/{created['id']}", {})

    assert resp.status_code == 422, resp.text


async def test_not_nullable_is_exactly_what_the_schema_says() -> None:
    """``_NOT_NULLABLE`` is derived-checked against the mapped columns.

    See :func:`~tests.api.conftest.assert_not_nullable_matches_schema`.
    This body is the first to exercise its mapper-keyed lookup:
    ``ComplianceCertificate`` maps the column ``type`` to the attribute
    ``certificate_type``, and a table-keyed lookup raises ``KeyError``
    there.
    """
    assert_not_nullable_matches_schema(
        certificates_router.CertificateUpdate, models.ComplianceCertificate
    )


async def test_a_certificate_can_be_moved_to_another_property(org_user: OrgUser) -> None:
    """Mis-filing is real across a dozen similar addresses."""
    created = await create_certificate(org_user)
    elsewhere = await make_property(org_user, "9 Other Road")

    resp = await as_user(
        org_user, "PATCH", f"/certificates/{created['id']}", {"property_id": elsewhere}
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["property_id"] == elsewhere


async def test_deleting_a_certificate_removes_it(org_user: OrgUser) -> None:
    """204, and the row is gone -- a lapsed certificate is replaced, not kept."""
    created = await create_certificate(org_user)

    resp = await as_user(org_user, "DELETE", f"/certificates/{created['id']}")

    assert resp.status_code == 204, resp.text
    assert await rows("compliance_certificates", org_user.org_id) == []
    assert (
        await as_user(org_user, "GET", f"/certificates/{created['id']}")
    ).status_code == 404


# ---------------------------------------------------------------------------
# Audit.
# ---------------------------------------------------------------------------
async def test_every_certificate_change_is_audited(org_user: OrgUser) -> None:
    """Create, update and delete each leave a row.

    ``docs/domain/compliance.md``'s aggregate note: *every certificate
    change is audited*. Task 13b shipped a router that audited money
    changes and not compliance ones, because the plan enumerated only the
    money reasons -- so this is asserted for all three verbs, not one.
    """
    created = await create_certificate(org_user)
    await as_user(org_user, "PATCH", f"/certificates/{created['id']}", {"certificate_ref": "X"})
    await as_user(org_user, "DELETE", f"/certificates/{created['id']}")

    actions = await audit_actions(org_user.org_id)
    for action in ["certificate.created", "certificate.updated", "certificate.deleted"]:
        assert action in actions, actions


async def test_the_delete_audit_records_what_was_removed(org_user: OrgUser) -> None:
    """``before`` holds the row; ``after`` is SQL NULL, not the JSON value null.

    "There was no new state" and "the new state is the JSON literal null"
    are different facts, and anything querying ``after is null`` can see
    the difference.
    """
    created = await create_certificate(org_user, certificate_ref="GS-GONE")
    await as_user(org_user, "DELETE", f"/certificates/{created['id']}")

    async with db() as conn:
        row = await conn.fetchrow(
            "select before, after from audit_log "
            "where org_id = $1 and action = 'certificate.deleted'",
            org_user.org_id,
        )
    assert "GS-GONE" in row["before"]
    assert row["after"] is None


# ---------------------------------------------------------------------------
# Tenant isolation.
#
# `DATABASE_URL` is the postgres superuser, so RLS is inert on this path.
# `0002_rls.sql`'s composite FKs do make a cross-org reference impossible at
# the database, so these tests are about the *status code* -- 404 rather
# than a 500 carrying an IntegrityError -- not about the leak itself.
# ---------------------------------------------------------------------------
async def test_a_certificate_cannot_be_created_against_another_orgs_property(
    make_org_user,
) -> None:
    """404, and nothing written."""
    alice = await make_org_user()
    bob = await make_org_user()
    bobs_property = await make_property(bob)

    resp = await as_user(
        alice,
        "POST",
        "/certificates",
        {
            "property_id": bobs_property,
            "certificate_type": "epc",
            "expiry_date": days(100),
        },
    )

    assert resp.status_code == 404, resp.text
    assert await rows("compliance_certificates", alice.org_id) == []


async def test_a_certificate_cannot_reference_another_orgs_document(make_org_user) -> None:
    """404 -- a document ref is a pointer out of the org if unchecked."""
    alice = await make_org_user()
    bob = await make_org_user()
    bobs_document = await make_document(bob)
    alices_property = await make_property(alice)

    resp = await as_user(
        alice,
        "POST",
        "/certificates",
        {
            "property_id": alices_property,
            "certificate_type": "epc",
            "expiry_date": days(100),
            "document_id": bobs_document,
        },
    )

    assert resp.status_code == 404, resp.text


async def test_a_certificate_cannot_be_moved_onto_another_orgs_property(
    make_org_user,
) -> None:
    """The move guard has to run before the write, not after."""
    alice = await make_org_user()
    bob = await make_org_user()
    created = await create_certificate(alice)
    bobs_property = await make_property(bob)

    resp = await as_user(
        alice, "PATCH", f"/certificates/{created['id']}", {"property_id": bobs_property}
    )

    assert resp.status_code == 404, resp.text
    stored = await rows("compliance_certificates", alice.org_id)
    assert str(stored[0]["property_id"]) == created["property_id"]


@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
async def test_another_orgs_certificate_is_not_found(make_org_user, method: str) -> None:
    """Reading, changing or deleting someone else's certificate is a 404."""
    alice = await make_org_user()
    bob = await make_org_user()
    bobs_certificate = await create_certificate(bob)

    resp = await as_user(
        alice,
        method,
        f"/certificates/{bobs_certificate['id']}",
        {"certificate_ref": "X"} if method == "PATCH" else None,
    )

    assert resp.status_code == 404, resp.text
    assert len(await rows("compliance_certificates", bob.org_id)) == 1


async def test_the_grouped_list_shows_only_the_callers_org(make_org_user) -> None:
    """Two orgs, one list each."""
    alice = await make_org_user()
    bob = await make_org_user()
    await create_certificate(alice, certificate_type="epc")
    await create_certificate(bob, certificate_type="eicr")

    resp = await call("GET", "/certificates", token=mint_token(alice.user_id))

    assert resp.status_code == 200, resp.text
    groups = resp.json()
    assert len(groups) == 1
    assert [c["certificate_type"] for c in groups[0]["certificates"]] == ["epc"]
