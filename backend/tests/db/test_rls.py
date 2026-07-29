"""Row Level Security tests for ``supabase/migrations/0002_rls.sql``.

Three layers, matching the three things the migration actually does:

1. A catalog guard (mirrors the style of ``test_schema.py``) confirming RLS
   is enabled, and at least one policy exists, on every one of the 13 MVP
   tables -- not just the one table the live PostgREST test below exercises.
2. A composite-FK test proving the DB-level "belt and braces" constraint:
   a service-role write (which bypasses RLS entirely) still cannot stitch a
   ``property_ownership`` row across two different orgs.
3. A live cross-tenant isolation test that goes through the real PostgREST
   + Supabase Auth path (not a direct DB connection): two real auth users
   in two different orgs, real JWTs, real HTTP requests -- proving the RLS
   policies actually block cross-tenant reads/writes for the role that
   matters (``authenticated``), not just that the policy text looks right.

Run locally (from ``backend/``), with the Supabase local stack running
(``supabase status`` from the repo root to confirm)::

    uv run --env-file ../.env pytest tests/db/test_rls.py -v

:seealso: docs/superpowers/plans/2026-07-29-mvp-iteration-1.md, Task 6.
"""

import os
import uuid

import asyncpg
import httpx
import pytest

#: The 13 MVP tables -- same set test_schema.py guards for existence; this
#: file guards that every one of them also has RLS enabled with a policy.
EXPECTED_TABLES = {
    "orgs",
    "users",
    "entities",
    "properties",
    "property_ownership",
    "tenancies",
    "imports",
    "transactions",
    "compliance_certificates",
    "documents",
    "mtd_quarters",
    "job_queue",
    "audit_log",
}


def _database_url() -> str:
    """Read ``DATABASE_URL`` from the environment.

    :raises RuntimeError: if unset -- fail loudly rather than skip (house
        rule; see ``test_schema.py`` for the identical rationale).
    :returns: the connection string to use for a direct (service-role
        equivalent) Postgres connection.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Start the local Supabase stack "
            "(`supabase start` from the repo root) and run this test with "
            "`uv run --env-file ../.env pytest tests/db/test_rls.py`, "
            "or export DATABASE_URL yourself."
        )
    return url


def _supabase_url() -> str:
    """Read ``SUPABASE_URL`` from the environment.

    :raises RuntimeError: if unset.
    :returns: the base URL of the local Supabase API gateway (Auth +
        PostgREST both hang off this).
    """
    url = os.environ.get("SUPABASE_URL")
    if not url:
        raise RuntimeError(
            "SUPABASE_URL is not set. Run this test with "
            "`uv run --env-file ../.env pytest tests/db/test_rls.py`."
        )
    return url


def _anon_key() -> str:
    """Read ``SUPABASE_ANON_KEY`` from the environment.

    :raises RuntimeError: if unset.
    :returns: the anon API key used for every PostgREST/Auth request in
        this file -- the real client-side key, paired with a real user JWT,
        so the test exercises the exact path the Flutter frontend will use.
    """
    key = os.environ.get("SUPABASE_ANON_KEY")
    if not key:
        raise RuntimeError(
            "SUPABASE_ANON_KEY is not set. Run this test with "
            "`uv run --env-file ../.env pytest tests/db/test_rls.py`."
        )
    return key


def _service_role_key() -> str:
    """Read ``SUPABASE_SERVICE_ROLE_KEY`` from the environment.

    :raises RuntimeError: if unset.
    :returns: the service-role key, used only to call the Auth admin API
        (create/delete users) -- never used against PostgREST in this file.
    """
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not key:
        raise RuntimeError(
            "SUPABASE_SERVICE_ROLE_KEY is not set. Run this test with "
            "`uv run --env-file ../.env pytest tests/db/test_rls.py`."
        )
    return key


@pytest.mark.asyncio
async def test_all_expected_tables_have_row_level_security_enabled() -> None:
    """Every one of the 13 MVP tables must have ``relrowsecurity = true``.

    The live PostgREST test below only ever inserts into ``properties`` --
    without this guard, RLS silently missing from any of the other 12
    tables would go undetected.
    """
    conn = await asyncpg.connect(_database_url())
    try:
        rows = await conn.fetch(
            """
            select c.relname, c.relrowsecurity
            from pg_class c
            join pg_namespace n on n.oid = c.relnamespace
            where n.nspname = 'public' and c.relkind = 'r'
            """
        )
    finally:
        await conn.close()

    rls_by_table = {row["relname"]: row["relrowsecurity"] for row in rows}
    missing = EXPECTED_TABLES - rls_by_table.keys()
    assert not missing, f"Tables not found at all: {sorted(missing)}"

    not_enabled = {t for t in EXPECTED_TABLES if not rls_by_table[t]}
    assert not not_enabled, f"Tables without RLS enabled: {sorted(not_enabled)}"


@pytest.mark.asyncio
async def test_all_expected_tables_have_at_least_one_policy() -> None:
    """Every one of the 13 MVP tables must have >=1 row in ``pg_policies``.

    RLS can be enabled with zero policies defined -- that fails *closed*
    (every role except the bypassing ones sees zero rows), which would
    silently break the app rather than leak data, but it's still drift
    from what this migration is supposed to do, so it's worth guarding.
    """
    conn = await asyncpg.connect(_database_url())
    try:
        rows = await conn.fetch(
            "select tablename, count(*) as n from pg_policies "
            "where schemaname = 'public' group by tablename"
        )
    finally:
        await conn.close()

    policy_count_by_table = {row["tablename"]: row["n"] for row in rows}
    missing = EXPECTED_TABLES - policy_count_by_table.keys()
    assert not missing, f"Tables with zero policies: {sorted(missing)}"


class _RollbackTestTransaction(Exception):
    """Internal sentinel raised to force a rollback of a successful test transaction."""


@pytest.mark.asyncio
async def test_property_ownership_cross_org_fk_violation() -> None:
    """A property_ownership row can't reference a property/entity in another org.

    This is the DB-level backstop from the Task 5 review: a service-role
    write bypasses RLS entirely (service_role has BYPASSRLS), so the only
    thing stopping it from stitching a ``property_ownership`` row across
    two orgs is the composite FK
    ``(property_id, org_id) references properties(id, org_id)`` (and the
    equivalent for ``entity_id``) added in 0002. Connects directly as the
    service-role-equivalent ``postgres`` user (``DATABASE_URL``), which is
    exactly the write path this constraint has to hold up against -- RLS
    policies are irrelevant to this test by design.
    """
    conn = await asyncpg.connect(_database_url())
    try:
        with pytest.raises(asyncpg.exceptions.ForeignKeyViolationError):
            async with conn.transaction():
                org_a = await conn.fetchval(
                    "insert into orgs (name) values ('rls fk test org a') returning id"
                )
                org_b = await conn.fetchval(
                    "insert into orgs (name) values ('rls fk test org b') returning id"
                )
                # property and entity both live in org_a...
                property_id = await conn.fetchval(
                    """
                    insert into properties
                        (org_id, address_line1, city, postcode, finance_cost_classification)
                    values ($1, '1 Test Street', 'Luton', 'LU1 1AA', 'residential')
                    returning id
                    """,
                    org_a,
                )
                entity_id = await conn.fetchval(
                    """
                    insert into entities (org_id, name, tax_regime)
                    values ($1, 'rls fk test entity', 'mtd_itsa')
                    returning id
                    """,
                    org_a,
                )
                # ...but the ownership row claims org_b. The simple FKs
                # (property_id -> properties.id, entity_id -> entities.id)
                # alone would allow this; the composite FKs must reject it.
                await conn.execute(
                    """
                    insert into property_ownership
                        (org_id, property_id, entity_id, ownership_percentage)
                    values ($1, $2, $3, 100)
                    """,
                    org_b,
                    property_id,
                    entity_id,
                )

                # Unreachable if the FK does its job -- included so a
                # regression shows up as a normal assertion failure too,
                # not just a missing exception.
                raise _RollbackTestTransaction
    finally:
        await conn.close()


class _CrossTenantFixture:
    """Two orgs, two real auth users, and their real JWTs, for the live RLS test.

    :ivar org_a_id: id of org A.
    :ivar org_b_id: id of org B.
    :ivar jwt_a: access token for the user in org A.
    :ivar jwt_b: access token for the user in org B.
    """

    def __init__(self, org_a_id: str, org_b_id: str, jwt_a: str, jwt_b: str) -> None:
        self.org_a_id = org_a_id
        self.org_b_id = org_b_id
        self.jwt_a = jwt_a
        self.jwt_b = jwt_b


@pytest.fixture
async def cross_tenant_fixture():
    """Create two orgs + two real Supabase Auth users (one per org), yield JWTs, then clean up.

    Setup uses the service-role-equivalent DB connection (inserting
    ``orgs``/``users`` rows directly, bypassing RLS by design -- this is
    the same "service connection" path the backend/worker use) plus the
    Supabase Auth admin API (to create real ``auth.users`` rows, since
    ``public.users.id`` FKs to ``auth.users.id``). Sign-in then goes through
    the real password grant, producing real JWTs.

    Cleanup order matters (org FKs are NO ACTION, not CASCADE -- see
    0001_core.sql's "Org deletion policy" comment): delete the
    ``properties`` row(s) created during the test first, then delete the
    two auth users via the Auth admin API (which cascades ``public.users``
    via ``on delete cascade``), then delete the two orgs. Nothing here is
    wrapped in try/except -- if cleanup fails, the test must fail loudly,
    not swallow it.
    """
    database_url = _database_url()
    supabase_url = _supabase_url()
    anon_key = _anon_key()
    service_key = _service_role_key()

    suffix = uuid.uuid4().hex
    email_a = f"rls-test-a-{suffix}@example.com"
    email_b = f"rls-test-b-{suffix}@example.com"
    password = f"Test-Password-{suffix}!"

    conn = await asyncpg.connect(database_url)
    try:
        org_a_id = await conn.fetchval(
            "insert into orgs (name) values ($1) returning id", f"RLS test org A {suffix}"
        )
        org_b_id = await conn.fetchval(
            "insert into orgs (name) values ($1) returning id", f"RLS test org B {suffix}"
        )
    finally:
        await conn.close()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{supabase_url}/auth/v1/admin/users",
            headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"},
            json={"email": email_a, "password": password, "email_confirm": True},
        )
        assert resp.status_code == 200, f"admin create user A failed: {resp.status_code} {resp.text}"
        user_a_id = resp.json()["id"]

        resp = await client.post(
            f"{supabase_url}/auth/v1/admin/users",
            headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"},
            json={"email": email_b, "password": password, "email_confirm": True},
        )
        assert resp.status_code == 200, f"admin create user B failed: {resp.status_code} {resp.text}"
        user_b_id = resp.json()["id"]

    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute(
            "insert into users (id, org_id, email) values ($1, $2, $3)",
            uuid.UUID(user_a_id),
            org_a_id,
            email_a,
        )
        await conn.execute(
            "insert into users (id, org_id, email) values ($1, $2, $3)",
            uuid.UUID(user_b_id),
            org_b_id,
            email_b,
        )
    finally:
        await conn.close()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{supabase_url}/auth/v1/token?grant_type=password",
            headers={"apikey": anon_key, "Content-Type": "application/json"},
            json={"email": email_a, "password": password},
        )
        assert resp.status_code == 200, f"sign-in A failed: {resp.status_code} {resp.text}"
        jwt_a = resp.json()["access_token"]

        resp = await client.post(
            f"{supabase_url}/auth/v1/token?grant_type=password",
            headers={"apikey": anon_key, "Content-Type": "application/json"},
            json={"email": email_b, "password": password},
        )
        assert resp.status_code == 200, f"sign-in B failed: {resp.status_code} {resp.text}"
        jwt_b = resp.json()["access_token"]

    yield _CrossTenantFixture(org_a_id=str(org_a_id), org_b_id=str(org_b_id), jwt_a=jwt_a, jwt_b=jwt_b)

    # --- Cleanup (no try/except -- a failure here must fail the test loudly). ---
    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute(
            "delete from properties where org_id in ($1, $2)", org_a_id, org_b_id
        )
    finally:
        await conn.close()

    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            f"{supabase_url}/auth/v1/admin/users/{user_a_id}",
            headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"},
        )
        assert resp.status_code in (200, 204), (
            f"admin delete user A failed: {resp.status_code} {resp.text}"
        )
        resp = await client.delete(
            f"{supabase_url}/auth/v1/admin/users/{user_b_id}",
            headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"},
        )
        assert resp.status_code in (200, 204), (
            f"admin delete user B failed: {resp.status_code} {resp.text}"
        )

    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute("delete from orgs where id in ($1, $2)", org_a_id, org_b_id)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_cross_tenant_isolation_via_postgrest(cross_tenant_fixture: _CrossTenantFixture) -> None:
    """User B must never see or modify user A's data through PostgREST.

    Exercises the real path the Flutter frontend will use: anon API key +
    a real user JWT against ``/rest/v1/properties``. Includes a positive
    control (user A can see their own freshly-inserted row) so that user
    B's empty result is proof of tenant isolation, not proof the whole
    request path is silently broken.
    """
    supabase_url = _supabase_url()
    anon_key = _anon_key()

    async with httpx.AsyncClient() as client:
        # User A inserts a property in their own org.
        resp = await client.post(
            f"{supabase_url}/rest/v1/properties",
            headers={
                "apikey": anon_key,
                "Authorization": f"Bearer {cross_tenant_fixture.jwt_a}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
            json={
                "org_id": cross_tenant_fixture.org_a_id,
                "address_line1": "1 Test Street",
                "city": "Luton",
                "postcode": "LU1 1AA",
                "finance_cost_classification": "residential",
            },
        )
        assert resp.status_code == 201, f"insert as user A failed: {resp.status_code} {resp.text}"
        inserted = resp.json()
        assert len(inserted) == 1
        property_id = inserted[0]["id"]

        # Positive control: user A must see exactly their own row.
        resp = await client.get(
            f"{supabase_url}/rest/v1/properties",
            headers={"apikey": anon_key, "Authorization": f"Bearer {cross_tenant_fixture.jwt_a}"},
            params={"select": "id"},
        )
        assert resp.status_code == 200
        assert [row["id"] for row in resp.json()] == [property_id], (
            "user A should see exactly the property they just inserted"
        )

        # User B selects properties -> must see zero rows.
        resp = await client.get(
            f"{supabase_url}/rest/v1/properties",
            headers={"apikey": anon_key, "Authorization": f"Bearer {cross_tenant_fixture.jwt_b}"},
        )
        assert resp.status_code == 200, f"select as user B failed: {resp.status_code} {resp.text}"
        assert resp.json() == [], "user B must not see user A's org's properties"

        # User B attempts to update A's row -> 0 rows affected.
        resp = await client.patch(
            f"{supabase_url}/rest/v1/properties",
            headers={
                "apikey": anon_key,
                "Authorization": f"Bearer {cross_tenant_fixture.jwt_b}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
            params={"id": f"eq.{property_id}"},
            json={"city": "Hacked City"},
        )
        assert resp.status_code == 200, f"update as user B failed: {resp.status_code} {resp.text}"
        assert resp.json() == [], "user B's update of user A's row must affect 0 rows"

        # Confirm A's row was genuinely untouched by B's attempted update.
        resp = await client.get(
            f"{supabase_url}/rest/v1/properties",
            headers={"apikey": anon_key, "Authorization": f"Bearer {cross_tenant_fixture.jwt_a}"},
            params={"select": "city"},
        )
        assert resp.status_code == 200
        assert [row["city"] for row in resp.json()] == ["Luton"]

        # Write-side check: user A must not be able to plant a row *in org
        # B's space* either. The assertions above only exercise the
        # `using` clause (read visibility); this exercises `with check`
        # (write eligibility) -- a different failure mode (a write-side
        # tenant escape) that `using` alone can't catch.
        resp = await client.post(
            f"{supabase_url}/rest/v1/properties",
            headers={
                "apikey": anon_key,
                "Authorization": f"Bearer {cross_tenant_fixture.jwt_a}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
            json={
                "org_id": cross_tenant_fixture.org_b_id,
                "address_line1": "2 Escape Street",
                "city": "Elsewhere",
                "postcode": "LU2 2BB",
                "finance_cost_classification": "residential",
            },
        )
        assert resp.status_code == 403, (
            f"user A inserting into org B's space should be rejected by `with check`, "
            f"got {resp.status_code} {resp.text}"
        )

        # The row must not have landed under either identity: re-check
        # user A's own view is still exactly the one legitimate property.
        resp = await client.get(
            f"{supabase_url}/rest/v1/properties",
            headers={"apikey": anon_key, "Authorization": f"Bearer {cross_tenant_fixture.jwt_a}"},
            params={"select": "id"},
        )
        assert resp.status_code == 200
        assert [row["id"] for row in resp.json()] == [property_id], (
            "the rejected cross-org insert must not have left a stray row behind"
        )
