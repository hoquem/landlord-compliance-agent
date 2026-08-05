"""Tests for ``src.api.routers.dashboard``.

The dashboard answers one question -- *what is waiting on me?* -- and two of
its numbers are things a user acts on: how much review is outstanding, and
when the next quarterly update is due.

**The deadline is the one worth guarding hardest.** It is computed by
``core/quarters.py`` and rendered by the frontend, never recomputed there. A
second implementation in Dart would be a second opinion about a statutory
date, and the failure mode is filing late because the app said the wrong
day.
"""

import datetime
import uuid

import pytest

from src.core.certificates import uk_today
from src.core.quarters import next_update_deadline
from tests.api.conftest import OrgUser, as_user, call, db


async def seed_entity(org_user: OrgUser) -> str:
    async with db() as conn:
        return str(
            await conn.fetchval(
                "insert into entities (org_id, name, tax_regime) "
                "values ($1, 'Owner', 'mtd_itsa') returning id",
                org_user.org_id,
            )
        )


async def seed_transaction(org_user: OrgUser, entity_id: str, status: str) -> None:
    async with db() as conn:
        await conn.execute(
            "insert into transactions (org_id, entity_id, date, amount, direction, "
            "description, status) values ($1, $2, '2026-07-01', 10.00, 'in', 'X', "
            "$3::transaction_status)",
            org_user.org_id,
            uuid.UUID(entity_id),
            status,
        )


async def seed_certificate(org_user: OrgUser, days_from_today: int) -> None:
    async with db() as conn:
        property_id = await conn.fetchval(
            "insert into properties (org_id, address_line1, city, postcode, "
            "finance_cost_classification) values ($1, '1 Sample St', 'Luton', "
            "'LU1 1AA', 'residential') returning id",
            org_user.org_id,
        )
        await conn.execute(
            "insert into compliance_certificates (org_id, property_id, type, expiry_date) "
            "values ($1, $2, 'gas_safety', $3)",
            org_user.org_id,
            property_id,
            uk_today() + datetime.timedelta(days=days_from_today),
        )


async def dashboard(org_user: OrgUser) -> dict:
    resp = await as_user(org_user, "GET", "/dashboard")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_the_dashboard_needs_a_token() -> None:
    assert (await call("GET", "/dashboard")).status_code == 401


async def test_an_empty_org_has_nothing_waiting(org_user: OrgUser) -> None:
    body = await dashboard(org_user)

    assert body["needs_decision"] == 0
    assert body["expiring_certificates"] == 0
    assert body["unreadable_imports"] == 0
    assert body["uncategorised_imports"] == 0


async def test_proposed_lines_count_as_needing_a_decision(org_user: OrgUser) -> None:
    """An agent's suggestion nobody accepted is not a decision.

    ``export_pack.py`` refuses to export while a ``proposed`` line remains,
    so a dashboard that counted only ``unclassified`` would tell the user
    they were ready to file when they were not.
    """
    entity_id = await seed_entity(org_user)
    await seed_transaction(org_user, entity_id, "unclassified")
    await seed_transaction(org_user, entity_id, "proposed")
    await seed_transaction(org_user, entity_id, "confirmed")
    await seed_transaction(org_user, entity_id, "excluded")

    assert (await dashboard(org_user))["needs_decision"] == 2


async def test_the_deadline_is_the_one_from_core(org_user: OrgUser) -> None:
    """Rendered by the frontend, computed here, defined once.

    Compared against ``next_update_deadline`` itself rather than a hardcoded
    date, so this cannot drift from the four statutory dates its own unit
    tests pin.
    """
    body = await dashboard(org_user)

    assert body["next_deadline"] == next_update_deadline(uk_today()).isoformat()
    assert body["days_until_deadline"] == (
        next_update_deadline(uk_today()) - uk_today()
    ).days


async def test_the_deadline_is_always_one_of_the_four_statutory_dates(
    org_user: OrgUser,
) -> None:
    """7 Feb, 7 May, 7 Aug, 7 Nov -- the 7th after each quarter end."""
    body = await dashboard(org_user)
    deadline = datetime.date.fromisoformat(body["next_deadline"])

    assert (deadline.month, deadline.day) in {(2, 7), (5, 7), (8, 7), (11, 7)}


@pytest.mark.parametrize(
    ("days", "expiring", "expired"),
    [(-1, 0, 1), (10, 1, 0), (400, 0, 0)],
)
async def test_certificates_are_counted_by_derived_status(
    org_user: OrgUser, days: int, expiring: int, expired: int
) -> None:
    """Counted through ``certificate_status``, not a second SQL rule.

    Status is derived and never stored; counting it in SQL would be a second
    implementation of the expiry rule, free to disagree with the one the
    certificates screen shows.
    """
    await seed_certificate(org_user, days)

    body = await dashboard(org_user)

    assert body["expiring_certificates"] == expiring
    assert body["expired_certificates"] == expired


async def test_failed_imports_are_surfaced(org_user: OrgUser) -> None:
    """A dead import must not be invisible from the one screen you check.

    **Counted apart, because they are not the same problem.** A file that
    could not be *read* is bad input and the fix is a different export. A
    file that was read and then could not be *categorised* is our side
    falling over -- the data is fine and sitting there. Seen for real on
    2026-08-05: one of each at once, reported as "2 imports could not be
    read", which was half false and hid the actionable one.
    """
    entity_id = await seed_entity(org_user)
    async with db() as conn:
        for status in ("failed", "categorisation_failed", "parsed"):
            await conn.execute(
                "insert into imports (org_id, entity_id, file_path, source_bank, status) "
                "values ($1, $2, 'x.csv', 'generic', $3::import_status)",
                org_user.org_id,
                uuid.UUID(entity_id),
                status,
            )

    body = await dashboard(org_user)
    assert body["unreadable_imports"] == 1
    assert body["uncategorised_imports"] == 1


async def test_another_orgs_work_is_never_counted(make_org_user) -> None:
    """A leak here would overstate someone's outstanding work with a stranger's rows."""
    alice = await make_org_user()
    bob = await make_org_user()
    bobs_entity = await seed_entity(bob)
    await seed_transaction(bob, bobs_entity, "unclassified")
    await seed_certificate(bob, 10)

    body = await dashboard(alice)

    assert body["needs_decision"] == 0
    assert body["expiring_certificates"] == 0
