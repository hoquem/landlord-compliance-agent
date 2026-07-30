"""Org-scoped row lookup: the one place a router turns an id into a row.

Every table in this schema is org-scoped, and ``src/db/session.py`` connects
as the ``postgres`` superuser (as ``DATABASE_URL`` is configured), so the
row-level-security policies of ``supabase/migrations/0002_rls.sql`` -- which
protect the PostgREST path the Flutter app uses directly -- are inert on
everything the API does. There is no backstop underneath a forgotten
``org_id`` filter, only a silent cross-tenant leak.

"Look a row up by id, in the caller's org, or 404" is the shape that recurs
in every org-scoped router. It lives here, in a module of its own, for two
reasons:

* **It cannot be called with the wrong org.** :func:`get_owned_or_404` takes
  the whole :class:`~src.api.auth.AuthContext`, never a bare ``org_id``, so
  the parameter that must be right is the one a caller cannot omit or
  mistype. A helper taking ``org_id: uuid.UUID`` would accept any UUID in
  scope -- including the resource id sitting right next to it.
* **404-vs-403 is decided once.** :func:`not_found` raises the same 404
  whether the row is missing or belongs to someone else. A 403 for the
  second case would confirm to the caller that the id is real, turning every
  endpoint into an existence oracle over other tenants' ids. One source for
  that response is what makes the two indistinguishable *by construction*
  rather than by six routers happening to agree.

Not in ``routers/portfolio.py``: the other routers would then import a
helper out of a sibling router, which is the wrong dependency direction.

Deliberately narrow. Queries that are not "one row by id" -- list selects,
deletes, existence probes, set-membership filters -- stay written out in the
router that runs them, where their ``org_id`` filter is visible at the point
of use. The case for centralising this one is that it is *identical* every
time it appears, which is where a silent copy-paste omission hides.

:seealso: backend/src/api/auth.py (the ``org_id`` this module filters on);
    backend/tests/api/test_portfolio.py (its tenant-isolation section, which
    is what holds this up -- ``get_owned_or_404`` ignoring ``auth.org_id``
    was checked to fail eight of those tests, and rewording the cross-org
    404 to fail four; neither was assumed).
"""

import uuid
from typing import Protocol

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped

from src.api.auth import AuthContext


class OrgScoped(Protocol):
    """A mapped model carrying the two columns :func:`get_owned_or_404` filters on.

    ``src/db/models.py`` builds these with the ``_uuid_pk()`` and
    ``_org_id_fk()`` column factories rather than a shared mixin, so there is
    no base class to type ``model`` against. This states the structural
    requirement instead: any model with both columns fits, and one missing
    ``org_id`` -- an unscoped table -- does not.

    :ivar id: the row's primary key.
    :ivar org_id: the org the row belongs to.
    """

    id: Mapped[uuid.UUID]
    org_id: Mapped[uuid.UUID]


def not_found(what: str, resource_id: uuid.UUID) -> HTTPException:
    """Build the 404 used for "no such row *in your org*".

    Deliberately the same response whether the row doesn't exist or belongs
    to another org: a 403 for the second case would confirm to the caller
    that the id is real. Both cases come from this one function, so they are
    identical by construction -- pinned by
    ``test_a_cross_org_404_is_identical_to_a_nonexistent_one``, since the
    construction is one refactor away from changing.

    :param what: the resource kind, for the message.
    :param resource_id: the id that couldn't be used.
    :returns: a 404 :class:`~fastapi.HTTPException`.
    """
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"No {what} with id {resource_id} in this org.",
    )


async def get_owned_or_404[RowT: OrgScoped](
    session: AsyncSession,
    model: type[RowT],
    resource_id: uuid.UUID,
    auth: AuthContext,
    *,
    what: str,
) -> RowT:
    """Load one row by id from the caller's org, or raise the 404.

    The org filter is part of the statement that *finds* the row, not a
    check applied to it afterwards, so a caller cannot act on another org's
    row even for the instant between the select and the test.

    :param session: the session to query in. The row is returned attached to
        it, so a caller may mutate it and flush within the same transaction.
    :param model: the mapped model to select, e.g.
        :class:`~src.db.models.Entity`.
    :param resource_id: the id to look up.
    :param auth: the authenticated caller. Their ``org_id`` is the filter --
        taken as the whole context rather than a bare id so that it cannot
        be omitted or confused with another UUID in scope.
    :param what: the resource kind, for the 404 message, e.g. ``"entity"``.
    :raises HTTPException: 404 if no such row exists *in the caller's org*.
        Identical to the 404 for an id that exists nowhere -- see
        :func:`not_found`.
    :returns: the row.
    """
    row = await session.scalar(
        select(model).where(model.id == resource_id, model.org_id == auth.org_id)
    )
    if row is None:
        raise not_found(what, resource_id)
    return row
