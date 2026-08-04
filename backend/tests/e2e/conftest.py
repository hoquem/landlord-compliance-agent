"""Fixtures for the end-to-end suite, borrowed from the api suite.

The smoke test needs exactly what a router test needs -- a real org, a real
Supabase auth user behind it, and a direct RLS-bypassing connection -- so the
fixtures are imported from ``tests/api/conftest.py`` rather than written a
third time.

**``_dispose_app_engine`` is named here on purpose.** It is ``autouse=True``
where it is defined, but autouse does *not* follow a plain import into a
sibling conftest -- it has to be re-exported here to apply here. Without it
the second DB-touching test in a run checks out a pooled connection bound to
the previous test's closed event loop, and the failure only appears when
*another* directory's tests run first. ``tests/worker/conftest.py`` says the
same thing for the same reason; that is where the bug was found.
"""

from tests.api.conftest import (  # noqa: F401 -- re-exported pytest fixtures
    OrgUser,
    _dispose_app_engine,
    db,
    make_auth_user,
    make_org_user,
    org_user,
)
