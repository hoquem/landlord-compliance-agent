"""Idempotent CLI that creates an org and links an existing auth user to it.

The one piece of setup that cannot be done through the API, because the API
has no unauthenticated path into it: a caller's ``(user_id, org_id)`` pair
comes from ``public.users`` (see ``src/api/auth.py``), so until a user has a
row there, every request they make is a 403. This script writes that row.

The Supabase Auth user must already exist -- create it in Supabase Studio
(Authentication > Users > Add user) or with the ``supabase`` CLI first. There
is deliberately no sign-up here: the frontend builds sign-in only, and a
setup script that could mint accounts would be a second, unaudited way into
the tenant.

Run it from the ``backend`` directory::

    uv run --env-file ../.env python scripts/seed_org.py \\
        --email m.hoque@gmail.com --org "Hoque Portfolio"

Safe to run twice: it creates the org only if no org of that name exists,
and links the user only if they aren't linked already. It refuses -- rather
than quietly re-pointing -- a user who already belongs to a *different* org,
because that would silently swap out their entire view of the portfolio.

:seealso: backend/tests/api/test_portfolio.py (the idempotency tests);
    backend/src/api/auth.py.
"""

import argparse
import asyncio
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

# Run as `python scripts/seed_org.py`, sys.path[0] is `backend/scripts`, so
# `src` is not importable without this -- which is why the imports below sit
# after a statement rather than at the top of the file. (The tests import
# this module with `backend` already on the path, from pytest's `pythonpath =
# ["."]`, and the insert is then a no-op.)
_BACKEND_ROOT = str(Path(__file__).resolve().parent.parent)
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from sqlalchemy import select, text

from src.db.models import Org, User
from src.db.session import async_session_factory, engine


@dataclass(frozen=True)
class SeedResult:
    """What one run of :func:`seed_org` found or did.

    :ivar org_id: the org the user is now in.
    :ivar org_created: ``True`` if this run created that org, ``False`` if
        it was already there.
    :ivar user_id: the ``auth.users.id`` that was linked.
    :ivar user_linked: ``True`` if this run wrote the ``public.users`` row,
        ``False`` if the user was already linked.
    """

    org_id: uuid.UUID
    org_created: bool
    user_id: uuid.UUID
    user_linked: bool


async def seed_org(*, email: str, org_name: str) -> SeedResult:
    """Ensure ``org_name`` exists and that ``email``'s auth user belongs to it.

    Everything happens in one transaction, so a run that raises leaves the
    database exactly as it found it -- no org created for a user who
    couldn't be linked.

    :param email: the email of an existing Supabase Auth user. Matched
        case-insensitively, since Auth normalises addresses it stores.
    :param org_name: the org's name, which is also this script's idempotency
        key -- rerunning with the same name reuses the same org.
    :raises RuntimeError: if no auth user has that email (the account must
        be created first); if more than one org already has that name, which
        makes "the" org ambiguous; or if the user already belongs to a
        different org.
    :returns: a :class:`SeedResult` describing what was found or done.
    """
    async with async_session_factory() as session:
        # auth.users is Supabase's table, not ours -- read it directly
        # rather than mirroring it into a model we would have to maintain.
        auth_user = (
            await session.execute(
                text("select id, email from auth.users where lower(email) = lower(:email)"),
                {"email": email},
            )
        ).one_or_none()
        if auth_user is None:
            raise RuntimeError(
                f"No Supabase Auth user with email {email}. Create the account first "
                "(Supabase Studio > Authentication > Users > Add user, or the "
                "`supabase` CLI), then run this again -- this script deliberately "
                "does not sign users up."
            )
        user_id, canonical_email = auth_user.id, auth_user.email

        named_orgs = (await session.scalars(select(Org.id).where(Org.name == org_name))).all()
        if len(named_orgs) > 1:
            raise RuntimeError(
                f"{len(named_orgs)} orgs are already named {org_name!r} ({', '.join(str(org_id) for org_id in named_orgs)}). "
                "Rerunning cannot tell which one was meant -- rename or remove the duplicates."
            )
        org_id = named_orgs[0] if named_orgs else None

        linked_org_id = await session.scalar(select(User.org_id).where(User.id == user_id))
        if linked_org_id is not None and linked_org_id != org_id:
            raise RuntimeError(
                f"User {canonical_email} ({user_id}) already belongs to org {linked_org_id}, "
                f"not to {org_name!r}. Moving a user between orgs changes everything they "
                "can see, so this script will not do it as a side effect."
            )

        org_created = org_id is None
        if org_created:
            org = Org(name=org_name)
            session.add(org)
            await session.flush()
            org_id = org.id

        user_linked = linked_org_id is None
        if user_linked:
            session.add(User(id=user_id, org_id=org_id, email=canonical_email))

        await session.commit()

    return SeedResult(
        org_id=org_id, org_created=org_created, user_id=user_id, user_linked=user_linked
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line.

    :param argv: arguments to parse, or ``None`` for ``sys.argv``.
    :returns: the parsed ``email`` and ``org_name``.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Create an org and link an existing Supabase Auth user to it. "
            "Idempotent: safe to run more than once."
        )
    )
    parser.add_argument("--email", required=True, help="email of an existing Supabase Auth user")
    parser.add_argument(
        "--org", required=True, dest="org_name", help="org name; also the idempotency key"
    )
    return parser.parse_args(argv)


async def _run(argv: list[str] | None = None) -> None:
    """Seed from the command line and report what happened.

    :param argv: arguments to parse, or ``None`` for ``sys.argv``.
    """
    args = _parse_args(argv)
    try:
        result = await seed_org(email=args.email, org_name=args.org_name)
    finally:
        # This is a one-shot process, so the module-level engine's pool has
        # to be closed explicitly or asyncpg complains on the way out.
        await engine.dispose()

    print(f"Org {args.org_name!r}: {'created' if result.org_created else 'already existed'} ({result.org_id})")
    print(f"User {args.email}: {'linked' if result.user_linked else 'already linked'} ({result.user_id})")
    print()
    print("Next steps:")
    print("  1. Sign in as this user in the app to get an access token.")
    print("  2. Add your ownership entities:  POST /entities")
    print("     {\"name\": \"...\", \"tax_regime\": \"mtd_itsa\" | \"corporation_tax\"}")
    print("  3. Add your properties:          POST /properties")
    print("     {\"address_line1\": \"...\", \"city\": \"...\", \"postcode\": \"...\",")
    print("      \"finance_cost_classification\": \"residential\" | \"non_residential\"}")
    print("  4. Set who owns what:            PUT /properties/{id}/ownership")
    print("     [{\"entity_id\": \"...\", \"percentage\": \"100.00\"}]  (must sum to 100)")


if __name__ == "__main__":
    asyncio.run(_run())
