"""Compliance certificate types, and the expiry status derived from a date.

Pure core logic for the compliance context, mirroring what
:mod:`src.core.categories` is to the money context: the single source of
truth for the certificate type names, plus the one rule that turns an
expiry date into the answer a landlord actually wants.

**Status is derived and never stored.** ``docs/domain/compliance.md`` is
explicit: a cached "compliant" flag goes stale silently, and it goes stale
precisely when it matters -- the day a certificate lapses is the day the
cached value is wrong and nothing has changed to invalidate it. So this is
a function of two dates and nothing else, and no column mirrors it.

**"Expiring" is a courtesy, "expired" is the law.** Only the expired/valid
boundary has legal meaning. The 60-day window is a UX threshold -- how much
notice someone wants in order to book a gas engineer -- and no statute keys
on it. Kept as a named constant so changing it is a decision rather than a
literal edit.

:seealso: docs/domain/compliance.md (the glossary these names come from);
    backend/src/api/routers/certificates.py (the only caller).
"""

import datetime
import zoneinfo
from enum import StrEnum
from typing import Literal

#: Days before expiry at which a certificate starts being flagged. A UX
#: warning threshold, not a legal deadline -- nothing in law keys on 60
#: days. Pinned by ``test_the_warning_window_is_sixty_days``.
EXPIRING_WINDOW_DAYS = 60

#: The compliance clock. A certificate expires on a date in UK law, so the
#: date must be the UK's: during BST a UTC-derived "today" rolls over an
#: hour late, and for that hour every expiry is reported against the wrong
#: day.
_UK = zoneinfo.ZoneInfo("Europe/London")

CertificateStatus = Literal["expired", "expiring", "valid"]


class CertificateType(StrEnum):
    """The closed set of certificate types, matching the DB enum.

    Single source of truth for the Python side, exactly as
    :class:`~src.core.categories.HmrcCategory` is for HMRC categories.
    ``tests/db/test_schema.py`` compares these values against the live
    ``certificate_type`` type, so adding one here without a migration
    fails loudly rather than at the first insert.
    """

    GAS_SAFETY = "gas_safety"
    EICR = "eicr"
    EPC = "epc"
    HMO_LICENCE = "hmo_licence"
    SELECTIVE_LICENCE = "selective_licence"


def uk_today() -> datetime.date:
    """Return today's date in the UK.

    Separated from :func:`certificate_status` so that the rule stays pure
    and testable against fixed dates, while the one impure line -- reading
    a clock -- has a single home next to the timezone decision it depends
    on.

    :returns: the current date in ``Europe/London``.
    """
    return datetime.datetime.now(tz=_UK).date()


def certificate_status(
    expiry_date: datetime.date, *, today: datetime.date
) -> CertificateStatus:
    """Classify a certificate by how close its expiry is.

    ``today`` is a parameter rather than read from the clock so that the
    boundaries can be tested as exact days; callers pass :func:`uk_today`.

    A certificate expiring **today** is ``expiring``, not ``expired``: it
    is still good for the rest of the day, and calling it expired would
    tell a landlord they are in breach a day before they are.

    :param expiry_date: when the certificate stops being valid.
    :param today: the date to judge against.
    :returns: ``expired`` if expiry has passed, ``expiring`` if it falls
        within the next :data:`EXPIRING_WINDOW_DAYS` days (inclusive of
        both ends), otherwise ``valid``.
    """
    if expiry_date < today:
        return "expired"
    if expiry_date <= today + datetime.timedelta(days=EXPIRING_WINDOW_DAYS):
        return "expiring"
    return "valid"
