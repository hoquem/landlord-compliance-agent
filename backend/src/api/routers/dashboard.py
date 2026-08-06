"""One screen's worth of "what needs you", in one round trip.

A purpose-built endpoint rather than three generic ones the frontend then
stitches together. The dashboard asks a single question -- *what is waiting
on me?* -- and answering it client-side would mean fetching every
transaction and every certificate to count two numbers.

**The deadline is computed here, never in the frontend.**
:func:`~src.core.quarters.next_update_deadline` is the one implementation,
unit-tested against all four statutory dates (7 Feb / 7 May / 7 Aug /
7 Nov). A second copy in Dart would be a second opinion about a tax
deadline, and the failure mode is a user who files late because the app
told them the wrong day.

**Every query still filters ``org_id``**, and since 2026-08-06 the database
enforces it too: the API connects as ``app_api``, a role with row-level
security applied, so an unfiltered query returns *this org's* rows rather than
everyone's. The filter is now the first of two defences instead of the only
one -- keep writing it (it is what makes the intent readable, and RLS is a
backstop, not a design), but a slip is no longer a leak. Proved by
``tests/db/test_rls_enforced.py``, which queries with no ``where`` at all.
these filters are the whole tenant boundary -- and a leak here would
overstate someone's outstanding work with another org's rows.

:seealso: backend/src/core/quarters.py; backend/src/core/certificates.py.
"""

import datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from src.api.auth import CurrentAuth
from src.core.certificates import certificate_status, uk_today
from src.core.quarters import next_update_deadline
from src.db.models import ComplianceCertificate, Import, Transaction
from src.db.session import org_session

router = APIRouter(tags=["dashboard"])


class DashboardRead(BaseModel):
    """What is waiting on the user right now.

    :ivar needs_decision: transactions still ``unclassified`` or
        ``proposed``. Both count: an agent's suggestion nobody accepted is
        not a decision, and an export refuses while one remains.
    :ivar next_deadline: the next statutory quarterly-update date.
    :ivar days_until_deadline: whole days from today, UK clock. Negative if
        the deadline has passed, which is worth showing rather than hiding.
    :ivar expiring_certificates: certificates within 60 days of lapsing.
    :ivar expired_certificates: certificates already lapsed.
    :ivar unreadable_imports: files the parser refused. Bad input; the fix is
        a different export from the bank.
    :ivar uncategorised_imports: files that parsed cleanly and whose
        categorisation then failed. **Not the same problem** -- the data is
        fine and sitting there, and the fix is on our side. They were one
        field until 2026-08-05, which rendered as "2 imports could not be
        read" when one of them had been read perfectly.
    """

    needs_decision: int
    next_deadline: datetime.date
    days_until_deadline: int
    expiring_certificates: int
    expired_certificates: int
    unreadable_imports: int
    uncategorised_imports: int


@router.get("/dashboard")
async def get_dashboard(auth: CurrentAuth) -> DashboardRead:
    """Summarise what needs the caller's attention.

    :param auth: the authenticated caller.
    :returns: the counts and the next deadline.
    """
    today = uk_today()

    async with org_session(auth.user_id) as session:
        needs_decision = await session.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(
                Transaction.org_id == auth.org_id,
                Transaction.status.in_(("unclassified", "proposed")),
            )
        )

        async def count_imports(status: str) -> int:
            return await session.scalar(
                select(func.count())
                .select_from(Import)
                .where(Import.org_id == auth.org_id, Import.status == status)
            )

        unreadable_imports = await count_imports("failed")
        uncategorised_imports = await count_imports("categorisation_failed")
        # Statuses are derived, never stored, so the rows come back and the
        # counting happens against `certificate_status` -- the same function
        # the certificates screen shows. Counting in SQL would be a second
        # implementation of the expiry rule.
        expiry_dates = await session.scalars(
            select(ComplianceCertificate.expiry_date).where(
                ComplianceCertificate.org_id == auth.org_id
            )
        )
        expiring = 0
        expired = 0
        for expiry in expiry_dates:
            match certificate_status(expiry, today=today):
                case "expiring":
                    expiring += 1
                case "expired":
                    expired += 1

    deadline = next_update_deadline(today)
    return DashboardRead(
        needs_decision=needs_decision or 0,
        next_deadline=deadline,
        days_until_deadline=(deadline - today).days,
        expiring_certificates=expiring,
        expired_certificates=expired,
        unreadable_imports=unreadable_imports or 0,
        uncategorised_imports=uncategorised_imports or 0,
    )
