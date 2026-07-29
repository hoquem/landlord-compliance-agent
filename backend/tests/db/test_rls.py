"""Row Level Security tests for ``supabase/migrations/0002_rls.sql``.

Four layers:

1. Catalog guards (mirror the style of ``test_schema.py``) confirming RLS
   is enabled on every one of the 13 MVP tables, that every table's policy
   actually references ``current_org_id()`` in both its ``USING`` and
   ``WITH CHECK`` clauses -- not just that *a* policy exists -- and (via
   ``has_table_privilege``/``has_function_privilege``) that the exact
   grant/revoke state from the "Adversarial spec review fixes" block in
   0002_rls.sql holds: no TRUNCATE/REFERENCES/TRIGGER for ``anon`` or
   ``authenticated`` on any table, ``current_org_id()`` executable only by
   ``authenticated``, and ``users``/``orgs`` writable only via SELECT for
   ``authenticated``.
2. A composite-FK test proving the DB-level "belt and braces" constraint:
   a service-role write (which bypasses RLS entirely) still cannot stitch a
   ``property_ownership`` row across two different orgs.
3. A live cross-tenant isolation test that goes through the real PostgREST
   + Supabase Auth path (not a direct DB connection): two real auth users
   in two different orgs, real JWTs, real HTTP requests -- proving the RLS
   policies actually block cross-tenant reads/writes (both ``USING`` and
   ``WITH CHECK``) for the role that matters (``authenticated``), not just
   that the policy text looks right.
4. Least-privilege / fail-closed pins added after an adversarial review of
   this migration: ``authenticated`` can't write ``job_queue`` or mutate
   ``audit_log``, ``anon`` is refused outright (not just filtered to zero
   rows), a user with no ``public.users`` row is fully locked out on both
   reads and writes, and an org member can't repoint their own ``org_id``
   or delete an org-mate's ``users`` row.

Run locally (from ``backend/``), with the Supabase local stack running
(``supabase status`` from the repo root to confirm)::

    uv run --env-file ../.env pytest tests/db/test_rls.py -v

:seealso: docs/superpowers/plans/2026-07-29-mvp-iteration-1.md, Task 6.
"""

import uuid

import asyncpg
import httpx
import pytest
from conftest import (
    EXPECTED_TABLES,
    _anon_key,
    _database_url,
    _service_role_key,
    _supabase_url,
)


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

    # A 0003+ migration that adds a table without updating EXPECTED_TABLES
    # would otherwise sail through both guards above silently -- the new
    # table simply never gets checked. Assert the catalog set is exactly
    # EXPECTED_TABLES, not just a superset of it.
    extra = rls_by_table.keys() - EXPECTED_TABLES
    assert not extra, f"Unexpected tables in public schema not covered by this guard: {sorted(extra)}"

    not_enabled = {t for t in EXPECTED_TABLES if not rls_by_table[t]}
    assert not not_enabled, f"Tables without RLS enabled: {sorted(not_enabled)}"


@pytest.mark.asyncio
async def test_all_expected_tables_have_org_scoped_policies() -> None:
    """Every one of the 13 MVP tables must have a policy that actually org-scopes.

    A bare ``count(*) >= 1`` guard (this test's original form) passes just
    as happily for a ``using (true)`` policy as for a real org-scoping one
    -- RLS "enabled" with a policy that grants blanket access reads as
    protected while doing nothing. Assert the org-scoping expression
    (``current_org_id()``) is actually present in both ``pg_policies.qual``
    (the ``USING`` clause) and ``pg_policies.with_check`` (the
    ``WITH CHECK`` clause) for every table, so both the read-visibility and
    the write-eligibility halves of the policy are checked, not just one.
    """
    conn = await asyncpg.connect(_database_url())
    try:
        rows = await conn.fetch(
            "select tablename, qual, with_check from pg_policies where schemaname = 'public'"
        )
    finally:
        await conn.close()

    by_table = {row["tablename"]: row for row in rows}
    missing = EXPECTED_TABLES - by_table.keys()
    assert not missing, f"Tables with zero policies: {sorted(missing)}"

    # Same reasoning as the RLS-enabled guard above: without this, a 0003
    # migration adding a table with its own (possibly non-org-scoped)
    # policy but no EXPECTED_TABLES update would go unchecked.
    extra = by_table.keys() - EXPECTED_TABLES
    assert not extra, f"Unexpected tables with policies not covered by this guard: {sorted(extra)}"

    not_org_scoped = {
        table
        for table in EXPECTED_TABLES
        if "current_org_id" not in (by_table[table]["qual"] or "")
        or "current_org_id" not in (by_table[table]["with_check"] or "")
    }
    assert not not_org_scoped, (
        "Tables whose policy doesn't reference current_org_id() in both "
        f"USING and WITH CHECK: {sorted(not_org_scoped)}"
    )


@pytest.mark.asyncio
async def test_privilege_regression_pins() -> None:
    """Pin the exact grant/revoke state 0002_rls.sql is supposed to produce.

    Catalog-level check using ``has_table_privilege``/``has_function_privilege``
    (Postgres's own privilege-resolution functions, not a re-parse of grant
    text) so this fails the moment the *effective* privilege set drifts --
    including the 0002 refactor that collapses the ``users``/``orgs``
    grant-then-revoke pairs down to a single ``grant select`` at each
    table's own section: this test pins the final privilege state that
    refactor must preserve.

    Three things pinned:

    1. Neither ``anon`` nor ``authenticated`` has TRUNCATE, REFERENCES, or
       TRIGGER on any of the 13 tables (adversarial-review item 4 --
       TRUNCATE bypasses RLS entirely; REFERENCES/TRIGGER let a role alter
       table structure).
    2. ``current_org_id()`` is executable by ``authenticated`` but not by
       ``anon`` (adversarial-review item 3).
    3. ``authenticated`` can SELECT but cannot INSERT/UPDATE/DELETE
       ``public.users`` or ``public.orgs`` (adversarial-review items 1-2 --
       org membership management is service-side only).
    """
    conn = await asyncpg.connect(_database_url())
    try:
        privilege_rows = await conn.fetch(
            """
            select r.role, t.tbl, p.priv,
                   has_table_privilege(r.role, format('public.%I', t.tbl), p.priv) as has_priv
            from unnest($1::text[]) as t(tbl)
            cross join unnest(array['anon', 'authenticated']) as r(role)
            cross join unnest(array['TRUNCATE', 'REFERENCES', 'TRIGGER']) as p(priv)
            """,
            sorted(EXPECTED_TABLES),
        )

        anon_can_execute = await conn.fetchval(
            "select has_function_privilege('anon', 'public.current_org_id()', 'execute')"
        )
        authenticated_can_execute = await conn.fetchval(
            "select has_function_privilege('authenticated', 'public.current_org_id()', 'execute')"
        )

        users_orgs_rows = await conn.fetch(
            """
            select t.tbl, p.priv,
                   has_table_privilege('authenticated', format('public.%I', t.tbl), p.priv) as has_priv
            from unnest(array['users', 'orgs']) as t(tbl)
            cross join unnest(array['SELECT', 'INSERT', 'UPDATE', 'DELETE']) as p(priv)
            """
        )
    finally:
        await conn.close()

    leaked = [
        f"{row['role']} has {row['priv']} on {row['tbl']} (should have been revoked)"
        for row in privilege_rows
        if row["has_priv"]
    ]
    assert not leaked, "TRUNCATE/REFERENCES/TRIGGER privilege regression:\n" + "\n".join(leaked)

    assert anon_can_execute is False, (
        "anon must not be able to execute public.current_org_id() -- it should have been "
        "revoked from PUBLIC and never re-granted to anon"
    )
    assert authenticated_can_execute is True, (
        "authenticated must be able to execute public.current_org_id() -- RLS policy "
        "evaluation depends on it internally"
    )

    users_orgs_wrong = [
        f"authenticated {'lacks' if row['priv'] == 'SELECT' else 'has'} {row['priv']} on {row['tbl']}"
        for row in users_orgs_rows
        if (row["priv"] == "SELECT" and not row["has_priv"])
        or (row["priv"] != "SELECT" and row["has_priv"])
    ]
    assert not users_orgs_wrong, (
        "authenticated must have SELECT but not INSERT/UPDATE/DELETE on users/orgs:\n"
        + "\n".join(users_orgs_wrong)
    )


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

                # Unreachable if the FK does its job. If the FK regresses,
                # the insert succeeds and this raise fires: it rolls the
                # transaction back (leaving no test data behind) and,
                # because it isn't a ForeignKeyViolationError,
                # pytest.raises() lets it propagate -- surfacing as an
                # unhandled-exception test ERROR, not a plain assertion
                # failure. Nothing here actually asserts anything itself.
                raise _RollbackTestTransaction
    finally:
        await conn.close()


class _CrossTenantFixture:
    """Two orgs, three real auth users (two in org A, one in org B), for the live RLS test.

    User A2 is an org-mate of user A (same org_a_id) -- needed to test the
    intra-org lockout path (one org member deleting another's ``users``
    row), which cross-org user B can't stand in for: a cross-org delete
    would already return 0 rows matched under the old ``using`` clause
    alone, so it wouldn't prove the grant-level fix does anything.

    :ivar org_a_id: id of org A.
    :ivar org_b_id: id of org B.
    :ivar user_a_id: ``auth.users``/``public.users`` id of user A.
    :ivar user_a2_id: id of user A2 (org-mate of user A).
    :ivar user_b_id: id of user B.
    :ivar jwt_a: access token for user A.
    :ivar jwt_a2: access token for user A2.
    :ivar jwt_b: access token for user B.
    """

    def __init__(
        self,
        org_a_id: str,
        org_b_id: str,
        user_a_id: str,
        user_a2_id: str,
        user_b_id: str,
        jwt_a: str,
        jwt_a2: str,
        jwt_b: str,
    ) -> None:
        self.org_a_id = org_a_id
        self.org_b_id = org_b_id
        self.user_a_id = user_a_id
        self.user_a2_id = user_a2_id
        self.user_b_id = user_b_id
        self.jwt_a = jwt_a
        self.jwt_a2 = jwt_a2
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
    email_a2 = f"rls-test-a2-{suffix}@example.com"
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
            json={"email": email_a2, "password": password, "email_confirm": True},
        )
        assert resp.status_code == 200, f"admin create user A2 failed: {resp.status_code} {resp.text}"
        user_a2_id = resp.json()["id"]

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
            uuid.UUID(user_a2_id),
            org_a_id,
            email_a2,
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
            json={"email": email_a2, "password": password},
        )
        assert resp.status_code == 200, f"sign-in A2 failed: {resp.status_code} {resp.text}"
        jwt_a2 = resp.json()["access_token"]

        resp = await client.post(
            f"{supabase_url}/auth/v1/token?grant_type=password",
            headers={"apikey": anon_key, "Content-Type": "application/json"},
            json={"email": email_b, "password": password},
        )
        assert resp.status_code == 200, f"sign-in B failed: {resp.status_code} {resp.text}"
        jwt_b = resp.json()["access_token"]

    yield _CrossTenantFixture(
        org_a_id=str(org_a_id),
        org_b_id=str(org_b_id),
        user_a_id=user_a_id,
        user_a2_id=user_a2_id,
        user_b_id=user_b_id,
        jwt_a=jwt_a,
        jwt_a2=jwt_a2,
        jwt_b=jwt_b,
    )

    # --- Cleanup (no try/except -- a failure here must fail the test loudly). ---
    # Per-table sweep, keyed on org_id, over every org-scoped table --  not
    # just properties -- in FK-safe dependency order (children before the
    # parents they reference; see 0001_core.sql for the FK graph this
    # mirrors). This is what lets a future test reuse this fixture and
    # insert into any org-scoped table without silently breaking teardown
    # the way a properties-only delete would.
    conn = await asyncpg.connect(database_url)
    try:
        for table in (
            "transactions",
            "property_ownership",
            "tenancies",
            "compliance_certificates",
            "mtd_quarters",
            "job_queue",
            "audit_log",
            "imports",
            "documents",
            "properties",
            "entities",
            "users",
        ):
            await conn.execute(f"delete from {table} where org_id in ($1, $2)", org_a_id, org_b_id)
    finally:
        await conn.close()

    async with httpx.AsyncClient() as client:
        for label, uid in (("A", user_a_id), ("A2", user_a2_id), ("B", user_b_id)):
            resp = await client.delete(
                f"{supabase_url}/auth/v1/admin/users/{uid}",
                headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"},
            )
            assert resp.status_code in (200, 204), (
                f"admin delete user {label} failed: {resp.status_code} {resp.text}"
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


@pytest.mark.asyncio
async def test_authenticated_cannot_insert_job_queue(cross_tenant_fixture: _CrossTenantFixture) -> None:
    """An authenticated client must not be able to enqueue arbitrary worker jobs.

    job_queue is worker-internal (Task 18 polls it, Task 14's imports
    endpoint enqueues into it) -- both service-side. `authenticated` was
    narrowed to select-only in 0002_rls.sql; this pins that a signed-in
    client attempting to INSERT gets a hard permission failure, not a
    silently-ignored write or a policy-shaped 0-rows response.
    """
    supabase_url = _supabase_url()
    anon_key = _anon_key()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{supabase_url}/rest/v1/job_queue",
            headers={
                "apikey": anon_key,
                "Authorization": f"Bearer {cross_tenant_fixture.jwt_a}",
                "Content-Type": "application/json",
            },
            json={
                "org_id": cross_tenant_fixture.org_a_id,
                "type": "categorise",
                "payload": {},
            },
        )
        assert resp.status_code == 403, (
            f"expected INSERT into job_queue to be rejected, got {resp.status_code} {resp.text}"
        )


@pytest.mark.asyncio
async def test_authenticated_cannot_update_or_delete_audit_log(
    cross_tenant_fixture: _CrossTenantFixture,
) -> None:
    """An authenticated client must not be able to edit or erase the audit trail.

    audit_log is append-only by design (0001_core.sql: no updated_at/update
    trigger); `authenticated` is granted select+insert only. Postgres
    checks table-level privilege before it ever evaluates row visibility,
    so this must reject with 403 regardless of whether any row matches the
    filter -- no audit_log row needs to exist for this test to be valid.
    """
    supabase_url = _supabase_url()
    anon_key = _anon_key()
    arbitrary_id = str(uuid.uuid4())

    async with httpx.AsyncClient() as client:
        resp = await client.patch(
            f"{supabase_url}/rest/v1/audit_log",
            headers={
                "apikey": anon_key,
                "Authorization": f"Bearer {cross_tenant_fixture.jwt_a}",
                "Content-Type": "application/json",
            },
            params={"id": f"eq.{arbitrary_id}"},
            json={"action": "tampered"},
        )
        assert resp.status_code == 403, (
            f"expected UPDATE on audit_log to be rejected, got {resp.status_code} {resp.text}"
        )

        resp = await client.delete(
            f"{supabase_url}/rest/v1/audit_log",
            headers={"apikey": anon_key, "Authorization": f"Bearer {cross_tenant_fixture.jwt_a}"},
            params={"id": f"eq.{arbitrary_id}"},
        )
        assert resp.status_code == 403, (
            f"expected DELETE on audit_log to be rejected, got {resp.status_code} {resp.text}"
        )


@pytest.mark.asyncio
async def test_anon_cannot_read_properties() -> None:
    """An unauthenticated (anon-key-only) request must be refused outright.

    `anon` has no grants at all on `properties` (0002_rls.sql deliberately
    withholds them) -- this must fail closed with a permission error, not
    quietly return an empty list the way a same-privilege-but-wrong-org
    ``authenticated`` request would. Those are different failure modes and
    worth telling apart: an empty list from an authorized-but-scoped-out
    caller is normal; an empty list from a caller that was never granted
    access at all would be masking a privilege hole as an isolation
    success.
    """
    supabase_url = _supabase_url()
    anon_key = _anon_key()

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{supabase_url}/rest/v1/properties",
            headers={"apikey": anon_key},
        )
        # Observed against the local stack: PostgREST surfaces the missing
        # table grant for the anon role as HTTP 401 with SQLSTATE 42501
        # ("permission denied for table properties") -- 401 rather than the
        # 403 seen for authenticated-but-unprivileged roles, because anon
        # carries no bearer identity at all. Pin both the status and the
        # SQLSTATE so a regression to an empty-but-successful 200 [] (grant
        # accidentally restored) or to a different refusal mode is caught.
        assert resp.status_code == 401, (
            f"expected anon read of properties to be refused, got {resp.status_code} {resp.text}"
        )
        assert "42501" in resp.text, (
            f"expected permission-denied (42501) refusal, got {resp.text}"
        )


class _OrglessUserFixture:
    """A real Supabase Auth user with no corresponding ``public.users`` row.

    :ivar jwt: access token for this user.
    """

    def __init__(self, jwt: str) -> None:
        self.jwt = jwt


@pytest.fixture
async def orgless_user_fixture():
    """Create a real auth user deliberately NOT linked via a ``public.users`` row.

    Exercises the fail-closed path: ``current_org_id()`` returns ``NULL``
    for a user with no ``users`` row (the security-definer lookup finds no
    match), so every ``org_id = current_org_id()`` comparison is ``NULL``
    (per SQL three-valued logic, never ``true``) -- reads must return zero
    rows and writes must be rejected, rather than the caller ending up
    scoped to "no org" in some other, more permissive way.
    """
    supabase_url = _supabase_url()
    anon_key = _anon_key()
    service_key = _service_role_key()

    suffix = uuid.uuid4().hex
    email = f"rls-test-orgless-{suffix}@example.com"
    password = f"Test-Password-{suffix}!"

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{supabase_url}/auth/v1/admin/users",
            headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"},
            json={"email": email, "password": password, "email_confirm": True},
        )
        assert resp.status_code == 200, (
            f"admin create orgless user failed: {resp.status_code} {resp.text}"
        )
        user_id = resp.json()["id"]

        resp = await client.post(
            f"{supabase_url}/auth/v1/token?grant_type=password",
            headers={"apikey": anon_key, "Content-Type": "application/json"},
            json={"email": email, "password": password},
        )
        assert resp.status_code == 200, f"sign-in orgless user failed: {resp.status_code} {resp.text}"
        jwt = resp.json()["access_token"]

    yield _OrglessUserFixture(jwt=jwt)

    # --- Cleanup (no try/except -- a failure here must fail the test loudly). ---
    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            f"{supabase_url}/auth/v1/admin/users/{user_id}",
            headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"},
        )
        assert resp.status_code in (200, 204), (
            f"admin delete orgless user failed: {resp.status_code} {resp.text}"
        )


@pytest.mark.asyncio
async def test_authenticated_user_without_users_row_is_fully_locked_out(
    orgless_user_fixture: _OrglessUserFixture,
) -> None:
    """A signed-in user with no ``public.users`` row reads nothing and can't write.

    This is the "user exists in Supabase Auth but org onboarding never
    finished" case. It must fail closed on both sides: reads come back
    empty (never an error -- ``NULL = NULL`` is not ``true``, so the
    ``using`` clause just never matches), and writes are rejected outright
    by ``with check`` (there is no org_id value that can equal ``NULL``).
    """
    supabase_url = _supabase_url()
    anon_key = _anon_key()

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{supabase_url}/rest/v1/properties",
            headers={"apikey": anon_key, "Authorization": f"Bearer {orgless_user_fixture.jwt}"},
        )
        assert resp.status_code == 200
        assert resp.json() == [], "an orgless user must read zero rows, not an error"

        resp = await client.post(
            f"{supabase_url}/rest/v1/properties",
            headers={
                "apikey": anon_key,
                "Authorization": f"Bearer {orgless_user_fixture.jwt}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
            json={
                "org_id": str(uuid.uuid4()),
                "address_line1": "1 Nowhere Lane",
                "city": "Nowhere",
                "postcode": "NW1 1AA",
                "finance_cost_classification": "residential",
            },
        )
        assert resp.status_code == 403, (
            f"expected write from an orgless user to be rejected, got {resp.status_code} {resp.text}"
        )


@pytest.mark.asyncio
async def test_authenticated_cannot_update_own_users_org_id(
    cross_tenant_fixture: _CrossTenantFixture,
) -> None:
    """A signed-in user must not be able to move themselves into another org.

    Before the 0002 fix, this only failed as a side effect of
    ``current_org_id()``'s STABLE volatility (see the migration's comment);
    now `authenticated` has no UPDATE grant on ``users`` at all, so this
    must fail with a hard permission error regardless of the org_id
    comparison's evaluation order.
    """
    supabase_url = _supabase_url()
    anon_key = _anon_key()

    async with httpx.AsyncClient() as client:
        resp = await client.patch(
            f"{supabase_url}/rest/v1/users",
            headers={
                "apikey": anon_key,
                "Authorization": f"Bearer {cross_tenant_fixture.jwt_a}",
                "Content-Type": "application/json",
            },
            params={"id": f"eq.{cross_tenant_fixture.user_a_id}"},
            json={"org_id": cross_tenant_fixture.org_b_id},
        )
        assert resp.status_code == 403, (
            f"expected self UPDATE of users.org_id to be rejected, got {resp.status_code} {resp.text}"
        )

    # Confirm via the service connection that org_id genuinely didn't move.
    conn = await asyncpg.connect(_database_url())
    try:
        org_id = await conn.fetchval(
            "select org_id from users where id = $1", uuid.UUID(cross_tenant_fixture.user_a_id)
        )
    finally:
        await conn.close()
    assert str(org_id) == cross_tenant_fixture.org_a_id


@pytest.mark.asyncio
async def test_authenticated_cannot_delete_an_org_mates_users_row(
    cross_tenant_fixture: _CrossTenantFixture,
) -> None:
    """A signed-in user must not be able to delete an org-mate's ``users`` row.

    This is the live-proven vulnerability from the adversarial review:
    user A2 is in the SAME org as user A, so the old ``using`` clause
    (``org_id = current_org_id()``) alone would have let this DELETE
    through -- org scoping doesn't distinguish "my own row" from "my
    org-mate's row". Only the grant-level revoke closes this; a cross-org
    delete attempt wouldn't have proven anything here, since that was
    already blocked by ``using`` before this fix.
    """
    supabase_url = _supabase_url()
    anon_key = _anon_key()

    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            f"{supabase_url}/rest/v1/users",
            headers={"apikey": anon_key, "Authorization": f"Bearer {cross_tenant_fixture.jwt_a}"},
            params={"id": f"eq.{cross_tenant_fixture.user_a2_id}"},
        )
        assert resp.status_code == 403, (
            f"expected DELETE of an org-mate's users row to be rejected, "
            f"got {resp.status_code} {resp.text}"
        )

    # Confirm via the service connection that the org-mate's row survives.
    conn = await asyncpg.connect(_database_url())
    try:
        remaining = await conn.fetchval(
            "select count(*) from users where id = $1", uuid.UUID(cross_tenant_fixture.user_a2_id)
        )
    finally:
        await conn.close()
    assert remaining == 1, "user A2's row must still exist after the rejected delete"
