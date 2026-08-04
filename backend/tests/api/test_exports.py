"""Tests for ``src.api.routers.exports`` -- generating a quarter's return.

Runs against the live local Supabase stack, through the real mounted app,
and against the real ``exports`` bucket created by
``supabase/migrations/0004_exports_bucket.sql``::

    uv run --env-file ../.env pytest tests/api/test_exports.py

This is the endpoint that produces the figures a return is filed from, so
the tests worth reading first are the ones about **refusing** to produce
them.

*An unreviewed line anywhere in the org blocks every entity's export.* Not
just the exporting entity's own lines: an unclassified transaction has no
category and no property yet, so there is no way to know whose figures it
would land on. Once reviewed it might be allocated to a jointly-owned
property, which changes the totals of every co-owner. Narrowing the block
to ``transactions.entity_id`` would be narrowing on the one attribute that
does *not* decide attribution -- see ``cumulative_totals``, which splits by
ownership and uses ``entity_id`` only for lines with no property at all.

*Changed history behind a filed quarter is a 409, not a 422.* A 422 says
"fix your request"; nothing about this request is wrong. What conflicts is
previously filed state, and reconciling it is a decision about a tax return
that the user has to make outside this call.

*The version guard is the endpoint's, not the core's.* ``test_export_pack``
proves ``assert_history_intact`` works; only
``test_changed_history_behind_a_filed_quarter_is_refused`` proves this
router calls it. Delete the call and the core suite stays green.
"""

import datetime
import uuid
from decimal import Decimal

import httpx
import pytest

from tests.api.conftest import OrgUser, as_user, call, db


async def make_entity(
    org_user: OrgUser, name: str = "Owner", regime: str = "mtd_itsa"
) -> str:
    """Create an entity through the API and return its id.

    :param org_user: the owning caller.
    :param name: the entity's name.
    :param regime: ``mtd_itsa`` or ``corporation_tax``.
    :returns: the new entity's id.
    """
    resp = await as_user(org_user, "POST", "/entities", {"name": name, "tax_regime": regime})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def make_property_owned_by(org_user: OrgUser, entity_id: str) -> str:
    """Create a property wholly owned by ``entity_id``, through the API.

    :param org_user: the owning caller.
    :param entity_id: the sole owner.
    :returns: the new property's id.
    """
    resp = await as_user(
        org_user,
        "POST",
        "/properties",
        {
            "address_line1": "106 Sample Cres",
            "city": "Luton",
            "postcode": "LU1 1AA",
            "finance_cost_classification": "residential",
        },
    )
    assert resp.status_code == 201, resp.text
    property_id = resp.json()["id"]

    resp = await as_user(
        org_user,
        "PUT",
        f"/properties/{property_id}/ownership",
        [{"entity_id": entity_id, "percentage": "100.00"}],
    )
    assert resp.status_code == 200, resp.text
    return property_id


async def seed_transaction(
    org_user: OrgUser,
    *,
    entity_id: str,
    property_id: str | None = None,
    category: str | None = "rent_income",
    amount: str = "1200.00",
    direction: str = "in",
    when: str = "2026-05-01",
    status: str = "confirmed",
) -> str:
    """Insert one transaction directly, and return its id.

    Inserted rather than driven through import-then-confirm: this module is
    about what happens once transactions are already reviewed, and going
    through the upload path would make every test here depend on the parser
    and on statement storage being healthy.

    :param org_user: the owning caller.
    :param entity_id: the bank-account owner.
    :param property_id: the property the line is allocated to, if any.
    :param category: the confirmed HMRC category, or ``None``.
    :param amount: stored magnitude, always positive.
    :param direction: ``in`` or ``out``.
    :param when: transaction date.
    :param status: the review status.
    :returns: the new transaction's id.
    """
    async with db() as conn:
        return str(
            await conn.fetchval(
                "insert into transactions (org_id, entity_id, property_id, date, amount, "
                "direction, description, hmrc_category, status) "
                "values ($1, $2, $3, $4, $5, $6, 'SEEDED', $7::hmrc_category, "
                "$8::transaction_status) returning id",
                org_user.org_id,
                uuid.UUID(entity_id),
                uuid.UUID(property_id) if property_id else None,
                datetime.date.fromisoformat(when),
                Decimal(amount),
                direction,
                category,
                status,
            )
        )


async def export(
    org_user: OrgUser, entity_id: str, *, tax_year: int = 2026, quarter: int = 2
) -> httpx.Response:
    """Call ``POST /exports/quarter``.

    :param org_user: the caller.
    :param entity_id: the entity to export for.
    :param tax_year: calendar year the tax year starts in.
    :param quarter: 1-4.
    :returns: the raw response.
    """
    return await as_user(
        org_user,
        "POST",
        "/exports/quarter",
        {"entity_id": entity_id, "tax_year": tax_year, "quarter": quarter},
    )


async def rows(table: str, org_id: uuid.UUID) -> list[dict]:
    """Read an org's rows from ``table`` straight from the database.

    :param table: table name.
    :param org_id: the owning org.
    :returns: one dict per row, oldest first.
    """
    async with db() as conn:
        records = await conn.fetch(
            f"select * from {table} where org_id = $1 order by created_at", org_id
        )
    return [dict(r) for r in records]


async def seed_filed_quarter(
    org_user: OrgUser, entity_id: str, *, quarter: str = "Q1", rent_income: str = "1200.00"
) -> None:
    """Insert an ``mtd_quarters`` row as though a quarter had been filed.

    Written directly so a *stored* figure can be made to disagree with what
    today's transactions recompute to -- which is the whole condition
    :func:`~src.core.export_pack.assert_history_intact` exists to detect,
    and which the API cannot produce on its own.

    :param org_user: the owning caller.
    :param entity_id: the entity the quarter was filed for.
    :param quarter: the quarter label.
    :param rent_income: the rent income total as filed.
    """
    async with db() as conn:
        await conn.execute(
            "insert into mtd_quarters (org_id, entity_id, tax_year, quarter, version, "
            "rent_income_total) values ($1, $2, '2026-27', $3::mtd_quarter_number, 1, $4)",
            org_user.org_id,
            uuid.UUID(entity_id),
            quarter,
            Decimal(rent_income),
        )


# ---------------------------------------------------------------------------
# Auth -- every route.
# ---------------------------------------------------------------------------
async def test_the_export_route_requires_a_token() -> None:
    """No credentials must never reach the export logic."""
    resp = await call("POST", "/exports/quarter", json={})
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# The happy path.
# ---------------------------------------------------------------------------
async def test_exporting_a_quarter_files_the_totals_as_version_one(org_user: OrgUser) -> None:
    """A first export writes one ``mtd_quarters`` row holding the YTD figures."""
    entity_id = await make_entity(org_user)
    property_id = await make_property_owned_by(org_user, entity_id)
    await seed_transaction(org_user, entity_id=entity_id, property_id=property_id)
    await seed_transaction(
        org_user,
        entity_id=entity_id,
        property_id=property_id,
        category="repairs_maintenance",
        amount="84.99",
        direction="out",
        when="2026-08-01",
    )

    resp = await export(org_user, entity_id)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["tax_year"] == "2026-27"
    assert body["quarter"] == "Q2"
    assert body["version"] == 1

    filed = await rows("mtd_quarters", org_user.org_id)
    assert len(filed) == 1
    assert filed[0]["rent_income_total"] == Decimal("1200.00")
    assert filed[0]["repairs_maintenance_total"] == Decimal("84.99")
    # Every other column is stated as zero rather than left to its default.
    assert filed[0]["travel_vehicle_total"] == Decimal("0.00")
    assert str(filed[0]["id"]) == body["mtd_quarter_id"]


async def test_the_tax_year_is_written_in_hmrc_notation(org_user: OrgUser) -> None:
    """``2026-27``, not ``2026``.

    ``0001_core.sql`` CHECKs the format and row identity keys on the exact
    string, so an f-string here would be a constraint violation at best and
    a duplicate filed quarter at worst.
    """
    entity_id = await make_entity(org_user)

    resp = await export(org_user, entity_id, tax_year=2026, quarter=1)

    assert resp.status_code == 201, resp.text
    filed = await rows("mtd_quarters", org_user.org_id)
    assert filed[0]["tax_year"] == "2026-27"


async def test_a_contractor_refund_reduces_the_filed_repairs_total(org_user: OrgUser) -> None:
    """The sign rule survives the trip through the endpoint.

    ``test_export_pack`` proves :func:`~src.core.export_pack.signed_amount`;
    this proves the router hands it a magnitude and a direction rather than
    a pre-signed amount it invented itself.
    """
    entity_id = await make_entity(org_user)
    property_id = await make_property_owned_by(org_user, entity_id)
    await seed_transaction(
        org_user,
        entity_id=entity_id,
        property_id=property_id,
        category="repairs_maintenance",
        amount="500.00",
        direction="out",
    )
    await seed_transaction(
        org_user,
        entity_id=entity_id,
        property_id=property_id,
        category="repairs_maintenance",
        amount="200.00",
        direction="in",
        when="2026-08-01",
    )

    resp = await export(org_user, entity_id)

    assert resp.status_code == 201, resp.text
    filed = await rows("mtd_quarters", org_user.org_id)
    assert filed[0]["repairs_maintenance_total"] == Decimal("300.00")


async def test_re_exporting_the_same_quarter_adds_a_version(org_user: OrgUser) -> None:
    """Versions accumulate; nothing is overwritten.

    ``0001_core.sql`` keeps every export as its own row precisely so that
    what was filed on the day remains readable afterwards.
    """
    entity_id = await make_entity(org_user)
    property_id = await make_property_owned_by(org_user, entity_id)
    await seed_transaction(org_user, entity_id=entity_id, property_id=property_id)

    first = await export(org_user, entity_id)
    assert first.status_code == 201, first.text

    await seed_transaction(
        org_user,
        entity_id=entity_id,
        property_id=property_id,
        amount="300.00",
        when="2026-08-01",
    )
    second = await export(org_user, entity_id)

    assert second.status_code == 201, second.text
    assert second.json()["version"] == 2

    filed = sorted(await rows("mtd_quarters", org_user.org_id), key=lambda r: r["version"])
    assert [r["version"] for r in filed] == [1, 2]
    assert filed[0]["rent_income_total"] == Decimal("1200.00")
    assert filed[1]["rent_income_total"] == Decimal("1500.00")


# ---------------------------------------------------------------------------
# The generated files.
# ---------------------------------------------------------------------------
async def test_the_three_generated_files_are_recorded_as_documents(org_user: OrgUser) -> None:
    """Two CSVs and a PDF, each a ``documents`` row under the org's prefix."""
    entity_id = await make_entity(org_user)
    property_id = await make_property_owned_by(org_user, entity_id)
    await seed_transaction(org_user, entity_id=entity_id, property_id=property_id)

    resp = await export(org_user, entity_id)

    assert resp.status_code == 201, resp.text
    documents = resp.json()["documents"]
    assert {d["kind"] for d in documents} == {
        "export_category_csv",
        "export_property_csv",
        "export_pdf",
    }

    stored = await rows("documents", org_user.org_id)
    assert len(stored) == 3
    assert {str(d["id"]) for d in stored} == {d["id"] for d in documents}
    # The org prefix is what 0004_exports_bucket.sql's policies key on.
    assert all(d["storage_path"].startswith(f"{org_user.org_id}/") for d in stored)


async def test_the_filed_quarter_points_at_the_pdf(org_user: OrgUser) -> None:
    """``generated_document_id`` is a single FK and three files are produced.

    It points at the PDF -- the one artefact a person opens. The CSVs are
    reachable from the response, which is what "returns download refs"
    asks for.
    """
    entity_id = await make_entity(org_user)

    resp = await export(org_user, entity_id)

    assert resp.status_code == 201, resp.text
    pdf = next(d for d in resp.json()["documents"] if d["kind"] == "export_pdf")
    filed = await rows("mtd_quarters", org_user.org_id)
    assert str(filed[0]["generated_document_id"]) == pdf["id"]


async def test_the_stored_pdf_is_really_a_pdf(org_user: OrgUser) -> None:
    """Downloaded back out of storage, the object is a PDF.

    The end of the chain nothing else covers: WeasyPrint runs, the bytes
    reach the bucket, and the path recorded in ``documents`` finds them
    again.
    """
    import os

    entity_id = await make_entity(org_user)
    resp = await export(org_user, entity_id)
    assert resp.status_code == 201, resp.text

    pdf = next(d for d in resp.json()["documents"] if d["kind"] == "export_pdf")
    stored = await rows("documents", org_user.org_id)
    path = next(d["storage_path"] for d in stored if str(d["id"]) == pdf["id"])

    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    async with httpx.AsyncClient() as client:
        downloaded = await client.get(
            f"{os.environ['SUPABASE_URL']}/storage/v1/object/exports/{path}",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
        )
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content.startswith(b"%PDF")


# ---------------------------------------------------------------------------
# Refusals.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("blocking_status", ["unclassified", "proposed"])
async def test_an_unreviewed_line_blocks_the_export_and_is_named(
    org_user: OrgUser, blocking_status: str
) -> None:
    """422, naming the transaction to go and fix -- and nothing is written.

    ``proposed`` blocks as firmly as ``unclassified``: an agent's suggestion
    that no human accepted is not a decision.
    """
    entity_id = await make_entity(org_user)
    blocker = await seed_transaction(
        org_user, entity_id=entity_id, category=None, status=blocking_status
    )

    resp = await export(org_user, entity_id)

    assert resp.status_code == 422, resp.text
    assert blocker in resp.text
    assert await rows("mtd_quarters", org_user.org_id) == []
    assert await rows("documents", org_user.org_id) == []


async def test_another_entitys_unreviewed_line_blocks_too(org_user: OrgUser) -> None:
    """An unclassified line has no property yet, so it could land anywhere.

    Once reviewed it might be allocated to a jointly-owned property, which
    moves every co-owner's totals. Blocking only the exporting entity's own
    lines would filter on ``transactions.entity_id`` -- the one attribute
    that does not decide attribution.
    """
    exporting = await make_entity(org_user, name="Exporting")
    other = await make_entity(org_user, name="Someone else")
    await seed_transaction(org_user, entity_id=other, category=None, status="unclassified")

    resp = await export(org_user, exporting)

    assert resp.status_code == 422, resp.text


async def test_an_unreviewed_line_outside_the_window_does_not_block(
    org_user: OrgUser,
) -> None:
    """Q3's untouched inbox must not make Q2 unfileable forever."""
    entity_id = await make_entity(org_user)
    await seed_transaction(
        org_user, entity_id=entity_id, category=None, status="unclassified", when="2026-11-01"
    )

    resp = await export(org_user, entity_id, quarter=2)

    assert resp.status_code == 201, resp.text


async def test_changed_history_behind_a_filed_quarter_is_refused(org_user: OrgUser) -> None:
    """409, and nothing is filed.

    **This is the only test that proves the router calls
    ``assert_history_intact``.** Q1 was filed at 1200; today's data
    recomputes it to 500, so a transaction behind a filed period was edited
    or deleted. That is a conflict with state this request cannot fix, not
    a malformed request.
    """
    entity_id = await make_entity(org_user)
    property_id = await make_property_owned_by(org_user, entity_id)
    await seed_transaction(org_user, entity_id=entity_id, property_id=property_id, amount="500.00")
    await seed_filed_quarter(org_user, entity_id, quarter="Q1", rent_income="1200.00")

    resp = await export(org_user, entity_id, quarter=2)

    assert resp.status_code == 409, resp.text
    assert "Q1" in resp.text
    assert "rent_income" in resp.text
    # Only the seeded Q1 row; the refused Q2 export wrote nothing.
    assert len(await rows("mtd_quarters", org_user.org_id)) == 1
    assert await rows("documents", org_user.org_id) == []


async def test_a_refund_decrease_with_intact_history_still_exports(org_user: OrgUser) -> None:
    """A lawful decrease is not a history change, and must not be refused."""
    entity_id = await make_entity(org_user)
    property_id = await make_property_owned_by(org_user, entity_id)
    await seed_transaction(
        org_user,
        entity_id=entity_id,
        property_id=property_id,
        category="repairs_maintenance",
        amount="500.00",
        direction="out",
    )
    await seed_filed_quarter(org_user, entity_id, quarter="Q1", rent_income="0.00")
    # The seeded Q1 row states repairs as 0.00 but Q1 really holds 500 --
    # so file it truthfully first, then let the refund land in Q2.
    async with db() as conn:
        await conn.execute(
            "update mtd_quarters set repairs_maintenance_total = 500.00 where org_id = $1",
            org_user.org_id,
        )
    await seed_transaction(
        org_user,
        entity_id=entity_id,
        property_id=property_id,
        category="repairs_maintenance",
        amount="200.00",
        direction="in",
        when="2026-08-01",
    )

    resp = await export(org_user, entity_id, quarter=2)

    assert resp.status_code == 201, resp.text
    filed = [r for r in await rows("mtd_quarters", org_user.org_id) if r["quarter"] == "Q2"]
    assert filed[0]["repairs_maintenance_total"] == Decimal("300.00")


async def test_only_the_latest_version_of_a_filed_quarter_is_compared(
    org_user: OrgUser,
) -> None:
    """A superseded version must not be what history is checked against.

    Q1 was filed at 500, then re-filed at 1200 when a missing transaction
    turned up. Today's data recomputes Q1 to 1200 -- it agrees with the
    latest version, so Q2 exports. Comparing against version 1 would refuse
    a correct return.
    """
    entity_id = await make_entity(org_user)
    property_id = await make_property_owned_by(org_user, entity_id)
    await seed_transaction(org_user, entity_id=entity_id, property_id=property_id)
    await seed_filed_quarter(org_user, entity_id, quarter="Q1", rent_income="500.00")
    async with db() as conn:
        await conn.execute(
            "insert into mtd_quarters (org_id, entity_id, tax_year, quarter, version, "
            "rent_income_total) values ($1, $2, '2026-27', 'Q1', 2, 1200.00)",
            org_user.org_id,
            uuid.UUID(entity_id),
        )

    resp = await export(org_user, entity_id, quarter=2)

    assert resp.status_code == 201, resp.text


async def test_a_property_whose_ownership_no_longer_sums_to_100_is_refused(
    org_user: OrgUser,
) -> None:
    """422 naming the property, not a 500.

    Confirming refuses a short ownership set, but ownership can be edited
    afterwards -- so an export can meet one even though no confirm ever
    could.
    """
    entity_id = await make_entity(org_user)
    property_id = await make_property_owned_by(org_user, entity_id)
    await seed_transaction(org_user, entity_id=entity_id, property_id=property_id)
    async with db() as conn:
        await conn.execute(
            "update property_ownership set ownership_percentage = 50.00 where property_id = $1",
            uuid.UUID(property_id),
        )

    resp = await export(org_user, entity_id)

    assert resp.status_code == 422, resp.text
    assert property_id in resp.text


@pytest.mark.parametrize("quarter", [0, 5, -1])
async def test_a_quarter_outside_one_to_four_is_refused(
    org_user: OrgUser, quarter: int
) -> None:
    """Clamping would file a return against the wrong period."""
    entity_id = await make_entity(org_user)

    resp = await export(org_user, entity_id, quarter=quarter)

    assert resp.status_code == 422, resp.text


async def test_a_tax_year_outside_the_supported_range_is_refused(org_user: OrgUser) -> None:
    """``format_tax_year`` bounds the year; a 500 here would be a stack trace."""
    entity_id = await make_entity(org_user)

    resp = await export(org_user, entity_id, tax_year=1999)

    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Companies.
# ---------------------------------------------------------------------------
async def test_a_company_gets_files_but_no_filed_quarter(org_user: OrgUser) -> None:
    """Ltd entities are outside MTD ITSA, so no ``mtd_quarters`` row exists.

    Asserted on the table, not just the response: a row written and then
    left out of the body would look identical from outside.
    """
    entity_id = await make_entity(org_user, name="Sample Ltd", regime="corporation_tax")

    resp = await export(org_user, entity_id)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["version"] is None
    assert body["mtd_quarter_id"] is None
    assert len(body["documents"]) == 3
    assert await rows("mtd_quarters", org_user.org_id) == []


# ---------------------------------------------------------------------------
# Audit.
# ---------------------------------------------------------------------------
async def test_generating_an_export_is_audited(org_user: OrgUser) -> None:
    """Filing figures is a state change to money data, so it leaves a trail."""
    entity_id = await make_entity(org_user)

    resp = await export(org_user, entity_id)
    assert resp.status_code == 201, resp.text

    async with db() as conn:
        actions = [
            r["action"]
            for r in await conn.fetch(
                "select action from audit_log where org_id = $1", org_user.org_id
            )
        ]
    assert "export.generated" in actions


async def test_a_refused_export_leaves_no_audit_row(org_user: OrgUser) -> None:
    """An audit trail claiming an export happened when it did not is worse than none."""
    entity_id = await make_entity(org_user)
    await seed_transaction(org_user, entity_id=entity_id, category=None, status="unclassified")

    resp = await export(org_user, entity_id)
    assert resp.status_code == 422, resp.text

    async with db() as conn:
        actions = [
            r["action"]
            for r in await conn.fetch(
                "select action from audit_log where org_id = $1", org_user.org_id
            )
        ]
    assert "export.generated" not in actions


# ---------------------------------------------------------------------------
# Tenant isolation.
#
# `DATABASE_URL` is the postgres superuser, so RLS is inert on this path and
# the org filters in the router are the entire boundary.
# ---------------------------------------------------------------------------
async def test_another_orgs_entity_is_not_found(make_org_user) -> None:
    """Exporting someone else's entity is a 404, not an empty export."""
    alice = await make_org_user()
    bob = await make_org_user()
    bobs_entity = await make_entity(bob)

    resp = await export(alice, bobs_entity)

    assert resp.status_code == 404, resp.text
    assert await rows("mtd_quarters", bob.org_id) == []


async def test_another_orgs_transactions_never_reach_the_totals(make_org_user) -> None:
    """Two orgs, identical figures: one org's export must see only its own.

    The failure this guards is not a leak of ids but a leak of *money* --
    an overstated return, which looks entirely plausible.
    """
    alice = await make_org_user()
    bob = await make_org_user()
    alices_entity = await make_entity(alice)
    alices_property = await make_property_owned_by(alice, alices_entity)
    await seed_transaction(
        alice, entity_id=alices_entity, property_id=alices_property, amount="1000.00"
    )

    bobs_entity = await make_entity(bob)
    bobs_property = await make_property_owned_by(bob, bobs_entity)
    await seed_transaction(
        bob, entity_id=bobs_entity, property_id=bobs_property, amount="9999.00"
    )

    resp = await export(alice, alices_entity)

    assert resp.status_code == 201, resp.text
    filed = await rows("mtd_quarters", alice.org_id)
    assert filed[0]["rent_income_total"] == Decimal("1000.00")


async def test_a_property_with_no_ownership_at_all_is_refused(org_user: OrgUser) -> None:
    """422 naming the property, not a 500 and not a silent 100% assumption.

    A property is created before its ownership is set, so a transaction can
    be confirmed against one and the ownership rows deleted afterwards.
    Treating the missing map as sole ownership would misstate a tax figure,
    so the core raises and this is where that becomes an answer.
    """
    entity_id = await make_entity(org_user)
    property_id = await make_property_owned_by(org_user, entity_id)
    await seed_transaction(org_user, entity_id=entity_id, property_id=property_id)
    async with db() as conn:
        await conn.execute(
            "delete from property_ownership where property_id = $1", uuid.UUID(property_id)
        )

    resp = await export(org_user, entity_id)

    assert resp.status_code == 422, resp.text
    assert property_id in resp.text
    assert await rows("mtd_quarters", org_user.org_id) == []
