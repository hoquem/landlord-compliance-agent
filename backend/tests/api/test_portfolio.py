"""Tests for ``src.api.routers.portfolio`` -- entities, properties, ownership.

Runs against the live local Supabase stack (``supabase start`` from the repo
root), through the real mounted app (``src.api.main:app``), with real
Supabase-shaped JWTs::

    uv run --env-file ../.env pytest tests/api/

The fixtures and helpers used here live in ``tests/api/conftest.py``:
``make_org_user`` (a factory, because tenant-isolation tests need two orgs
in one test), ``org_user``, ``mint_token``, and the RLS-bypassing ``db()``
connection used to arrange and assert DB state.

**Why the tenant-isolation tests matter more than the rest of this file.**
``src/db/session.py`` connects as the ``postgres`` superuser, which bypasses
row level security -- so the RLS policies of ``0002_rls.sql`` are inert on
every query this router makes. The only thing keeping org A out of org B's
data is the explicit ``org_id`` filter in each statement, and a forgotten
one is a silent cross-tenant leak. Hence
``test_org_a_cannot_*``/``..._is_404``: two orgs, two real tokens, asserted
through the API rather than at the DB level.
"""

import uuid
from decimal import Decimal

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.api.routers import portfolio
from tests.api.conftest import OrgUser, db, mint_token

# ---------------------------------------------------------------------------
# Request helpers.
# ---------------------------------------------------------------------------


async def call(
    method: str, path: str, *, token: str | None = None, json: object = None
) -> httpx.Response:
    """Make one in-process request against the real app.

    :param method: HTTP method.
    :param path: request path.
    :param token: bearer credentials to send, or ``None`` to send no
        ``Authorization`` header at all.
    :param json: JSON body to send, or ``None`` for no body.
    :returns: the raw response.
    """
    headers = {"Authorization": f"Bearer {token}"} if token is not None else None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        return await client.request(method, path, headers=headers, json=json)


async def as_user(
    org_user: OrgUser, method: str, path: str, json: object = None
) -> httpx.Response:
    """Make one request authenticated as ``org_user``.

    :param org_user: the caller.
    :param method: HTTP method.
    :param path: request path.
    :param json: JSON body to send, or ``None`` for no body.
    :returns: the raw response.
    """
    return await call(method, path, token=mint_token(org_user.user_id), json=json)


def entity_body(**overrides: object) -> dict:
    """Build a valid ``POST /entities`` body.

    :param overrides: fields to replace or add.
    :returns: the request body.
    """
    return {"name": "Test Owner", "tax_regime": "mtd_itsa"} | overrides


def property_body(**overrides: object) -> dict:
    """Build a valid ``POST /properties`` body.

    :param overrides: fields to replace or add.
    :returns: the request body.
    """
    return {
        "address_line1": "1 Test Street",
        "city": "Luton",
        "postcode": "LU1 1AA",
        "finance_cost_classification": "residential",
    } | overrides


async def create_entity(org_user: OrgUser, **overrides: object) -> dict:
    """Create an entity through the API, asserting it worked.

    :param org_user: the owning caller.
    :param overrides: body fields to replace or add.
    :returns: the created entity as returned by the API.
    """
    resp = await as_user(org_user, "POST", "/entities", entity_body(**overrides))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def create_property(org_user: OrgUser, **overrides: object) -> dict:
    """Create a property through the API, asserting it worked.

    :param org_user: the owning caller.
    :param overrides: body fields to replace or add.
    :returns: the created property as returned by the API.
    """
    resp = await as_user(org_user, "POST", "/properties", property_body(**overrides))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def ownership_rows(property_id: str) -> list[dict]:
    """Read a property's ownership rows straight from the database.

    :param property_id: the property whose rows to read.
    :returns: one dict per row, ordered by entity id, carrying the row's
        ``id`` (so a test can prove rows were *not* rewritten),
        ``entity_id`` and ``ownership_percentage``.
    """
    async with db() as conn:
        records = await conn.fetch(
            "select id, entity_id, ownership_percentage, org_id from property_ownership "
            "where property_id = $1 order by entity_id",
            uuid.UUID(property_id),
        )
    return [dict(record) for record in records]


async def audit_rows(org_id: uuid.UUID) -> list[dict]:
    """Read an org's audit rows, oldest first.

    :param org_id: the org whose audit trail to read.
    :returns: one dict per ``audit_log`` row.
    """
    async with db() as conn:
        records = await conn.fetch(
            "select actor_type, actor_id, action, before, after from audit_log "
            "where org_id = $1 order by created_at, action",
            org_id,
        )
    return [dict(record) for record in records]


# ---------------------------------------------------------------------------
# Authentication -- every route, no exceptions.
# ---------------------------------------------------------------------------
def portfolio_routes() -> list[tuple[str, str]]:
    """Enumerate every ``(method, path)`` the portfolio router exposes.

    Path parameters are filled with a random UUID: these requests are meant
    to be refused before anything looks them up.

    :returns: one ``(method, concrete path)`` pair per route/method.
    """
    pairs = []
    for route in portfolio.router.routes:
        path = route.path.replace("{entity_id}", str(uuid.uuid4())).replace(
            "{property_id}", str(uuid.uuid4())
        )
        pairs.extend((method, path) for method in sorted(route.methods))
    return pairs


@pytest.mark.parametrize(("method", "path"), portfolio_routes())
async def test_every_portfolio_route_requires_authentication(method: str, path: str) -> None:
    """No route may be reachable without a bearer token.

    Parametrised over the router's own route table rather than a hand-kept
    list, so a route added later without ``auth: CurrentAuth`` fails here
    instead of shipping as an unauthenticated hole.
    """
    resp = await call(method, path, json=[] if method == "PUT" else {})
    assert resp.status_code == 401, f"{method} {path} -> {resp.status_code} {resp.text}"


# ---------------------------------------------------------------------------
# Entities.
# ---------------------------------------------------------------------------
async def test_create_entity_returns_it_scoped_to_the_callers_org(org_user: OrgUser) -> None:
    """A created entity comes back with the caller's org and the schema's defaults."""
    entity = await create_entity(org_user, name="Hoque Properties Ltd", tax_regime="corporation_tax")

    assert entity["org_id"] == str(org_user.org_id)
    assert entity["name"] == "Hoque Properties Ltd"
    assert entity["tax_regime"] == "corporation_tax"
    assert entity["quarter_basis"] == "tax_year", "the schema default must be reflected back"
    assert entity["prs_registration_number"] is None
    assert entity["prs_registered_at"] is None

    async with db() as conn:
        row = await conn.fetchrow(
            "select org_id, name, quarter_basis from entities where id = $1",
            uuid.UUID(entity["id"]),
        )
    assert row["org_id"] == org_user.org_id
    assert row["name"] == "Hoque Properties Ltd"
    assert row["quarter_basis"] == "tax_year"


async def test_create_entity_accepts_quarter_basis_and_prs_fields(org_user: OrgUser) -> None:
    """The optional entity fields are all writable on create."""
    entity = await create_entity(
        org_user,
        quarter_basis="calendar_election",
        prs_registration_number="PRS-12345",
        prs_registered_at="2026-04-06",
    )

    assert entity["quarter_basis"] == "calendar_election"
    assert entity["prs_registration_number"] == "PRS-12345"
    assert entity["prs_registered_at"] == "2026-04-06"


async def test_create_entity_rejects_unknown_tax_regime(org_user: OrgUser) -> None:
    """An enum value the database doesn't have must 422, not reach the DB as a 500."""
    resp = await as_user(org_user, "POST", "/entities", entity_body(tax_regime="income_tax"))
    assert resp.status_code == 422, resp.text


async def test_create_entity_rejects_unknown_field(org_user: OrgUser) -> None:
    """A misspelled or unsupported field must be refused, not silently dropped."""
    resp = await as_user(org_user, "POST", "/entities", entity_body(tax_regine="mtd_itsa"))
    assert resp.status_code == 422, resp.text


async def test_create_entity_writes_an_audit_row(org_user: OrgUser) -> None:
    """Creating an entity is a state change to money-affecting reference data."""
    entity = await create_entity(org_user)

    rows = await audit_rows(org_user.org_id)
    assert len(rows) == 1, rows
    assert rows[0]["actor_type"] == "user"
    assert rows[0]["actor_id"] == org_user.user_id
    assert rows[0]["action"] == "entity.created"
    assert rows[0]["before"] is None
    assert entity["id"] in rows[0]["after"]


async def test_list_entities_returns_only_the_callers_org(make_org_user) -> None:
    """Org A's list must not contain org B's entities."""
    org_a = await make_org_user()
    org_b = await make_org_user()
    mine = await create_entity(org_a, name="Mine")
    theirs = await create_entity(org_b, name="Theirs")

    resp = await as_user(org_a, "GET", "/entities")
    assert resp.status_code == 200, resp.text
    ids = [entity["id"] for entity in resp.json()]
    assert ids == [mine["id"]], f"expected only org A's entity, got {resp.json()}"
    assert theirs["id"] not in ids


async def test_get_entity_returns_it(org_user: OrgUser) -> None:
    """A single entity is readable by id within the caller's org."""
    entity = await create_entity(org_user)
    resp = await as_user(org_user, "GET", f"/entities/{entity['id']}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == entity


async def test_get_unknown_entity_is_404(org_user: OrgUser) -> None:
    """An id that exists nowhere is a 404, not a 500."""
    resp = await as_user(org_user, "GET", f"/entities/{uuid.uuid4()}")
    assert resp.status_code == 404, resp.text


async def test_patch_entity_updates_only_the_named_fields(org_user: OrgUser) -> None:
    """PATCH is partial: absent fields keep their stored values."""
    entity = await create_entity(org_user, name="Before", prs_registration_number="PRS-1")

    resp = await as_user(
        org_user, "PATCH", f"/entities/{entity['id']}", {"name": "After", "quarter_basis": "calendar_election"}
    )
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["name"] == "After"
    assert updated["quarter_basis"] == "calendar_election"
    assert updated["tax_regime"] == entity["tax_regime"], "untouched field must be unchanged"
    assert updated["prs_registration_number"] == "PRS-1", "untouched field must be unchanged"


async def test_patch_entity_writes_an_audit_row_with_before_and_after(org_user: OrgUser) -> None:
    """An entity update records what it changed from as well as to."""
    entity = await create_entity(org_user, name="Before")
    resp = await as_user(org_user, "PATCH", f"/entities/{entity['id']}", {"name": "After"})
    assert resp.status_code == 200, resp.text

    rows = await audit_rows(org_user.org_id)
    updates = [row for row in rows if row["action"] == "entity.updated"]
    assert len(updates) == 1, rows
    assert "Before" in updates[0]["before"]
    assert "After" in updates[0]["after"]
    assert updates[0]["actor_id"] == org_user.user_id


async def test_patch_entity_with_no_fields_is_422(org_user: OrgUser) -> None:
    """An empty patch is a client mistake, not a no-op 200."""
    entity = await create_entity(org_user)
    resp = await as_user(org_user, "PATCH", f"/entities/{entity['id']}", {})
    assert resp.status_code == 422, resp.text


async def test_patch_entity_cannot_null_a_field(org_user: OrgUser) -> None:
    """Explicit ``null`` is refused loudly rather than silently ignored or 500-ing.

    ``entities.name`` is NOT NULL, so a swallowed null would either be
    dropped without telling the caller or reach the database as an
    IntegrityError. MVP has no "clear this field" operation; asking for one
    gets a 422 that says so.
    """
    entity = await create_entity(org_user)
    resp = await as_user(org_user, "PATCH", f"/entities/{entity['id']}", {"name": None})
    assert resp.status_code == 422, resp.text


async def test_patch_unknown_entity_is_404(org_user: OrgUser) -> None:
    """Patching an id that exists nowhere is a 404, not a silent 200."""
    resp = await as_user(org_user, "PATCH", f"/entities/{uuid.uuid4()}", {"name": "X"})
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Properties.
# ---------------------------------------------------------------------------
async def test_create_property_returns_it_scoped_to_the_callers_org(org_user: OrgUser) -> None:
    """A created property comes back with the caller's org and the schema's defaults."""
    created = await create_property(org_user)

    assert created["org_id"] == str(org_user.org_id)
    assert created["address_line1"] == "1 Test Street"
    assert created["address_line2"] is None
    assert created["country"] == "GB", "the schema default must be reflected back"
    assert created["licensing_flag"] is False, "the schema default must be reflected back"
    assert created["epc_rating"] is None
    assert created["epc_expiry"] is None
    assert created["bedroom_count"] is None

    async with db() as conn:
        row = await conn.fetchrow(
            "select org_id, country, licensing_flag from properties where id = $1",
            uuid.UUID(created["id"]),
        )
    assert row["org_id"] == org_user.org_id
    assert row["country"] == "GB"
    assert row["licensing_flag"] is False


async def test_create_property_accepts_epc_bedrooms_and_licensing(org_user: OrgUser) -> None:
    """The optional property fields are all writable on create."""
    created = await create_property(
        org_user,
        address_line2="Flat 3",
        country="GB",
        finance_cost_classification="non_residential",
        epc_rating="C",
        epc_expiry="2031-05-04",
        bedroom_count=4,
        licensing_flag=True,
    )

    assert created["address_line2"] == "Flat 3"
    assert created["finance_cost_classification"] == "non_residential"
    assert created["epc_rating"] == "C"
    assert created["epc_expiry"] == "2031-05-04"
    assert created["bedroom_count"] == 4
    assert created["licensing_flag"] is True


async def test_create_property_rejects_unknown_finance_cost_classification(
    org_user: OrgUser,
) -> None:
    """An enum value the database doesn't have must 422, not reach the DB as a 500."""
    resp = await as_user(
        org_user, "POST", "/properties", property_body(finance_cost_classification="mixed")
    )
    assert resp.status_code == 422, resp.text


async def test_create_property_rejects_negative_bedroom_count(org_user: OrgUser) -> None:
    """A negative bedroom count is nonsense the API should refuse."""
    resp = await as_user(org_user, "POST", "/properties", property_body(bedroom_count=-1))
    assert resp.status_code == 422, resp.text


async def test_list_properties_returns_only_the_callers_org(make_org_user) -> None:
    """Org A's list must not contain org B's properties."""
    org_a = await make_org_user()
    org_b = await make_org_user()
    mine = await create_property(org_a, address_line1="Mine")
    theirs = await create_property(org_b, address_line1="Theirs")

    resp = await as_user(org_a, "GET", "/properties")
    assert resp.status_code == 200, resp.text
    ids = [prop["id"] for prop in resp.json()]
    assert ids == [mine["id"]], f"expected only org A's property, got {resp.json()}"
    assert theirs["id"] not in ids


async def test_get_property_returns_it(org_user: OrgUser) -> None:
    """A single property is readable by id within the caller's org."""
    created = await create_property(org_user)
    resp = await as_user(org_user, "GET", f"/properties/{created['id']}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == created


async def test_get_unknown_property_is_404(org_user: OrgUser) -> None:
    """An id that exists nowhere is a 404, not a 500."""
    resp = await as_user(org_user, "GET", f"/properties/{uuid.uuid4()}")
    assert resp.status_code == 404, resp.text


async def test_patch_property_updates_only_the_named_fields(org_user: OrgUser) -> None:
    """PATCH is partial: absent fields keep their stored values."""
    created = await create_property(org_user, epc_rating="E", bedroom_count=2)

    resp = await as_user(
        org_user,
        "PATCH",
        f"/properties/{created['id']}",
        {"epc_rating": "B", "epc_expiry": "2035-01-31", "finance_cost_classification": "non_residential"},
    )
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["epc_rating"] == "B"
    assert updated["epc_expiry"] == "2035-01-31"
    assert updated["finance_cost_classification"] == "non_residential"
    assert updated["bedroom_count"] == 2, "untouched field must be unchanged"
    assert updated["address_line1"] == created["address_line1"], "untouched field must be unchanged"


async def test_patch_property_with_no_fields_is_422(org_user: OrgUser) -> None:
    """An empty patch is a client mistake, not a no-op 200."""
    created = await create_property(org_user)
    resp = await as_user(org_user, "PATCH", f"/properties/{created['id']}", {})
    assert resp.status_code == 422, resp.text


async def test_patch_unknown_property_is_404(org_user: OrgUser) -> None:
    """Patching an id that exists nowhere is a 404, not a silent 200."""
    resp = await as_user(org_user, "PATCH", f"/properties/{uuid.uuid4()}", {"epc_rating": "A"})
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Ownership -- the money-critical one.
# ---------------------------------------------------------------------------
async def test_put_ownership_writes_the_whole_set(org_user: OrgUser) -> None:
    """A valid set of shares summing to 100 is stored against the property."""
    prop = await create_property(org_user)
    first = await create_entity(org_user, name="First")
    second = await create_entity(org_user, name="Second")

    resp = await as_user(
        org_user,
        "PUT",
        f"/properties/{prop['id']}/ownership",
        [
            {"entity_id": first["id"], "percentage": "60.00"},
            {"entity_id": second["id"], "percentage": "40.00"},
        ],
    )
    assert resp.status_code == 200, resp.text
    returned = {share["entity_id"]: Decimal(share["percentage"]) for share in resp.json()}
    assert returned == {first["id"]: Decimal("60.00"), second["id"]: Decimal("40.00")}
    assert all(share["property_id"] == prop["id"] for share in resp.json())
    assert all(share["org_id"] == str(org_user.org_id) for share in resp.json())

    stored = await ownership_rows(prop["id"])
    assert {str(row["entity_id"]): row["ownership_percentage"] for row in stored} == {
        first["id"]: Decimal("60.00"),
        second["id"]: Decimal("40.00"),
    }
    assert all(row["org_id"] == org_user.org_id for row in stored)


async def test_put_ownership_accepts_a_sole_owner_at_100(org_user: OrgUser) -> None:
    """The common case: one entity owning the whole property."""
    prop = await create_property(org_user)
    entity = await create_entity(org_user)

    resp = await as_user(
        org_user,
        "PUT",
        f"/properties/{prop['id']}/ownership",
        [{"entity_id": entity["id"], "percentage": 100}],
    )
    assert resp.status_code == 200, resp.text
    assert [Decimal(share["percentage"]) for share in resp.json()] == [Decimal(100)]


async def test_put_ownership_sums_json_numbers_exactly(org_user: OrgUser) -> None:
    """Thirds sent as JSON numbers must sum to exactly 100 -- Decimal, never float.

    ``33.33 + 33.33 + 33.34`` is 100 in decimal and 99.99999999999999 in
    binary floating point. This figure drives penny-exact apportionment in
    ``src/core/splits.py``, so the parse has to be exact: pinned here
    because the JSON-number path (what the Flutter client will send) is the
    one where a float slip would hide.
    """
    prop = await create_property(org_user)
    entities = [await create_entity(org_user, name=f"Third {index}") for index in range(3)]

    resp = await as_user(
        org_user,
        "PUT",
        f"/properties/{prop['id']}/ownership",
        [
            {"entity_id": entities[0]["id"], "percentage": 33.33},
            {"entity_id": entities[1]["id"], "percentage": 33.33},
            {"entity_id": entities[2]["id"], "percentage": 33.34},
        ],
    )
    assert resp.status_code == 200, resp.text
    assert sum(Decimal(share["percentage"]) for share in resp.json()) == Decimal(100)


async def test_put_ownership_replaces_the_prior_set_wholesale(org_user: OrgUser) -> None:
    """A second PUT replaces every prior row rather than merging into them."""
    prop = await create_property(org_user)
    old_owner = await create_entity(org_user, name="Old")
    new_owner = await create_entity(org_user, name="New")

    resp = await as_user(
        org_user,
        "PUT",
        f"/properties/{prop['id']}/ownership",
        [{"entity_id": old_owner["id"], "percentage": "100.00"}],
    )
    assert resp.status_code == 200, resp.text
    before = await ownership_rows(prop["id"])

    resp = await as_user(
        org_user,
        "PUT",
        f"/properties/{prop['id']}/ownership",
        [
            {"entity_id": old_owner["id"], "percentage": "25.00"},
            {"entity_id": new_owner["id"], "percentage": "75.00"},
        ],
    )
    assert resp.status_code == 200, resp.text

    after = await ownership_rows(prop["id"])
    assert {str(row["entity_id"]): row["ownership_percentage"] for row in after} == {
        old_owner["id"]: Decimal("25.00"),
        new_owner["id"]: Decimal("75.00"),
    }
    assert {row["id"] for row in before}.isdisjoint({row["id"] for row in after}), (
        "replacement must delete the prior rows, not update them in place"
    )


async def test_put_ownership_writes_an_audit_row_with_before_and_after(org_user: OrgUser) -> None:
    """Ownership percentages change money computation, so every replacement is audited."""
    prop = await create_property(org_user)
    first = await create_entity(org_user, name="First")
    second = await create_entity(org_user, name="Second")

    resp = await as_user(
        org_user,
        "PUT",
        f"/properties/{prop['id']}/ownership",
        [{"entity_id": first["id"], "percentage": "100.00"}],
    )
    assert resp.status_code == 200, resp.text
    resp = await as_user(
        org_user,
        "PUT",
        f"/properties/{prop['id']}/ownership",
        [
            {"entity_id": first["id"], "percentage": "50.00"},
            {"entity_id": second["id"], "percentage": "50.00"},
        ],
    )
    assert resp.status_code == 200, resp.text

    replacements = [
        row for row in await audit_rows(org_user.org_id) if row["action"] == "ownership.replaced"
    ]
    assert len(replacements) == 2, replacements
    assert replacements[0]["before"] == "[]", "the first replacement started from no owners"
    assert "100.00" in replacements[0]["after"]
    assert "100.00" in replacements[1]["before"], "the second must record what it replaced"
    assert replacements[1]["after"].count("50.00") == 2
    assert all(row["actor_id"] == org_user.user_id for row in replacements)
    assert all(row["actor_type"] == "user" for row in replacements)


@pytest.mark.parametrize(
    ("percentages", "expected_sum"),
    [(["50.00", "40.00"], "90"), (["60.00", "60.00"], "120"), (["99.99"], "99.99")],
)
async def test_put_ownership_rejects_shares_that_do_not_sum_to_100(
    org_user: OrgUser, percentages: list[str], expected_sum: str
) -> None:
    """Not summing to exactly 100 is a 422 that names the sum it got."""
    prop = await create_property(org_user)
    shares = [
        {
            "entity_id": (await create_entity(org_user, name=f"Owner {index}"))["id"],
            "percentage": percentage,
        }
        for index, percentage in enumerate(percentages)
    ]

    resp = await as_user(org_user, "PUT", f"/properties/{prop['id']}/ownership", shares)
    assert resp.status_code == 422, resp.text
    assert expected_sum in resp.text, f"the 422 must name the sum it got: {resp.text}"
    assert await ownership_rows(prop["id"]) == []


async def test_put_ownership_rejects_the_same_entity_twice(org_user: OrgUser) -> None:
    """A duplicate entity is a 422 naming it -- not a unique-constraint 500.

    ``uq_property_ownership_property_entity`` would reject the second row at
    the database, which is the right backstop but the wrong error for a
    client to receive.
    """
    prop = await create_property(org_user)
    entity = await create_entity(org_user)

    resp = await as_user(
        org_user,
        "PUT",
        f"/properties/{prop['id']}/ownership",
        [
            {"entity_id": entity["id"], "percentage": "50.00"},
            {"entity_id": entity["id"], "percentage": "50.00"},
        ],
    )
    assert resp.status_code == 422, resp.text
    assert entity["id"] in resp.text, f"the 422 must name the duplicated entity: {resp.text}"
    assert await ownership_rows(prop["id"]) == []


@pytest.mark.parametrize("percentage", ["0.00", "-10.00", "100.01", "33.333"])
async def test_put_ownership_rejects_out_of_range_percentages(
    org_user: OrgUser, percentage: str
) -> None:
    """Per-row bounds are enforced in the API, ahead of the CHECK constraint.

    ``ownership_percentage numeric(5,2) check (> 0 and <= 100)`` means 0,
    negatives and >100 die at the database as a 500; a third decimal place
    would be silently rounded into a different number. All four are 422s.
    """
    prop = await create_property(org_user)
    entity = await create_entity(org_user)

    resp = await as_user(
        org_user,
        "PUT",
        f"/properties/{prop['id']}/ownership",
        [{"entity_id": entity["id"], "percentage": percentage}],
    )
    assert resp.status_code == 422, resp.text
    assert await ownership_rows(prop["id"]) == []


async def test_put_ownership_rejects_an_empty_list(org_user: OrgUser) -> None:
    """Removing every owner would leave the property's income unattributable."""
    prop = await create_property(org_user)
    resp = await as_user(org_user, "PUT", f"/properties/{prop['id']}/ownership", [])
    assert resp.status_code == 422, resp.text


async def test_put_ownership_rejects_an_unknown_entity_id(org_user: OrgUser) -> None:
    """An entity id that exists nowhere is a 422 naming it, not a foreign-key 500."""
    prop = await create_property(org_user)
    missing = str(uuid.uuid4())

    resp = await as_user(
        org_user,
        "PUT",
        f"/properties/{prop['id']}/ownership",
        [{"entity_id": missing, "percentage": "100.00"}],
    )
    assert resp.status_code == 422, resp.text
    assert missing in resp.text, f"the 422 must name the unusable entity: {resp.text}"


async def test_put_ownership_on_unknown_property_is_404(org_user: OrgUser) -> None:
    """The property has to exist before its ownership can be set."""
    entity = await create_entity(org_user)
    resp = await as_user(
        org_user,
        "PUT",
        f"/properties/{uuid.uuid4()}/ownership",
        [{"entity_id": entity["id"], "percentage": "100.00"}],
    )
    assert resp.status_code == 404, resp.text


async def test_rejected_ownership_payloads_leave_the_prior_set_untouched(
    make_org_user,
) -> None:
    """Every rejection path must be atomic: the stored rows, ids included, stay as they were.

    Delete-then-insert in one transaction is what makes this true. A partial
    replacement would silently corrupt the split that
    ``src/core/splits.py`` apportions money by, so each bad payload is
    checked against the row ids, not just the percentages -- a
    delete-and-reinsert of the same values would otherwise pass unnoticed.
    """
    org_a = await make_org_user()
    org_b = await make_org_user()
    prop = await create_property(org_a)
    owner = await create_entity(org_a, name="Established owner")
    other = await create_entity(org_a, name="Other")
    foreign = await create_entity(org_b, name="Org B entity")

    resp = await as_user(
        org_a,
        "PUT",
        f"/properties/{prop['id']}/ownership",
        [{"entity_id": owner["id"], "percentage": "100.00"}],
    )
    assert resp.status_code == 200, resp.text
    established = await ownership_rows(prop["id"])

    rejected_payloads = [
        [
            {"entity_id": owner["id"], "percentage": "50.00"},
            {"entity_id": other["id"], "percentage": "40.00"},
        ],
        [
            {"entity_id": other["id"], "percentage": "50.00"},
            {"entity_id": other["id"], "percentage": "50.00"},
        ],
        [{"entity_id": str(uuid.uuid4()), "percentage": "100.00"}],
        [{"entity_id": foreign["id"], "percentage": "100.00"}],
        [{"entity_id": other["id"], "percentage": "0.00"}],
        [],
    ]
    for payload in rejected_payloads:
        resp = await as_user(org_a, "PUT", f"/properties/{prop['id']}/ownership", payload)
        assert resp.status_code == 422, f"{payload} -> {resp.status_code} {resp.text}"
        assert await ownership_rows(prop["id"]) == established, (
            f"rejected payload changed stored ownership: {payload}"
        )


# ---------------------------------------------------------------------------
# Tenant isolation. RLS is inert here (src/db/session.py connects as the
# superuser), so these tests are the only thing standing between a forgotten
# org_id filter and a silent cross-tenant leak.
# ---------------------------------------------------------------------------
async def test_org_a_cannot_read_org_bs_entity(make_org_user) -> None:
    """A cross-org GET must 404 -- 403 would confirm the row exists."""
    org_a = await make_org_user()
    org_b = await make_org_user()
    theirs = await create_entity(org_b, name="Org B entity")

    resp = await as_user(org_a, "GET", f"/entities/{theirs['id']}")
    assert resp.status_code == 404, resp.text
    assert "Org B entity" not in resp.text, "a 404 must not leak the row's contents"


async def test_org_a_cannot_read_org_bs_property(make_org_user) -> None:
    """A cross-org GET must 404 -- 403 would confirm the row exists."""
    org_a = await make_org_user()
    org_b = await make_org_user()
    theirs = await create_property(org_b, address_line1="Org B address")

    resp = await as_user(org_a, "GET", f"/properties/{theirs['id']}")
    assert resp.status_code == 404, resp.text
    assert "Org B address" not in resp.text, "a 404 must not leak the row's contents"


async def test_org_a_cannot_patch_org_bs_entity(make_org_user) -> None:
    """A cross-org PATCH must 404 loudly and leave the row exactly as it was."""
    org_a = await make_org_user()
    org_b = await make_org_user()
    theirs = await create_entity(org_b, name="Org B entity")

    resp = await as_user(org_a, "PATCH", f"/entities/{theirs['id']}", {"name": "Hijacked"})
    assert resp.status_code == 404, resp.text

    async with db() as conn:
        name = await conn.fetchval(
            "select name from entities where id = $1", uuid.UUID(theirs["id"])
        )
    assert name == "Org B entity"
    assert [row["action"] for row in await audit_rows(org_b.org_id)] == ["entity.created"], (
        "a refused cross-org PATCH must not write an audit row"
    )


async def test_org_a_cannot_patch_org_bs_property(make_org_user) -> None:
    """A cross-org PATCH must 404 loudly and leave the row exactly as it was."""
    org_a = await make_org_user()
    org_b = await make_org_user()
    theirs = await create_property(org_b, address_line1="Org B address")

    resp = await as_user(
        org_a, "PATCH", f"/properties/{theirs['id']}", {"address_line1": "Hijacked"}
    )
    assert resp.status_code == 404, resp.text

    async with db() as conn:
        address = await conn.fetchval(
            "select address_line1 from properties where id = $1", uuid.UUID(theirs["id"])
        )
    assert address == "Org B address"


async def test_org_a_cannot_set_ownership_on_org_bs_property(make_org_user) -> None:
    """A cross-org ownership PUT must 404 and write nothing at all."""
    org_a = await make_org_user()
    org_b = await make_org_user()
    theirs = await create_property(org_b)
    mine = await create_entity(org_a, name="Org A entity")

    resp = await as_user(
        org_a,
        "PUT",
        f"/properties/{theirs['id']}/ownership",
        [{"entity_id": mine["id"], "percentage": "100.00"}],
    )
    assert resp.status_code == 404, resp.text
    assert await ownership_rows(theirs["id"]) == []


async def test_org_a_cannot_attach_org_bs_entity_to_its_own_property(make_org_user) -> None:
    """An entity from another org is unusable, and indistinguishable from a missing one.

    The composite FK ``fk_property_ownership_entity_org`` would refuse this
    write at the database (a 500), so the API has to catch it first -- and
    the 422 has to look exactly like the unknown-entity one, or the
    difference in wording tells org A that org B's entity id exists.
    """
    org_a = await make_org_user()
    org_b = await make_org_user()
    prop = await create_property(org_a)
    foreign = await create_entity(org_b, name="Org B entity")

    resp = await as_user(
        org_a,
        "PUT",
        f"/properties/{prop['id']}/ownership",
        [{"entity_id": foreign["id"], "percentage": "100.00"}],
    )
    assert resp.status_code == 422, resp.text
    assert foreign["id"] in resp.text
    assert "Org B entity" not in resp.text, "a rejection must not leak the other org's data"
    assert await ownership_rows(prop["id"]) == []

    unknown = str(uuid.uuid4())
    unknown_resp = await as_user(
        org_a,
        "PUT",
        f"/properties/{prop['id']}/ownership",
        [{"entity_id": unknown, "percentage": "100.00"}],
    )
    assert unknown_resp.status_code == 422
    assert unknown_resp.text.replace(unknown, "X") == resp.text.replace(foreign["id"], "X"), (
        "a cross-org entity and a non-existent one must be reported identically"
    )
