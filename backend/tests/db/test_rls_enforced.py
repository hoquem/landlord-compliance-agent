"""Proof that row-level security is actually in force on the API's connection.

**This file is the point of the least-privilege roles.** Everything else in
that change is plumbing; this is the guarantee.

Until 2026-08-06 the API connected as the `postgres` superuser, which bypasses
RLS. The policies in ``0002_rls.sql`` existed, were correct, and protected
nothing on any path the product uses -- they were written for the
direct-from-Flutter PostgREST route, and the app never calls PostgREST. So the
manual ``org_id`` filter at every query site was not the first of two defences
but the only one, and a single forgotten ``where`` clause was one customer
reading another's tax return.

The tests below therefore do the thing a bug would do: **query without the
filter**, through the same session factory the routers use, and assert the
database refuses anyway. A test that only checked filtered queries would pass
just as happily with RLS switched off, which is precisely how this went
unnoticed for the life of the project.

:seealso: supabase/migrations/0006_least_privilege_roles.sql;
    backend/src/db/session.py.
"""

import datetime
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from src.db.models import Property, Transaction
from src.db.session import async_session_factory, org_session
from tests.api.conftest import OrgUser, db


async def seed_property(org_user: OrgUser, line1: str) -> str:
    """Insert one property straight into an org, bypassing the API."""
    async with db() as conn:
        return str(
            await conn.fetchval(
                "insert into properties (org_id, address_line1, city, postcode, "
                "finance_cost_classification) values ($1, $2, 'Luton', 'LU1 1AA', "
                "'residential') returning id",
                org_user.org_id,
                line1,
            )
        )


async def test_an_unfiltered_query_still_sees_only_one_org(
    make_org_user,
) -> None:
    """**The whole guarantee, in one test.**

    ``select * from properties`` with no ``where`` at all -- the shape a
    forgotten filter produces. Two orgs have rows; the caller sees one.
    """
    alice = await make_org_user()
    bob = await make_org_user()
    await seed_property(alice, "1 Alice Street")
    await seed_property(bob, "2 Bob Street")

    async with org_session(alice.user_id) as session:
        rows = list(await session.scalars(select(Property)))

    assert [r.address_line1 for r in rows] == ["1 Alice Street"]


async def test_the_superuser_would_have_seen_both(make_org_user) -> None:
    """The control, without which the test above proves nothing.

    If the two orgs' rows were not both there, "saw one org" would be
    indistinguishable from "the other org had no rows". This is the assertion
    that makes the previous test mean something.
    """
    alice = await make_org_user()
    bob = await make_org_user()
    await seed_property(alice, "1 Alice Street")
    await seed_property(bob, "2 Bob Street")

    async with async_session_factory() as session:
        rows = list(await session.scalars(select(Property)))

    seen = {r.address_line1 for r in rows}
    assert {"1 Alice Street", "2 Bob Street"} <= seen


async def test_a_session_with_no_claim_sees_nothing(make_org_user) -> None:
    """Fail closed: no identity means no data, never all data.

    ``org_session`` always sets a claim, so this reaches past it to the raw
    factory -- the state a future refactor could leave a connection in.
    """
    alice = await make_org_user()
    await seed_property(alice, "1 Alice Street")

    from src.db.session import api_session_factory

    async with api_session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(Property))

    assert count == 0


async def test_another_orgs_row_cannot_be_read_by_id(make_org_user) -> None:
    """Not even when the id is known exactly.

    ``get_owned_or_404`` filters by org, but this asks what happens if it
    ever stopped: the row is invisible, so the 404 becomes structural rather
    than a matter of remembering.
    """
    alice = await make_org_user()
    bob = await make_org_user()
    bobs_property = await seed_property(bob, "2 Bob Street")

    async with org_session(alice.user_id) as session:
        found = await session.scalar(
            select(Property).where(Property.id == uuid.UUID(bobs_property))
        )

    assert found is None


async def test_a_write_into_another_org_is_refused(make_org_user) -> None:
    """The ``with check`` half of the policy, which is easy to forget exists.

    Reading is only half a tenant boundary. Without this, a caller could
    create rows *inside* another org -- inventing transactions on someone
    else's tax return rather than merely reading them.

    **The row is otherwise completely valid**, and that matters. The first
    version of this test used a string where a date belongs and a made-up
    entity id, so the insert failed on a type error before ever reaching a
    policy -- it would have passed with RLS switched off, which is the failure
    this whole file exists to catch. Everything here is real except the org.
    """
    alice = await make_org_user()
    bob = await make_org_user()
    async with db() as conn:
        bobs_entity = await conn.fetchval(
            "insert into entities (org_id, name, tax_regime) "
            "values ($1, 'Bob Ltd', 'mtd_itsa') returning id",
            bob.org_id,
        )

    with pytest.raises(Exception) as caught:
        async with org_session(alice.user_id) as session:
            session.add(
                Transaction(
                    org_id=bob.org_id,
                    entity_id=bobs_entity,
                    date=datetime.date(2026, 5, 1),
                    amount=Decimal("10.00"),
                    direction="in",
                    description="PLANTED BY ANOTHER ORG",
                    status="unclassified",
                )
            )
            await session.commit()

    assert "row-level security" in str(caught.value).lower(), (
        f"refused, but not by a policy: {caught.value}"
    )

    async with db() as conn:
        planted = await conn.fetchval(
            "select count(*) from transactions where description = $1",
            "PLANTED BY ANOTHER ORG",
        )
    assert planted == 0


async def test_the_claim_does_not_survive_into_the_next_session(
    make_org_user,
) -> None:
    """**The pooled-connection leak this design exists to avoid.**

    ``set_config(..., true)`` is transaction-local, so a connection returned
    to the pool cannot carry one request's org into the next request's. Making
    it session-local would work in every test that ran one request at a time
    and leak in production.
    """
    alice = await make_org_user()
    await seed_property(alice, "1 Alice Street")

    async with org_session(alice.user_id) as session:
        assert await session.scalar(select(func.count()).select_from(Property)) == 1

    from src.db.session import api_session_factory

    async with api_session_factory() as session:
        leaked = await session.scalar(
            text("select current_setting('request.jwt.claims', true)")
        )

    assert not leaked, "a pooled connection carried the previous caller's org"
