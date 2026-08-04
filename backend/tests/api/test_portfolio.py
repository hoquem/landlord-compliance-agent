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
``src/db/session.py`` connects as the ``postgres`` superuser (as
``DATABASE_URL`` is configured), which bypasses row level security -- so the
RLS policies of ``0002_rls.sql`` are inert on every query this router makes.
The only thing keeping org A out of org B's data is the ``org_id`` filter in
each statement -- written out in the router, or applied by
``src.api.scoping.get_owned_or_404`` for the four "one row by id" lookups --
and a forgotten one is a silent cross-tenant leak. Hence
``test_org_a_cannot_*``/``..._is_404``: two orgs, two real tokens, asserted
through the API rather than at the DB level.

These tests have teeth, checked rather than assumed: making
``get_owned_or_404`` ignore ``auth.org_id`` fails
``test_org_a_cannot_read_org_bs_entity``, ``..._property``,
``test_org_a_cannot_patch_org_bs_entity``, ``..._property`` and all four
cases of ``test_a_cross_org_404_is_identical_to_a_nonexistent_one`` -- eight
in all. It does **not** fail
``test_org_a_cannot_set_ownership_on_org_bs_property``: that 404 comes from
the ownership handler's own existence probe, which the helper was
deliberately not given. Probed separately, and it does have teeth --
rewording *that* probe's 404 fails it and nothing else.

``scripts/seed_org.py``'s idempotency tests are in the final section of this
module rather than a file of their own: the script drives the same app-level
engine (so it needs this directory's autouse ``_dispose_app_engine``) and the
same live stack, orgs and users, so it wants the same fixtures.
"""

import uuid
from decimal import Decimal

import pytest

from scripts.seed_org import seed_org
from src.api.routers import portfolio
from src.core.splits import split_amount
from src.db import models
from tests.api.conftest import (
    AuthUser,
    OrgUser,
    as_user,
    assert_not_nullable_matches_schema,
    call,
    db,
)

# ---------------------------------------------------------------------------
# Request helpers.
# ---------------------------------------------------------------------------


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


# ``test_patch_entity_cannot_null_a_field`` stood here. It sent
# ``{"name": null}`` and asserted a 422 -- which
# ``test_patch_entity_cannot_null_a_not_null_column[name]`` now does
# identically, alongside ``tax_regime`` and ``quarter_basis``, and it also
# asserts the 422 names the field. Removed rather than left as a duplicate
# carrying a docstring ("MVP has no 'clear this field' operation") that Step 5
# made false. Coverage strictly increased.


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


async def test_create_property_writes_an_audit_row(org_user: OrgUser) -> None:
    """Creating a property is a state change to money and compliance data.

    ``finance_cost_classification`` decides which side of the Section 24
    finance-cost restriction a property's interest falls (money), and
    ``epc_rating``/``epc_expiry``/``licensing_flag`` are compliance data --
    both inside the spec's "audit every state change to money or compliance
    data".
    """
    created = await create_property(org_user, epc_rating="C")

    creations = [
        row for row in await audit_rows(org_user.org_id) if row["action"] == "property.created"
    ]
    assert len(creations) == 1, creations
    assert creations[0]["actor_type"] == "user"
    assert creations[0]["actor_id"] == org_user.user_id
    assert creations[0]["before"] is None, "a creation has no prior state"
    assert created["id"] in creations[0]["after"]
    assert '"finance_cost_classification": "residential"' in creations[0]["after"]
    assert '"epc_rating": "C"' in creations[0]["after"]


async def test_patch_property_writes_an_audit_row_with_before_and_after(
    org_user: OrgUser,
) -> None:
    """A property update records what it changed from as well as to."""
    created = await create_property(
        org_user, epc_rating="E", finance_cost_classification="residential"
    )
    resp = await as_user(
        org_user,
        "PATCH",
        f"/properties/{created['id']}",
        {"epc_rating": "B", "finance_cost_classification": "non_residential"},
    )
    assert resp.status_code == 200, resp.text

    updates = [
        row for row in await audit_rows(org_user.org_id) if row["action"] == "property.updated"
    ]
    assert len(updates) == 1, updates
    assert updates[0]["actor_type"] == "user"
    assert updates[0]["actor_id"] == org_user.user_id
    assert '"epc_rating": "E"' in updates[0]["before"]
    assert '"epc_rating": "B"' in updates[0]["after"]
    assert '"finance_cost_classification": "residential"' in updates[0]["before"]
    assert '"finance_cost_classification": "non_residential"' in updates[0]["after"]


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
# Clearing nullable fields -- RFC 7386 (JSON Merge Patch) semantics.
#
# Three states have to stay distinguishable per field: key absent leaves the
# stored value alone, key present and null clears it, key present with a value
# sets it. Every clearable field is covered separately rather than one field
# standing in for the rest, because the failure this guards against is a
# per-field omission -- `bedroom_count` was itself missed when Step 5 was
# first specified.
#
# A mis-entered EPC expiry is a compliance problem, so "correctable but never
# clearable" was not a tenable API: short of direct database access the wrong
# value was stuck.
# ---------------------------------------------------------------------------

#: The nullable ``properties`` columns, with a sample value for each.
CLEARABLE_PROPERTY_FIELDS = [
    ("address_line2", "Flat 2"),
    ("epc_rating", "C"),
    ("epc_expiry", "2031-05-04"),
    ("bedroom_count", 3),
]

#: The nullable ``entities`` columns, with a sample value for each.
CLEARABLE_ENTITY_FIELDS = [
    ("prs_registration_number", "PRS-123456"),
    ("prs_registered_at", "2025-04-06"),
]

#: ``properties`` columns that are NOT NULL: nulling them stays a 422.
NOT_NULL_PROPERTY_FIELDS = [
    "address_line1",
    "city",
    "postcode",
    "country",
    "finance_cost_classification",
    "licensing_flag",
]

#: ``entities`` columns that are NOT NULL: nulling them stays a 422.
NOT_NULL_ENTITY_FIELDS = ["name", "tax_regime", "quarter_basis"]


@pytest.mark.parametrize(("field", "value"), CLEARABLE_PROPERTY_FIELDS)
async def test_patch_property_clears_a_nullable_field_when_explicitly_null(
    org_user: OrgUser, field: str, value: object
) -> None:
    """An explicit ``null`` wipes a nullable property field."""
    created = await create_property(org_user, **{field: value})
    assert created[field] is not None, "arrange failed: the field must start set"

    resp = await as_user(org_user, "PATCH", f"/properties/{created['id']}", {field: None})

    assert resp.status_code == 200, resp.text
    assert resp.json()[field] is None
    async with db() as conn:
        stored = await conn.fetchval(
            f"select {field} from properties where id = $1", uuid.UUID(created["id"])
        )
    assert stored is None, "the response must not claim a clear the database did not make"


@pytest.mark.parametrize(("field", "value"), CLEARABLE_PROPERTY_FIELDS)
async def test_patch_property_leaves_a_nullable_field_alone_when_omitted(
    org_user: OrgUser, field: str, value: object
) -> None:
    """Omitting the key is not the same as sending null: the value survives."""
    created = await create_property(org_user, **{field: value})

    resp = await as_user(
        org_user, "PATCH", f"/properties/{created['id']}", {"address_line1": "Somewhere Else"}
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()[field] == created[field]


@pytest.mark.parametrize(("field", "value"), CLEARABLE_PROPERTY_FIELDS)
async def test_patch_property_sets_a_nullable_field_to_a_value(
    org_user: OrgUser, field: str, value: object
) -> None:
    """A cleared field can be filled in again -- clearing is not one-way."""
    created = await create_property(org_user)
    assert created[field] is None, "arrange failed: the field must start unset"

    resp = await as_user(org_user, "PATCH", f"/properties/{created['id']}", {field: value})

    assert resp.status_code == 200, resp.text
    assert resp.json()[field] == value


@pytest.mark.parametrize(("field", "value"), CLEARABLE_ENTITY_FIELDS)
async def test_patch_entity_clears_a_nullable_field_when_explicitly_null(
    org_user: OrgUser, field: str, value: object
) -> None:
    """The entity body is not assumed symmetric with the property one -- checked."""
    created = await create_entity(org_user, **{field: value})
    assert created[field] is not None, "arrange failed: the field must start set"

    resp = await as_user(org_user, "PATCH", f"/entities/{created['id']}", {field: None})

    assert resp.status_code == 200, resp.text
    assert resp.json()[field] is None
    async with db() as conn:
        stored = await conn.fetchval(
            f"select {field} from entities where id = $1", uuid.UUID(created["id"])
        )
    assert stored is None, "the response must not claim a clear the database did not make"


@pytest.mark.parametrize(("field", "value"), CLEARABLE_ENTITY_FIELDS)
async def test_patch_entity_leaves_a_nullable_field_alone_when_omitted(
    org_user: OrgUser, field: str, value: object
) -> None:
    """Omitting the key leaves the entity's stored value alone."""
    created = await create_entity(org_user, **{field: value})

    resp = await as_user(org_user, "PATCH", f"/entities/{created['id']}", {"name": "Renamed"})

    assert resp.status_code == 200, resp.text
    assert resp.json()[field] == created[field]


@pytest.mark.parametrize(("field", "value"), CLEARABLE_ENTITY_FIELDS)
async def test_patch_entity_sets_a_nullable_field_to_a_value(
    org_user: OrgUser, field: str, value: object
) -> None:
    """A cleared entity field can be filled in again."""
    created = await create_entity(org_user)
    assert created[field] is None, "arrange failed: the field must start unset"

    resp = await as_user(org_user, "PATCH", f"/entities/{created['id']}", {field: value})

    assert resp.status_code == 200, resp.text
    assert resp.json()[field] == value


async def test_clearing_a_property_field_writes_an_audit_row_showing_the_null(
    org_user: OrgUser,
) -> None:
    """Clearing compliance data is audited, and the null is visible in ``after``.

    The cleared key must be *present and null* rather than omitted: a reader
    reconstructing state from the trail cannot tell an omitted key from an
    unchanged one.
    """
    created = await create_property(org_user, epc_expiry="2031-05-04")

    resp = await as_user(org_user, "PATCH", f"/properties/{created['id']}", {"epc_expiry": None})
    assert resp.status_code == 200, resp.text

    updates = [
        row for row in await audit_rows(org_user.org_id) if row["action"] == "property.updated"
    ]
    assert len(updates) == 1, updates
    assert '"epc_expiry": "2031-05-04"' in updates[0]["before"]
    assert '"epc_expiry": null' in updates[0]["after"]


async def test_clearing_an_entity_field_writes_an_audit_row_showing_the_null(
    org_user: OrgUser,
) -> None:
    """Clearing a PRS registration is audited the same way."""
    created = await create_entity(org_user, prs_registration_number="PRS-123456")

    resp = await as_user(
        org_user, "PATCH", f"/entities/{created['id']}", {"prs_registration_number": None}
    )
    assert resp.status_code == 200, resp.text

    updates = [
        row for row in await audit_rows(org_user.org_id) if row["action"] == "entity.updated"
    ]
    assert len(updates) == 1, updates
    assert '"prs_registration_number": "PRS-123456"' in updates[0]["before"]
    assert '"prs_registration_number": null' in updates[0]["after"]


@pytest.mark.parametrize(
    ("body", "model"),
    [
        (portfolio.EntityUpdate, models.Entity),
        (portfolio.PropertyUpdate, models.Property),
    ],
    ids=["entity", "property"],
)
async def test_not_nullable_is_exactly_what_the_schema_says(
    body: type[portfolio._PatchBody], model: type
) -> None:
    """``_NOT_NULLABLE`` is derived-checked against the mapped columns.

    The check itself lives in ``tests/api/conftest.py`` -- see
    :func:`~tests.api.conftest.assert_not_nullable_matches_schema` for what
    it closes and why it keys on the mapper rather than the table. It moved
    there in Task 17, when a second router needed the same authority and a
    divergent copy would have been the one that stopped being updated.
    """
    assert_not_nullable_matches_schema(body, model)


@pytest.mark.parametrize("field", NOT_NULL_PROPERTY_FIELDS)
async def test_patch_property_cannot_null_a_not_null_column(
    org_user: OrgUser, field: str
) -> None:
    """Null still refused where the column is NOT NULL -- a 422, never a 500.

    This is what stops "null clears it" from becoming "null reaches Postgres
    as an IntegrityError". Every NOT NULL column is named individually,
    because the *pydantic body* cannot infer nullability: each field is
    uniformly typed ``X | None`` so the API can express "absent". The mapped
    column does know, and
    ``test_not_nullable_is_exactly_what_the_schema_says`` is what holds the
    two in agreement -- these per-name cases only pin the names that are
    already in the set.
    """
    created = await create_property(org_user)
    resp = await as_user(org_user, "PATCH", f"/properties/{created['id']}", {field: None})
    assert resp.status_code == 422, resp.text
    assert field in resp.text, "the 422 must name the field it refused"


@pytest.mark.parametrize("field", NOT_NULL_ENTITY_FIELDS)
async def test_patch_entity_cannot_null_a_not_null_column(org_user: OrgUser, field: str) -> None:
    """Null still refused on the entity's NOT NULL columns."""
    created = await create_entity(org_user)
    resp = await as_user(org_user, "PATCH", f"/entities/{created['id']}", {field: None})
    assert resp.status_code == 422, resp.text
    assert field in resp.text, "the 422 must name the field it refused"


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
    """The common case: one entity owning the whole property, sent as a bare ``100``.

    Also pins the rendering: a percentage comes back at the column's two
    decimal places whichever of ``100``, ``100.0`` or ``"100.00"`` was sent,
    so a client never sees ``"100"`` beside a sibling's ``"40.00"`` and an
    audit ``after`` payload is comparable with a ``before`` read from the
    database.
    """
    prop = await create_property(org_user)
    entity = await create_entity(org_user)

    resp = await as_user(
        org_user,
        "PUT",
        f"/properties/{prop['id']}/ownership",
        [{"entity_id": entity["id"], "percentage": 100}],
    )
    assert resp.status_code == 200, resp.text
    assert [share["percentage"] for share in resp.json()] == ["100.00"]
    assert [row["ownership_percentage"] for row in await ownership_rows(prop["id"])] == [
        Decimal("100.00")
    ]
    # Filtered by action rather than read as [-1]: `audit_rows` orders by
    # `created_at, action`, and this test's property.created / entity.created
    # rows can tie on `created_at`, at which point the alphabetical secondary
    # sort decides the last element.
    replacements = [
        row for row in await audit_rows(org_user.org_id) if row["action"] == "ownership.replaced"
    ]
    assert len(replacements) == 1, replacements
    assert '"percentage": "100.00"' in replacements[0]["after"]


async def test_put_ownership_sums_json_numbers_exactly(org_user: OrgUser) -> None:
    """Shares sent as JSON numbers must parse as Decimal, never through float.

    ``0.01 + 64.04 + 35.95`` is exactly 100 in decimal but 100.00000000000001
    in binary floating point. These figures drive penny-exact apportionment
    in ``src/core/splits.py``, so the parse has to be exact -- pinned here
    because the JSON-number path (what the Flutter client will send) is the
    one where a float slip would hide.

    **The discriminating assertion is the 200, not the sum.** Were the
    values to pass through ``float`` on their way to
    :class:`~decimal.Decimal`, 0.01 would arrive as
    ``0.010000000000000000208...`` -- far more than the two decimal places
    ``OwnershipShare.percentage`` permits -- and all three shares would be
    422s. Getting a 200 at all is the proof that pydantic built each
    ``Decimal`` from the raw JSON text. The sum assertion then makes the
    float counterexample concrete.

    (Do not restore the previous ``33.33 + 33.33 + 33.34`` payload: those
    three floats happen to sum to exactly ``100.0``, so they demonstrate
    nothing about float error.)
    """
    prop = await create_property(org_user)
    entities = [await create_entity(org_user, name=f"Share {index}") for index in range(3)]

    resp = await as_user(
        org_user,
        "PUT",
        f"/properties/{prop['id']}/ownership",
        [
            {"entity_id": entities[0]["id"], "percentage": 0.01},
            {"entity_id": entities[1]["id"], "percentage": 64.04},
            {"entity_id": entities[2]["id"], "percentage": 35.95},
        ],
    )
    assert resp.status_code == 200, resp.text
    assert sum(Decimal(share["percentage"]) for share in resp.json()) == Decimal(100)
    assert 0.01 + 64.04 + 35.95 != 100.0, "the float hazard this test stands for must be real"


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


@pytest.mark.parametrize(
    "percentages",
    [
        ["0.00", "100.00"],
        ["-10.00", "55.00", "55.00"],
        ["33.333", "66.667"],
    ],
)
async def test_put_ownership_rejects_out_of_range_percentages(
    org_user: OrgUser, percentages: list[str]
) -> None:
    """Per-row bounds are enforced in the API, ahead of the CHECK constraint.

    **Every payload here sums to exactly 100**, which is the whole point: a
    set that misses the sum is refused by the sum rule whatever the per-row
    bounds say, so a one-share payload would pass this test with the bounds
    deleted. Pinning the sum to 100 leaves the per-row bound as the only
    thing that can reject it. Each case was checked to fail when its guard
    is removed:

    * ``["0.00", "100.00"]`` and ``["-10.00", "55.00", "55.00"]`` pin
      ``gt=0``. Without it, ``ownership_percentage numeric(5,2) check (> 0
      and <= 100)`` fires as an unhandled ``CheckViolationError`` -- a 500,
      precisely the trap the API-side bound exists to prevent.
    * ``["33.333", "66.667"]`` pins ``decimal_places=2``, the worst of the
      three failures. Without it the request **succeeds with a 200** and
      ``numeric(5,2)`` stores 33.33/66.67 -- money silently altered, with no
      error raised anywhere.

    ``le=100`` has no case *in this parametrize*, and cannot have one: every
    payload here sums to exactly 100, and with ``gt=0`` in force no single
    share of such a set can exceed 100. It is pinned separately, by
    ``test_put_ownership_over_100_is_refused_by_the_field_bound``, whose
    payload deliberately breaks the sum rule so that the field bound is
    reached at all.
    """
    prop = await create_property(org_user)
    shares = [
        {
            "entity_id": (await create_entity(org_user, name=f"Owner {index}"))["id"],
            "percentage": percentage,
        }
        for index, percentage in enumerate(percentages)
    ]
    assert sum(Decimal(percentage) for percentage in percentages) == Decimal(100), (
        "these payloads only test the per-row bound while their sum is exactly 100"
    )

    resp = await as_user(org_user, "PUT", f"/properties/{prop['id']}/ownership", shares)
    assert resp.status_code == 422, resp.text
    assert await ownership_rows(prop["id"]) == []


async def test_put_ownership_over_100_is_refused_by_the_field_bound(org_user: OrgUser) -> None:
    """A single share above 100 must be refused by ``le=100``, not by the sum rule.

    ``le=100`` cannot change which payloads are *accepted* -- a lone 100.01
    misses the sum-to-100 rule as well -- which is why it needs its own test
    rather than a case in ``test_put_ownership_rejects_out_of_range_percentages``,
    whose payloads all sum to exactly 100 by design.

    What it can change is **which rule refuses**, and therefore the 422 body.
    The ``less_than_equal`` assertion is the whole test: with the bound in
    place, pydantic rejects the field. Delete it and pydantic *accepts*
    ``Decimal("100.01")`` -- 5 digits at 2dp, so ``max_digits`` and
    ``decimal_places`` do not catch it -- and the request reaches the
    handler, which answers ``"must sum to exactly 100, got 100.01"``. Still
    a 422, different body. Checked: removing ``le=100`` fails this test on
    the ``less_than_equal`` assertion, not on the status code. Same
    assert-which-rule-refused technique as
    ``test_put_ownership_rejects_an_empty_list``'s ``too_short``.

    The property and entity are real so that the mutation's failure is the
    documented one: against a non-existent property the bound-less request
    would 404 at the ownership lookup and never reach the sum rule.
    """
    prop = await create_property(org_user)
    entity = await create_entity(org_user)

    resp = await as_user(
        org_user,
        "PUT",
        f"/properties/{prop['id']}/ownership",
        [{"entity_id": entity["id"], "percentage": "100.01"}],
    )
    assert resp.status_code == 422, resp.text
    assert "less_than_equal" in resp.text, (
        f"an over-100 share must be refused by the field bound, not the sum rule: {resp.text}"
    )
    assert await ownership_rows(prop["id"]) == []


async def test_put_ownership_rejects_an_empty_list(org_user: OrgUser) -> None:
    """Removing every owner would leave the property's income unattributable.

    The ``too_short`` assertion pins *which* rule refuses this. An empty set
    also sums to 0, so without it the test passes whether ``Body(min_length=1)``
    is present or not, and the 422 it names would silently become the
    sum-to-100 one.
    """
    prop = await create_property(org_user)
    resp = await as_user(org_user, "PUT", f"/properties/{prop['id']}/ownership", [])
    assert resp.status_code == 422, resp.text
    assert "too_short" in resp.text, f"the empty list must be refused by min_length: {resp.text}"


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


async def test_an_api_accepted_ownership_set_is_usable_by_split_amount(
    org_user: OrgUser,
) -> None:
    """Whatever this endpoint stores must be a share map ``split_amount`` will take.

    ``PUT /properties/{id}/ownership`` re-implements the three rules
    ``src/core/splits.py:_validate_shares`` applies -- non-empty, every share
    above zero, summing to exactly 100 -- rather than calling it, because the
    endpoint has to answer 422 and name the offending entities instead of
    raising ``InvalidOwnershipError``, and has to decide before it writes.
    Two copies of a rule drift.

    This is the drift guard, and it is deliberately *not* a coupling: it
    asserts only that the accepted set is acceptable downstream, so it fails
    if ``splits.py`` later tightens (or the endpoint loosens) without either
    caring how the two report their rejections. Nothing here re-tests
    apportionment itself -- ``tests/core/test_splits.py`` owns that -- so the
    assertion is that ``split_amount`` returns at all, plus its own
    penny-exactness postcondition.

    The 0.01/64.04/35.95 payload is reused from
    ``test_put_ownership_sums_json_numbers_exactly``: a set whose smallest
    share rounds to zero pennies on a small amount is the one most likely to
    expose a disagreement between the two validators.
    """
    prop = await create_property(org_user)
    entities = [await create_entity(org_user, name=f"Share {index}") for index in range(3)]

    resp = await as_user(
        org_user,
        "PUT",
        f"/properties/{prop['id']}/ownership",
        [
            {"entity_id": entities[0]["id"], "percentage": "0.01"},
            {"entity_id": entities[1]["id"], "percentage": "64.04"},
            {"entity_id": entities[2]["id"], "percentage": "35.95"},
        ],
    )
    assert resp.status_code == 200, resp.text

    stored = {
        uuid.UUID(str(row["entity_id"])): row["ownership_percentage"]
        for row in await ownership_rows(prop["id"])
    }
    allocated = split_amount(Decimal("1234.56"), stored)

    assert set(allocated) == set(stored), "every accepted owner must receive a share"
    assert sum(allocated.values(), Decimal(0)) == Decimal("1234.56")


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
        # Sums to exactly 100 so that `gt=0` is the only rule that can
        # refuse it -- a lone `{"percentage": "0.00"}` would be caught by
        # the sum rule instead, testing nothing about the per-row bound.
        [
            {"entity_id": owner["id"], "percentage": "0.00"},
            {"entity_id": other["id"], "percentage": "100.00"},
        ],
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
    assert [row["action"] for row in await audit_rows(org_b.org_id)] == ["property.created"], (
        "a refused cross-org PATCH must not write an audit row"
    )


@pytest.mark.parametrize("method", ["GET", "PATCH"])
@pytest.mark.parametrize("kind", ["entity", "property"])
async def test_a_cross_org_404_is_identical_to_a_nonexistent_one(
    make_org_user, kind: str, method: str
) -> None:
    """Another org's row must be indistinguishable from no row, on every verb.

    The 404 body is the last channel through which a caller could confirm
    that an id it guessed is real: if "belongs to someone else" read even
    slightly differently from "no such row", the endpoint would be an
    existence oracle over every other tenant's ids.
    ``src.api.scoping.not_found`` is the single source of both, so they are
    identical by construction -- this pins that, since the construction is
    one refactor away from changing.

    **Parametrised over the method as well as the kind** because
    ``test_org_a_cannot_patch_org_bs_entity``/``_property`` assert only the
    status code. PATCH runs the same lookup through the same
    ``get_owned_or_404`` and answers on the same channel, so the body
    property is exactly as load-bearing there; asserting it only for GET
    would leave a "helpfully" reworded PATCH 404 free to ship. The mutation
    that proves this: making ``get_owned_or_404`` fall back to an unfiltered
    lookup and word the cross-org case differently fails all four cases here
    and none of the status-only tests.

    Compared per resource kind, not across kinds: the message interpolates
    "entity"/"property", so an entity 404 and a property 404 legitimately
    differ. The PATCH bodies are valid ones -- an empty or nulling body
    would be refused by ``_PatchBody`` before any lookup happened, testing
    nothing about org scoping. Mirrors the ownership-422 equivalent in
    ``test_org_a_cannot_attach_org_bs_entity_to_its_own_property``.
    """
    org_a = await make_org_user()
    org_b = await make_org_user()
    if kind == "entity":
        theirs = await create_entity(org_b, name="Org B entity")
        path, body = "/entities", {"name": "Hijacked"}
    else:
        theirs = await create_property(org_b, address_line1="Org B address")
        path, body = "/properties", {"address_line1": "Hijacked"}
    payload = body if method == "PATCH" else None

    cross_org = await as_user(org_a, method, f"{path}/{theirs['id']}", payload)
    unknown = str(uuid.uuid4())
    nonexistent = await as_user(org_a, method, f"{path}/{unknown}", payload)

    assert cross_org.status_code == nonexistent.status_code == 404, cross_org.text
    assert cross_org.text.replace(theirs["id"], "X") == nonexistent.text.replace(unknown, "X"), (
        "another org's row and a non-existent one must be reported identically, "
        "or the 404 confirms which ids exist"
    )


async def test_org_a_cannot_set_ownership_on_org_bs_property(make_org_user) -> None:
    """A cross-org ownership PUT must 404, write nothing, and read like a missing id.

    The body-identity half is asserted here rather than in
    ``test_a_cross_org_404_is_identical_to_a_nonexistent_one``'s
    ``(kind, method)`` grid because this endpoint exists for properties only
    and needs a valid org-A entity in its body, so it does not fit that
    grid's shape. The property it is checking is the same one: this 404 comes
    from the ownership handler's own existence probe rather than from
    ``get_owned_or_404``, and a probe that answered differently would make
    the endpoint an existence oracle over other tenants' property ids just
    as surely. Mirrors the 422 comparison in
    ``test_org_a_cannot_attach_org_bs_entity_to_its_own_property``.
    """
    org_a = await make_org_user()
    org_b = await make_org_user()
    theirs = await create_property(org_b)
    mine = await create_entity(org_a, name="Org A entity")
    share = [{"entity_id": mine["id"], "percentage": "100.00"}]

    resp = await as_user(org_a, "PUT", f"/properties/{theirs['id']}/ownership", share)
    assert resp.status_code == 404, resp.text
    assert await ownership_rows(theirs["id"]) == []

    unknown = str(uuid.uuid4())
    unknown_resp = await as_user(org_a, "PUT", f"/properties/{unknown}/ownership", share)
    assert unknown_resp.status_code == 404, unknown_resp.text
    assert unknown_resp.text.replace(unknown, "X") == resp.text.replace(theirs["id"], "X"), (
        "another org's property and a non-existent one must be reported identically, "
        "or the 404 confirms which property ids exist"
    )


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


# ---------------------------------------------------------------------------
# scripts/seed_org.py -- see this module's docstring for why these live here.
# ---------------------------------------------------------------------------
@pytest.fixture
async def seeded_orgs():
    """Track org names ``seed_org`` created during a test and purge them afterwards.

    :yields: a list to append org names to.
    """
    names: list[str] = []
    yield names

    async with db() as conn:
        for name in names:
            org_ids = [
                record["id"] for record in await conn.fetch("select id from orgs where name = $1", name)
            ]
            for org_id in org_ids:
                await conn.execute("delete from users where org_id = $1", org_id)
                await conn.execute("delete from orgs where id = $1", org_id)


async def test_seed_org_creates_the_org_and_links_the_auth_user(
    make_auth_user, seeded_orgs: list[str]
) -> None:
    """The first run creates the org and the ``public.users`` row pointing at it."""
    user: AuthUser = await make_auth_user()
    org_name = f"Seed test org {uuid.uuid4().hex}"
    seeded_orgs.append(org_name)

    result = await seed_org(email=user.email, org_name=org_name)

    assert result.org_created is True
    assert result.user_linked is True
    assert result.user_id == user.user_id
    async with db() as conn:
        row = await conn.fetchrow(
            "select org_id, email from users where id = $1", user.user_id
        )
    assert row["org_id"] == result.org_id
    assert row["email"] == user.email


async def test_seed_org_is_idempotent(make_auth_user, seeded_orgs: list[str]) -> None:
    """A second run must change nothing and duplicate nothing."""
    user: AuthUser = await make_auth_user()
    org_name = f"Seed test org {uuid.uuid4().hex}"
    seeded_orgs.append(org_name)

    first = await seed_org(email=user.email, org_name=org_name)
    second = await seed_org(email=user.email, org_name=org_name)

    assert second.org_id == first.org_id
    assert second.org_created is False
    assert second.user_linked is False
    async with db() as conn:
        assert await conn.fetchval("select count(*) from orgs where name = $1", org_name) == 1
        assert await conn.fetchval("select count(*) from users where id = $1", user.user_id) == 1


async def test_seed_org_without_an_auth_user_fails_loudly(seeded_orgs: list[str]) -> None:
    """No auth user for the email is a hard error naming it, not a silently created one.

    Sign-up is out of scope (Task 19 builds sign-in only), so the auth user
    has to exist first -- and the script says so rather than half-seeding.
    """
    org_name = f"Seed test org {uuid.uuid4().hex}"
    seeded_orgs.append(org_name)
    email = f"nobody-{uuid.uuid4().hex}@example.com"

    with pytest.raises(RuntimeError, match=email):
        await seed_org(email=email, org_name=org_name)

    async with db() as conn:
        assert await conn.fetchval("select count(*) from orgs where name = $1", org_name) == 0, (
            "a failed seed must not leave an org behind"
        )


async def test_seed_org_refuses_an_ambiguous_org_name(
    make_auth_user, seeded_orgs: list[str]
) -> None:
    """Two orgs of the same name make the idempotency key ambiguous -- a hard error.

    ``org_name`` is this script's idempotency key: rerunning with the same
    name is supposed to reuse the same org. Two orgs of that name means
    "reuse the same one" has no answer, and silently taking the first would
    make a rerun's result depend on row order.

    The guard is genuinely reachable, not defensive dead code: ``orgs.name``
    carries **no** unique constraint (``0001_core.sql:150-155``), so the two
    rows below insert without complaint. Arranged over the direct ``db()``
    connection because the script itself refuses to create the second one.

    ``make_auth_user`` rather than ``make_org_user`` deliberately: the
    ambiguity check (``seed_org.py:103-108``) runs *before* the
    already-linked check, so an org-less user leaves it as the only guard
    that can fire, instead of relying on that ordering.
    """
    user: AuthUser = await make_auth_user()
    org_name = f"Seed test org {uuid.uuid4().hex}"
    seeded_orgs.append(org_name)

    async with db() as conn:
        for _ in range(2):
            await conn.execute("insert into orgs (name) values ($1)", org_name)

    with pytest.raises(RuntimeError, match=org_name):
        await seed_org(email=user.email, org_name=org_name)

    async with db() as conn:
        assert await conn.fetchval("select count(*) from orgs where name = $1", org_name) == 2, (
            "the refusal must not have created a third org"
        )
        assert await conn.fetchval("select count(*) from users where id = $1", user.user_id) == 0, (
            "an ambiguous run must link nobody"
        )


async def test_seed_org_refuses_to_move_a_user_between_orgs(
    make_auth_user, seeded_orgs: list[str]
) -> None:
    """A user already in another org is a hard error, not a silent re-pointing.

    Moving a user's ``org_id`` would move their whole view of the portfolio;
    an idempotent setup script must not do that as a side effect of being
    run with a different ``--org``.
    """
    user: AuthUser = await make_auth_user()
    first_name = f"Seed test org {uuid.uuid4().hex}"
    second_name = f"Seed test org {uuid.uuid4().hex}"
    seeded_orgs.extend([first_name, second_name])

    first = await seed_org(email=user.email, org_name=first_name)
    with pytest.raises(RuntimeError, match=str(first.org_id)):
        await seed_org(email=user.email, org_name=second_name)

    async with db() as conn:
        assert (
            await conn.fetchval("select org_id from users where id = $1", user.user_id)
            == first.org_id
        )
