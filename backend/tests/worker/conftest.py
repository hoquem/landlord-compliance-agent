"""Fixtures for the worker suite, borrowed from the api suite.

The worker needs the same scaffolding every router test needs -- a real org,
a real Supabase auth user behind it, and a direct RLS-bypassing connection --
so the fixtures are imported from ``tests/api/conftest.py`` rather than
written a second time. pytest resolves fixtures by name in the importing
module's namespace, so a plain import is all a re-export takes.

**``_dispose_app_engine`` is in that list on purpose, and it is the one that
matters.** It is ``autouse=True`` where it is defined, but autouse does *not*
follow a plain import into a sibling conftest -- it has to be named here to
apply here. Task 13a's whole bug was this shape: ``src.db.session`` holds a
module-level engine with a connection pool, pytest-asyncio gives each test a
fresh event loop, and a pooled connection bound to a closed loop only
explodes when some *other* directory's tests run first. Both orderings --
``pytest tests/worker tests/api`` and ``pytest tests/api tests/worker`` --
were run before this file was considered finished, because a single-directory
run cannot show the asymmetry.
"""

from tests.api.conftest import (  # noqa: F401 -- re-exported pytest fixtures
    OrgUser,
    _dispose_app_engine,
    db,
    make_auth_user,
    make_org_user,
    org_user,
)
