"""Tests for ``src.api.routers.transactions`` -- review, confirm, exclude.

This is the endpoint the review screen drives, and the last point at which a
human sees a figure before it reaches an export.

**The guard that matters most.** Confirming a transaction against a property
apportions its money by that property's ownership shares (HMRC PIM1035), and
``0001_core.sql:228-234`` deliberately does **not** enforce "shares sum to
100" in the database -- ownership is edited row by row, through transiently
invalid totals, so a DB-level sum check would fire mid-edit. The invariant is
therefore API-enforced only, and a property whose shares sum to 50 is
perfectly insertable. Confirming against one has to be refused *here*, or the
error surfaces much later as a silently short export.
"""

import uuid
from decimal import Decimal

import pytest

from tests.api.conftest import OrgUser, as_user, call, db


async def seed_transaction(
    org_user: OrgUser, *, entity_id: str, amount: str = "1200.00", direction: str = "in"
) -> str:
    """Insert one unclassified transaction directly, and return its id.

    Inserted rather than driven through ``POST /imports`` on purpose: this
    module is about what happens *after* an import, and going through the
    upload path would make every test here depend on the parser and on
    storage being healthy.

    :param org_user: the owning caller.
    :param entity_id: the entity the line is filed against.
    :param amount: stored magnitude.
    :param direction: ``in`` or ``out``.
    :returns: the new transaction's id.
    """
    async with db() as conn:
        return str(
            await conn.fetchval(
                "insert into transactions "
                "(org_id, entity_id, date, amount, direction, description, status) "
                "values ($1, $2, '2026-07-01', $3, $4, 'RENT 106 SAMPLE CRES', "
                "'unclassified') returning id",
                org_user.org_id,
                uuid.UUID(entity_id),
                Decimal(amount),
                direction,
            )
        )


async def make_entity(org_user: OrgUser, name: str = "Owner") -> str:
    """Create an entity through the API and return its id."""
    resp = await as_user(org_user, "POST", "/entities", {"name": name, "tax_regime": "mtd_itsa"})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def make_property(org_user: OrgUser) -> str:
    """Create a property through the API and return its id."""
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
    return resp.json()["id"]


async def set_ownership_directly(property_id: str, entity_id: str, percentage: str) -> None:
    """Write one ownership row straight to the database, bypassing the API.

    The API refuses a set that does not sum to 100, so a short set cannot be
    created through it -- but the database accepts one, because the sum rule
    is deliberately not a DB constraint. That gap is exactly what the
    confirm-time guard exists to cover, so reaching it requires writing the
    row directly.
    """
    async with db() as conn:
        org_id = await conn.fetchval("select org_id from properties where id = $1", uuid.UUID(property_id))
        await conn.execute(
            "insert into property_ownership (org_id, property_id, entity_id, ownership_percentage) "
            "values ($1, $2, $3, $4)",
            org_id,
            uuid.UUID(property_id),
            uuid.UUID(entity_id),
            Decimal(percentage),
        )


async def audit_actions(org_id: uuid.UUID) -> list[str]:
    """Return an org's audit actions, oldest first."""
    async with db() as conn:
        records = await conn.fetch(
            "select action from audit_log where org_id = $1 order by created_at, action", org_id
        )
    return [r["action"] for r in records]


async def txn_row(txn_id: str) -> dict:
    """Read one transaction straight from the database."""
    async with db() as conn:
        return dict(await conn.fetchrow("select * from transactions where id = $1", uuid.UUID(txn_id)))


# ---------------------------------------------------------------------------
# Auth -- every route.
# ---------------------------------------------------------------------------
def transaction_routes() -> list[tuple[str, str]]:
    """Enumerate every ``(method, path)`` the transactions router exposes."""
    from src.api.routers import transactions as router_module

    out: list[tuple[str, str]] = []
    for route in router_module.router.routes:
        path = route.path.replace("{transaction_id}", str(uuid.uuid4()))
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            out.append((method, path))
    return out


@pytest.mark.parametrize(("method", "path"), transaction_routes())
async def test_every_transaction_route_requires_authentication(method: str, path: str) -> None:
    resp = await call(method, path)
    assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"


# ---------------------------------------------------------------------------
# Listing.
# ---------------------------------------------------------------------------
async def test_list_returns_the_callers_transactions(org_user: OrgUser) -> None:
    entity = await make_entity(org_user)
    await seed_transaction(org_user, entity_id=entity)

    resp = await as_user(org_user, "GET", "/transactions")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["status"] == "unclassified"
    assert body[0]["hmrc_category"] is None
    assert body[0]["confidence"] is None


async def test_list_filters_by_status(org_user: OrgUser) -> None:
    entity = await make_entity(org_user)
    keep = await seed_transaction(org_user, entity_id=entity)
    other = await seed_transaction(org_user, entity_id=entity)
    resp = await as_user(org_user, "POST", f"/transactions/{other}/exclude")
    assert resp.status_code == 200, resp.text

    resp = await as_user(org_user, "GET", "/transactions", params={"status": "unclassified"})
    assert resp.status_code == 200, resp.text
    assert [row["id"] for row in resp.json()] == [keep]


async def test_org_a_cannot_list_org_bs_transactions(make_org_user) -> None:
    org_a = await make_org_user()
    org_b = await make_org_user()
    await seed_transaction(org_b, entity_id=await make_entity(org_b))

    resp = await as_user(org_a, "GET", "/transactions")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []

    resp = await as_user(org_b, "GET", "/transactions")
    assert len(resp.json()) == 1, "positive control: org B sees its own"


# ---------------------------------------------------------------------------
# Confirm.
# ---------------------------------------------------------------------------
async def test_confirm_sets_category_and_writes_an_audit_row(org_user: OrgUser) -> None:
    entity = await make_entity(org_user)
    prop = await make_property(org_user)
    resp = await as_user(
        org_user, "PUT", f"/properties/{prop}/ownership", [{"entity_id": entity, "percentage": "100"}]
    )
    assert resp.status_code == 200, resp.text
    txn = await seed_transaction(org_user, entity_id=entity)

    resp = await as_user(
        org_user,
        "POST",
        f"/transactions/{txn}/confirm",
        {"hmrc_category": "rent_income", "property_id": prop},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "confirmed"
    assert resp.json()["hmrc_category"] == "rent_income"

    stored = await txn_row(txn)
    assert stored["status"] == "confirmed"
    assert stored["hmrc_category"] == "rent_income"
    assert str(stored["property_id"]) == prop
    assert "transaction.confirmed" in await audit_actions(org_user.org_id)


async def test_confirm_without_a_property_is_allowed(org_user: OrgUser) -> None:
    """Some allowable costs are not attributable to one property.

    ``use_of_home_allowance`` is the plan's own example, so property_id has
    to stay optional rather than being quietly required.
    """
    entity = await make_entity(org_user)
    txn = await seed_transaction(org_user, entity_id=entity, direction="out")

    resp = await as_user(
        org_user, "POST", f"/transactions/{txn}/confirm", {"hmrc_category": "use_of_home_allowance"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["property_id"] is None


async def test_confirm_against_a_property_whose_shares_do_not_sum_to_100_is_422(
    org_user: OrgUser,
) -> None:
    """The money-critical guard, and the reason this endpoint is not plain CRUD.

    The database accepts a 50%-only ownership set by design, so this is
    reachable in practice. Refusing at confirm time is what stops it
    surfacing later as a silently short export.
    """
    entity = await make_entity(org_user)
    prop = await make_property(org_user)
    await set_ownership_directly(prop, entity, "50.00")
    txn = await seed_transaction(org_user, entity_id=entity)

    resp = await as_user(
        org_user,
        "POST",
        f"/transactions/{txn}/confirm",
        {"hmrc_category": "rent_income", "property_id": prop},
    )
    assert resp.status_code == 422, resp.text
    assert "100" in resp.text, "the refusal must name the rule it applied"
    assert "50" in resp.text, "and the total it actually found"

    stored = await txn_row(txn)
    assert stored["status"] == "unclassified", "a refused confirm must change nothing"
    assert "transaction.confirmed" not in await audit_actions(org_user.org_id)


async def test_confirm_with_another_orgs_property_is_404(make_org_user) -> None:
    org_a = await make_org_user()
    org_b = await make_org_user()
    b_prop = await make_property(org_b)
    txn = await seed_transaction(org_a, entity_id=await make_entity(org_a))

    resp = await as_user(
        org_a,
        "POST",
        f"/transactions/{txn}/confirm",
        {"hmrc_category": "rent_income", "property_id": b_prop},
    )
    assert resp.status_code == 404, resp.text
    assert (await txn_row(txn))["status"] == "unclassified"


async def test_confirm_another_orgs_transaction_is_404(make_org_user) -> None:
    org_a = await make_org_user()
    org_b = await make_org_user()
    b_txn = await seed_transaction(org_b, entity_id=await make_entity(org_b))

    resp = await as_user(
        org_a, "POST", f"/transactions/{b_txn}/confirm", {"hmrc_category": "rent_income"}
    )
    assert resp.status_code == 404, resp.text
    assert (await txn_row(b_txn))["status"] == "unclassified"


# ---------------------------------------------------------------------------
# Exclude.
# ---------------------------------------------------------------------------
async def test_exclude_marks_the_line_and_audits_it(org_user: OrgUser) -> None:
    entity = await make_entity(org_user)
    txn = await seed_transaction(org_user, entity_id=entity, direction="out")

    resp = await as_user(org_user, "POST", f"/transactions/{txn}/exclude")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "excluded"
    assert (await txn_row(txn))["status"] == "excluded"
    assert "transaction.excluded" in await audit_actions(org_user.org_id)


# ---------------------------------------------------------------------------
# Batch confirm -- all or nothing.
# ---------------------------------------------------------------------------
async def test_confirm_batch_confirms_every_line(org_user: OrgUser) -> None:
    entity = await make_entity(org_user)
    one = await seed_transaction(org_user, entity_id=entity)
    two = await seed_transaction(org_user, entity_id=entity)

    resp = await as_user(
        org_user,
        "POST",
        "/transactions/confirm-batch",
        {
            "items": [
                {"transaction_id": one, "hmrc_category": "rent_income"},
                {"transaction_id": two, "hmrc_category": "other_property_income"},
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    assert {row["status"] for row in resp.json()} == {"confirmed"}
    assert (await txn_row(one))["status"] == "confirmed"
    assert (await txn_row(two))["status"] == "confirmed"


async def test_confirm_batch_is_all_or_nothing(org_user: OrgUser) -> None:
    """One bad line must leave every other line in the batch untouched.

    A partially applied batch is the worst outcome available here: the user
    believes they confirmed a screenful, and some of it silently did not
    take.
    """
    entity = await make_entity(org_user)
    good = await seed_transaction(org_user, entity_id=entity)
    prop = await make_property(org_user)
    await set_ownership_directly(prop, entity, "50.00")
    bad = await seed_transaction(org_user, entity_id=entity)

    resp = await as_user(
        org_user,
        "POST",
        "/transactions/confirm-batch",
        {
            "items": [
                {"transaction_id": good, "hmrc_category": "rent_income"},
                {"transaction_id": bad, "hmrc_category": "rent_income", "property_id": prop},
            ]
        },
    )
    assert resp.status_code == 422, resp.text

    assert (await txn_row(good))["status"] == "unclassified", "the good line must not have landed"
    assert (await txn_row(bad))["status"] == "unclassified"
    assert "transaction.confirmed" not in await audit_actions(org_user.org_id)


async def test_confirm_batch_refuses_another_orgs_transaction(make_org_user) -> None:
    org_a = await make_org_user()
    org_b = await make_org_user()
    a_txn = await seed_transaction(org_a, entity_id=await make_entity(org_a))
    b_txn = await seed_transaction(org_b, entity_id=await make_entity(org_b))

    resp = await as_user(
        org_a,
        "POST",
        "/transactions/confirm-batch",
        {
            "items": [
                {"transaction_id": a_txn, "hmrc_category": "rent_income"},
                {"transaction_id": b_txn, "hmrc_category": "rent_income"},
            ]
        },
    )
    assert resp.status_code == 404, resp.text
    assert (await txn_row(a_txn))["status"] == "unclassified", "batch is atomic across orgs too"
    assert (await txn_row(b_txn))["status"] == "unclassified"
