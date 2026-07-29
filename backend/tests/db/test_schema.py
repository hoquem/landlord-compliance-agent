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
