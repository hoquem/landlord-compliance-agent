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
