"""Shared fixtures/helpers for the ``backend/tests/db`` suite.

Both ``test_schema.py`` and ``test_rls.py`` need the same "read this env var
or fail loudly" helpers and the same set of 13 MVP table names -- kept here
once rather than duplicated in both files (they had drifted into two
copies of each before this was extracted).

Deliberately NOT here: the Auth-admin user-creation sequence used by
``test_rls.py``'s ``cross_tenant_fixture``/``orgless_user_fixture`` -- YAGNI
until a second file actually needs it (see Task 13).
"""

import os

import pytest

from src.db.session import engine


@pytest.fixture(autouse=True)
async def _dispose_app_engine():
    """Drop the app engine's pooled connections after every test in ``tests/db``.

    ``test_models_roundtrip.py`` uses the module-level ``src.db.session``
    engine, whose pool outlives the per-test event loop pytest-asyncio's auto
    mode creates -- so without this it leaves connections bound to a closed
    loop and the next test to reach for that engine dies with ``RuntimeError:
    Event loop is closed``. That next test is not necessarily in this
    directory: it was reachable as ``pytest tests/db tests/api``, which only
    appeared to pass because the first ``tests/api`` test collected happens to
    401 before touching the database.

    A no-op for ``test_schema.py``/``test_rls.py``, which use raw asyncpg and
    never touch this engine. Mirrors ``tests/api/conftest.py``'s fixture of
    the same name; two copies rather than one shared helper, because the
    directory each one names is the useful part of the docstring.

    Importing ``engine`` eagerly costs nothing here -- ``tests/db`` already
    fails loudly at import without ``DATABASE_URL``.
    """
    yield
    await engine.dispose()

#: The 13 MVP tables from the spec's "Data model" > "Core (MVP)" list --
#: also the set ``test_rls.py`` guards for RLS-enabled + org-scoped
#: policies.
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
        rule).
    :returns: the connection string to use for a direct (service-role
        equivalent) Postgres connection.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Start the local Supabase stack "
            "(`supabase start` from the repo root) and run this test with "
            "`uv run --env-file ../.env pytest tests/db/`, "
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
            "`uv run --env-file ../.env pytest tests/db/`."
        )
    return url


def _anon_key() -> str:
    """Read ``SUPABASE_ANON_KEY`` from the environment.

    :raises RuntimeError: if unset.
    :returns: the anon API key used for every PostgREST/Auth request in
        this suite -- the real client-side key, paired with a real user
        JWT, so the tests exercise the exact path the Flutter frontend
        will use.
    """
    key = os.environ.get("SUPABASE_ANON_KEY")
    if not key:
        raise RuntimeError(
            "SUPABASE_ANON_KEY is not set. Run this test with "
            "`uv run --env-file ../.env pytest tests/db/`."
        )
    return key


def _service_role_key() -> str:
    """Read ``SUPABASE_SERVICE_ROLE_KEY`` from the environment.

    :raises RuntimeError: if unset.
    :returns: the service-role key, used only to call the Auth admin API
        (create/delete users) -- never used against PostgREST in this
        suite.
    """
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not key:
        raise RuntimeError(
            "SUPABASE_SERVICE_ROLE_KEY is not set. Run this test with "
            "`uv run --env-file ../.env pytest tests/db/`."
        )
    return key


# ---------------------------------------------------------------------------
# Fixtures borrowed from the api suite, for `test_rls_enforced.py`.
#
# That module needs two real orgs to prove one cannot see the other, which is
# exactly what `make_org_user` builds. Imported rather than written a fourth
# time -- and `_dispose_app_engine` is named here for the reason its own
# docstring gives: autouse does not follow a plain import into a sibling
# conftest, and there are three connection pools now.
# ---------------------------------------------------------------------------
from tests.api.conftest import (  # noqa: F401 -- re-exported fixtures
    OrgUser,
    _dispose_app_engine,
    db,
    make_auth_user,
    make_org_user,
    org_user,
)
