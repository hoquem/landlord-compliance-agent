"""Schema guard tests for the core migration (``supabase/migrations/0001_core.sql``).

Connects directly to the database identified by the ``DATABASE_URL``
environment variable -- no default, no skip. The whole point of this test
is to fail loudly if the schema is missing or has drifted, not to quietly
pass (or quietly skip) when the DB is unreachable.

Run locally (from ``backend/``), with the Supabase local stack running
(``supabase status`` from the repo root to confirm)::

    uv run --env-file ../.env pytest tests/db/test_schema.py -v

:seealso: docs/superpowers/specs/2026-07-28-landlord-compliance-agent-design.md,
    "Data model" section.
"""

import os

import asyncpg
import pytest

#: The 13 MVP tables from the spec's "Data model" > "Core (MVP)" list.
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

#: The exact 15 HMRC categories from the spec, in spec order. This is the
#: value backend/src/core/categories.py's HmrcCategory StrEnum (Task 7)
#: must mirror exactly -- this test is what catches the two drifting apart.
EXPECTED_HMRC_CATEGORIES = [
    "rent_income",
    "other_property_income",
    "rent_paid",
    "rates_insurance_ground",
    "repairs_maintenance",
    "finance_costs_residential",
    "finance_costs_nonresidential",
    "legal_professional",
    "service_costs",
    "travel_vehicle",
    "other_allowable",
    "replacement_domestic_items",
    "use_of_home_allowance",
    "capital_expense",
    "personal_non_business",
]


def _database_url() -> str:
    """Read ``DATABASE_URL`` from the environment.

    :raises RuntimeError: if unset. A schema guard test that silently
        skips when it can't reach a database is a hole in the guard, not
        a passing test -- fail loudly instead (house rule).
    :returns: the connection string to use.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Start the local Supabase stack "
            "(`supabase start` from the repo root) and run this test with "
            "`uv run --env-file ../.env pytest tests/db/test_schema.py`, "
            "or export DATABASE_URL yourself."
        )
    return url


@pytest.mark.asyncio
async def test_all_core_tables_exist() -> None:
    """All 13 spec tables must exist in the ``public`` schema."""
    conn = await asyncpg.connect(_database_url())
    try:
        rows = await conn.fetch(
            """
            select table_name
            from information_schema.tables
            where table_schema = 'public' and table_type = 'BASE TABLE'
            """
        )
    finally:
        await conn.close()

    actual_tables = {row["table_name"] for row in rows}
    missing = EXPECTED_TABLES - actual_tables
    assert not missing, f"Missing tables in public schema: {sorted(missing)}"


@pytest.mark.asyncio
async def test_transactions_hmrc_category_column_uses_the_enum_type() -> None:
    """``transactions.hmrc_category`` must actually be typed ``hmrc_category``.

    Without this, a test that only inspects the standalone ``hmrc_category``
    enum type would still pass even if the column were dropped, renamed, or
    retyped to plain ``text`` on the ``transactions`` table -- exactly the
    drift this guard exists to catch.
    """
    conn = await asyncpg.connect(_database_url())
    try:
        udt_name = await conn.fetchval(
            """
            select udt_name
            from information_schema.columns
            where table_schema = 'public'
              and table_name = 'transactions'
              and column_name = 'hmrc_category'
            """
        )
    finally:
        await conn.close()

    assert udt_name == "hmrc_category", (
        f"transactions.hmrc_category is not typed as the hmrc_category enum "
        f"(found udt_name={udt_name!r})"
    )


@pytest.mark.asyncio
async def test_hmrc_category_enum_has_exactly_15_spec_values() -> None:
    """The ``hmrc_category`` enum type must have exactly the 15 spec values.

    Guards against drift between this SQL enum and the Python
    ``HmrcCategory`` StrEnum (Task 7).
    """
    conn = await asyncpg.connect(_database_url())
    try:
        rows = await conn.fetch(
            """
            select e.enumlabel
            from pg_enum e
            join pg_type t on e.enumtypid = t.oid
            where t.typname = 'hmrc_category'
            order by e.enumsortorder
            """
        )
    finally:
        await conn.close()

    actual_values = [row["enumlabel"] for row in rows]
    assert actual_values == EXPECTED_HMRC_CATEGORIES, (
        "hmrc_category enum drifted from spec.\n"
        f"expected ({len(EXPECTED_HMRC_CATEGORIES)}): {EXPECTED_HMRC_CATEGORIES}\n"
        f"actual   ({len(actual_values)}): {actual_values}"
    )


@pytest.mark.asyncio
async def test_mtd_quarters_tax_year_check_rejects_bad_format() -> None:
    """``tax_year`` must match the pinned UK tax-year convention, e.g. '2026-27'.

    Row identity (the entity/tax_year/quarter/version unique constraint)
    and the Task 16 cumulative-decrease guard both key on this exact
    string, so a malformed value must be rejected at the DB -- not just
    by convention -- to keep the format single-sourced.
    """
    conn = await asyncpg.connect(_database_url())
    try:
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            async with conn.transaction():
                org_id = await conn.fetchval(
                    "insert into orgs (name) values ('schema test org') returning id"
                )
                entity_id = await conn.fetchval(
                    """
                    insert into entities (org_id, name, tax_regime)
                    values ($1, 'schema test entity', 'mtd_itsa')
                    returning id
                    """,
                    org_id,
                )
                await conn.execute(
                    """
                    insert into mtd_quarters (org_id, entity_id, tax_year, quarter)
                    values ($1, $2, '2026', 'Q1')
                    """,
                    org_id,
                    entity_id,
                )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_transactions_import_id_is_nullable() -> None:
    """``import_id`` must be nullable.

    Null represents a manually-entered claim with no bank statement line
    (e.g. flat-rate use_of_home_allowance). The MVP write path (imports
    endpoint) always sets it; manual-claim entry arrives in a later
    iteration.
    """
    conn = await asyncpg.connect(_database_url())
    try:
        is_nullable = await conn.fetchval(
            """
            select is_nullable
            from information_schema.columns
            where table_schema = 'public'
              and table_name = 'transactions'
              and column_name = 'import_id'
            """
        )
    finally:
        await conn.close()

    assert is_nullable == "YES", (
        f"transactions.import_id should be nullable (found is_nullable={is_nullable!r})"
    )


def test_database_url_raises_loudly_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_database_url()`` must fail loudly, not skip, when unset."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
        _database_url()


class _RollbackTestTransaction(Exception):
    """Internal sentinel raised to force a rollback of a successful test transaction."""


@pytest.mark.asyncio
async def test_deleting_import_cascades_to_its_transactions() -> None:
    """Deleting an import must delete its transactions with it (ON DELETE CASCADE).

    Confirms the Task 5 review controller decision: null ``import_id``
    already has a distinct meaning (manual claim), so orphaning
    bank-derived transaction rows via SET NULL would silently misclassify
    them -- CASCADE is correct here, unlike every other FK in this schema.
    """
    conn = await asyncpg.connect(_database_url())
    try:
        async with conn.transaction():
            org_id = await conn.fetchval(
                "insert into orgs (name) values ('cascade test org') returning id"
            )
            entity_id = await conn.fetchval(
                """
                insert into entities (org_id, name, tax_regime)
                values ($1, 'cascade test entity', 'mtd_itsa')
                returning id
                """,
                org_id,
            )
            import_id = await conn.fetchval(
                """
                insert into imports (org_id, entity_id, file_path, source_bank)
                values ($1, $2, 'statements/test.csv', 'Test Bank')
                returning id
                """,
                org_id,
                entity_id,
            )
            transaction_id = await conn.fetchval(
                """
                insert into transactions
                    (org_id, import_id, entity_id, date, amount, direction, description)
                values
                    ($1, $2, $3, current_date, 10.00, 'in', 'cascade test line')
                returning id
                """,
                org_id,
                import_id,
                entity_id,
            )

            await conn.execute("delete from imports where id = $1", import_id)

            remaining = await conn.fetchval(
                "select count(*) from transactions where id = $1", transaction_id
            )
            assert remaining == 0, (
                "transaction row survived deletion of its import; "
                "expected ON DELETE CASCADE on transactions.import_id"
            )

            # Roll back so this test leaves no residual data behind.
            raise _RollbackTestTransaction
    except _RollbackTestTransaction:
        pass
    finally:
        await conn.close()
