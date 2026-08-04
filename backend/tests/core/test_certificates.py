"""Tests for ``src.core.certificates`` -- certificate types and expiry status.

Status is **derived, never stored** (``docs/domain/compliance.md``): a
cached "compliant" flag goes stale silently, and the staleness is invisible
precisely when it matters. So the whole of the rule is one pure function of
two dates, and the boundaries are pinned here rather than through the API.

They have to be pinned here. An API test computes "today" from the system
clock while the router computes it in ``Europe/London``; on this machine
those agree, elsewhere they need not, and a one-day disagreement at a
boundary is a flake that reproduces about once a month. API tests therefore
use generous offsets and leave the exact edges to these.
"""

import datetime

import pytest

from src.core.certificates import (
    EXPIRING_WINDOW_DAYS,
    CertificateType,
    certificate_status,
    uk_today,
)

TODAY = datetime.date(2026, 8, 4)


def days(offset: int) -> datetime.date:
    """Return the date ``offset`` days from :data:`TODAY`."""
    return TODAY + datetime.timedelta(days=offset)


@pytest.mark.parametrize(
    ("expiry", "expected"),
    [
        (days(-365), "expired"),
        (days(-1), "expired"),
        # Today is not *past*. A certificate expiring today is still good
        # today -- calling it expired would tell a landlord they are in
        # breach a day before they are.
        (days(0), "expiring"),
        (days(1), "expiring"),
        # Literal 60 and 61, deliberately not `days(EXPIRING_WINDOW_DAYS)`.
        # Deriving the offset from the constant moves the input and the
        # expectation together, so the case restates the implementation
        # instead of pinning it -- measured: with derived offsets, changing
        # the constant to 59 or 61 left these green.
        (days(60), "expiring"),
        (days(61), "valid"),
        (days(3650), "valid"),
    ],
)
def test_status_boundaries(expiry: datetime.date, expected: str) -> None:
    """Every edge of the three-way split, stated as an exact day."""
    assert certificate_status(expiry, today=TODAY) == expected


def test_the_warning_window_is_sixty_days() -> None:
    """Pinned so the constant cannot drift without a decision being made.

    It is a UX warning threshold -- how much notice someone wants before
    renewing -- and not a legal deadline. Nothing in law keys on 60 days.
    """
    assert EXPIRING_WINDOW_DAYS == 60


def test_uk_today_is_the_london_date() -> None:
    """The compliance clock is UK local, not UTC.

    A certificate expires on a date in UK law, and during BST a UTC-based
    "today" rolls over an hour late -- so for one hour each night an expiry
    would be reported against the wrong day.
    """
    import zoneinfo

    expected = datetime.datetime.now(tz=zoneinfo.ZoneInfo("Europe/London")).date()
    assert uk_today() == expected


def test_certificate_type_matches_the_schema_enum() -> None:
    """Five types, and the same five the database has.

    ``docs/domain/compliance.md`` said three until 2026-08-04; the schema
    and the spec both said five, and the glossary was the one that was
    wrong. ``tests/db/test_schema.py`` compares this enum against the live
    ``certificate_type`` type -- this only pins the Python side.
    """
    assert [t.value for t in CertificateType] == [
        "gas_safety",
        "eicr",
        "epc",
        "hmo_licence",
        "selective_licence",
    ]
