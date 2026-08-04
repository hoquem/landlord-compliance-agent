"""UK tax-year quarters, cumulative YTD totals, and MTD ITSA deadlines.

Pure, dependency-free core logic -- everything here operates on data passed
in by the caller (transactions, ownership maps) and never touches the DB.
:func:`format_tax_year` is the single source of the ``mtd_quarters.tax_year``
string format (``^\\d{4}-\\d{2}$``, e.g. ``"2026-27"``); the SQL CHECK
constraint in ``supabase/migrations/0001_core.sql`` documents that this
function is the one place that produces it.

:seealso: spec HMRC PIM1035 (ownership attribution), MTD ITSA quarterly
    update deadlines (7th of the month following quarter end).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from src.core.categories import EXCLUDED_FROM_EXPORT, HmrcCategory
from src.core.splits import InvalidOwnershipError, split_amount

# EXCLUDED_FROM_EXPORT (categories.py) is the source of truth for "never on
# an HMRC export": personal_non_business and capital_expense. Only
# personal_non_business is dropped from cumulative_totals' result entirely
# -- capital_expense keeps its own key (see cumulative_totals docstring), so
# it is carved back out here rather than hand-listing personal_non_business
# alone. A future category added to EXCLUDED_FROM_EXPORT is dropped by
# default, matching personal_non_business, unless explicitly carved out too.
_DROPPED_CATEGORIES = EXCLUDED_FROM_EXPORT - {HmrcCategory.CAPITAL_EXPENSE}

# UK tax year runs 6 April - 5 April. Quarters within it start on the 6th
# of Apr/Jul/Oct/Jan (Q4 crosses the calendar-year boundary).
_MIN_TAX_YEAR_START = 2020
_MAX_TAX_YEAR_START = 2100

# (quarter number, month, day) of each quarter's start, relative to the
# tax year's start calendar year -- Q4's start falls in start_year + 1.
_QUARTER_STARTS = (
    (1, 4, 6),
    (2, 7, 6),
    (3, 10, 6),
    (4, 1, 6),
)

# The statutory MTD ITSA quarterly-update deadline is the 7th of the month
# following quarter end, irrespective of which tax year the quarter
# belongs to -- so these four (month, day) pairs recur every calendar year.
_DEADLINE_MONTH_DAYS = ((2, 7), (5, 7), (8, 7), (11, 7))


class MissingOwnershipError(KeyError):
    """Raised when a property-allocated transaction's property is unknown.

    Silently treating it as wholly owned would misattribute a tax figure,
    so the lookup is loud. It is a distinguishable class rather than a bare
    ``KeyError`` because callers turn it into a user-facing message: a
    handler catching plain ``KeyError`` around this call tree would dress a
    future dict-lookup bug in *this* module up as the user's data problem.
    Subclasses ``KeyError`` so callers written before it existed still work.

    :ivar property_id: the property with no entry in the ownership map.
    """

    def __init__(self, property_id: UUID) -> None:
        self.property_id = property_id
        super().__init__(
            f"property {property_id} has no ownership shares; it cannot be "
            "attributed, and assuming sole ownership would misstate a tax figure"
        )


def quarter_for(d: date) -> tuple[int, int]:
    """Return the UK MTD tax-year quarter containing ``d``.

    Quarters: Q1 6 Apr-5 Jul, Q2 6 Jul-5 Oct, Q3 6 Oct-5 Jan, Q4 6 Jan-5 Apr.

    :param d: any calendar date.
    :returns: ``(tax_year_start_year, quarter)`` where ``quarter`` is 1-4
        and ``tax_year_start_year`` is the calendar year the tax year
        *started* in -- e.g. a date in Jan-Apr belongs to the tax year
        that started the previous calendar year, so
        ``quarter_for(date(2027, 2, 1)) == (2026, 4)``.
    """
    tax_year_start = d.year if d >= date(d.year, 4, 6) else d.year - 1

    quarter = 1
    for q, month, day in _QUARTER_STARTS:
        # Q4's start month/day fall in the calendar year after
        # tax_year_start (Jan), every other quarter starts in
        # tax_year_start itself.
        start_calendar_year = tax_year_start + 1 if q == 4 else tax_year_start
        if d >= date(start_calendar_year, month, day):
            quarter = q

    return tax_year_start, quarter


def _quarter_end_date(tax_year_start: int, quarter: int) -> date:
    """Return the last calendar day of ``quarter`` within a tax year.

    :param tax_year_start: calendar year the tax year started in.
    :param quarter: 1-4.
    :raises ValueError: if ``quarter`` is not in 1-4.
    """
    if quarter not in (1, 2, 3, 4):
        raise ValueError(f"quarter must be 1-4, got {quarter}")

    # End date is the day before the *next* quarter's start (Q4's "next"
    # start is Q1 of the following tax year, 6 Apr of start_year + 1).
    ends = {
        1: date(tax_year_start, 7, 5),
        2: date(tax_year_start, 10, 5),
        3: date(tax_year_start + 1, 1, 5),
        4: date(tax_year_start + 1, 4, 5),
    }
    return ends[quarter]


def _validate_start_year(start_year: int) -> None:
    """Validate a tax-year start year, shared by every function keyed on one.

    :param start_year: calendar year the tax year starts in (6 April).
    :raises ValueError: if ``start_year`` is outside 2020-2100. MTD ITSA
        did not exist before then, so earlier "tax years" are meaningless
        in this system; beyond 2100 :func:`format_tax_year`'s two-digit
        end-year suffix starts colliding with the *next* century's tax
        years (e.g. a hypothetical 2199-00 would be visually
        indistinguishable from 2099-00), so the format itself becomes
        ambiguous rather than merely unused. Enforced here too (not just
        in ``format_tax_year``) so an out-of-range year used only for date
        arithmetic -- e.g. in :func:`cumulative_totals` -- fails at the
        point of use instead of surfacing later as a confusing string.
    """
    if start_year < _MIN_TAX_YEAR_START or start_year > _MAX_TAX_YEAR_START:
        raise ValueError(
            f"tax year start {start_year} outside supported range "
            f"{_MIN_TAX_YEAR_START}-{_MAX_TAX_YEAR_START}"
        )


def format_tax_year(start_year: int) -> str:
    """Format a tax year as HMRC/DB notation, e.g. ``"2026-27"``.

    Single source of the ``mtd_quarters.tax_year`` string format (see the
    module docstring) -- callers must not hand-roll this string elsewhere.

    :param start_year: calendar year the tax year starts in (6 April).
    :returns: ``"{start_year}-{end_year mod 100, zero-padded}"``, e.g.
        ``format_tax_year(2099) == "2099-00"``.
    :raises ValueError: see :func:`_validate_start_year`.
    """
    _validate_start_year(start_year)
    end_suffix = (start_year + 1) % 100
    return f"{start_year}-{end_suffix:02d}"


def next_update_deadline(today: date) -> date:
    """Return the next statutory MTD ITSA quarterly-update deadline.

    Deadlines fall on the 7th of Feb/May/Aug/Nov (the month following each
    quarter's end). Deadlines are inclusive-today: if ``today`` itself is a
    deadline day, the update can still be filed today, so ``today`` is
    returned as "the next deadline".

    :param today: the date to search forward from (inclusive).
    :returns: the nearest deadline date on or after ``today``.
    """
    candidates = [
        date(year, month, day)
        for year in (today.year, today.year + 1)
        for month, day in _DEADLINE_MONTH_DAYS
    ]
    return min(c for c in candidates if c >= today)


@dataclass(frozen=True)
class TxnForTotals:
    """A lightweight transaction record for :func:`cumulative_totals`.

    Deliberately not the SQLAlchemy model -- keeps this module DB-free and
    testable with plain data.

    :ivar date: transaction date.
    :ivar amount: penny-exact amount attributed to the whole transaction
        (pre-ownership-split). Sign convention (enforced in application
        code only -- the ``mtd_quarters.*_total`` columns carry no DB
        CHECK on sign) is derived, not copied straight from a
        ``transactions`` row's ``amount``/``direction`` pair:
        ``amount`` here is positive iff
        ``(hmrc_category in INCOME_CATEGORIES) == (direction == "in")``.
        Concretely: rent received (income category, direction ``in``) is
        positive; a tenant refund (income category, direction ``out``) is
        negative; a repair paid (expense category, direction ``out``) is
        positive; a contractor refund (expense category, direction
        ``in``) is negative. Callers building a :class:`TxnForTotals` from
        a DB ``transactions`` row MUST apply this rule -- the row only
        carries magnitude + direction, never a pre-signed amount.
    :ivar hmrc_category: the classified HMRC category.
    :ivar entity_id: ``transactions.entity_id`` -- the bank-account owner
        (provenance), used directly only when ``property_id`` is null.
    :ivar property_id: the property this line is allocated to, or ``None``
        for unallocated/non-property lines (e.g. ``use_of_home_allowance``,
        ``travel_vehicle``).
    """

    date: date
    amount: Decimal
    hmrc_category: HmrcCategory
    entity_id: UUID
    property_id: UUID | None


def cumulative_totals(
    transactions: Sequence[TxnForTotals],
    entity_id: UUID,
    tax_year: int,
    quarter: int,
    ownerships: Mapping[UUID, Mapping[UUID, Decimal]],
) -> dict[HmrcCategory, Decimal]:
    """Compute YTD per-category totals for one entity, 6 Apr through quarter end.

    Property-allocated lines are attributed via :func:`split_amount` using
    that property's ownership percentages (HMRC PIM1035) -- never via
    ``transactions.entity_id``. Lines with no ``property_id`` (e.g.
    ``use_of_home_allowance``, ``travel_vehicle``) attribute 100% to
    ``transactions.entity_id`` instead, since there is no property to split
    by.

    ``personal_non_business`` lines are dropped entirely -- by definition
    they are never reported to HMRC, so the category never appears as a key
    in the result. ``capital_expense`` lines DO appear as their own key
    (mirroring the ``capital_expense_total`` column on ``mtd_quarters``),
    but are understood by callers as not-allowable -- this function does
    not fold them into any other category.

    :param transactions: candidate transactions (e.g. all ``confirmed``
        transactions for the org); lines outside the YTD window or
        belonging to a different entity's unallocated ledger are ignored.
    :param entity_id: the entity to compute totals for.
    :param tax_year: calendar year the tax year starts in (6 April) -- the
        first element of :func:`quarter_for`'s return value.
    :param quarter: 1-4, the quarter whose end date closes the YTD window.
    :param ownerships: ``property_id -> {entity_id: percentage}`` for every
        property referenced by ``transactions``. A property-allocated
        transaction whose ``property_id`` is missing from this mapping
        raises :class:`MissingOwnershipError` -- silently treating it as
        100% owned would misattribute tax figures.
    :returns: mapping of :class:`HmrcCategory` to the entity's penny-exact
        YTD total for that category. Categories with no attributable
        transaction in the window are simply absent (not zero-valued).
    :raises MissingOwnershipError: if a transaction's ``property_id`` has
        no entry in ``ownerships``. A ``KeyError`` subclass, so a caller
        written against the older bare ``KeyError`` still catches it.
    :raises ValueError: see :func:`_validate_start_year`.
    """
    _validate_start_year(tax_year)
    window_start = date(tax_year, 4, 6)
    window_end = _quarter_end_date(tax_year, quarter)

    totals: dict[HmrcCategory, Decimal] = {}

    for txn in transactions:
        if not (window_start <= txn.date <= window_end):
            continue
        if txn.hmrc_category in _DROPPED_CATEGORIES:
            continue

        if txn.property_id is None:
            if txn.entity_id != entity_id:
                continue
            contribution = txn.amount
        else:
            if txn.property_id not in ownerships:
                raise MissingOwnershipError(txn.property_id)
            shares = ownerships[txn.property_id]
            if entity_id not in shares:
                continue
            try:
                contribution = split_amount(txn.amount, shares)[entity_id]
            except InvalidOwnershipError as exc:
                # `split_amount` only sees a share map, so it cannot say
                # whose it is. This is the innermost place that knows, and
                # the message surfaces to a user through the export
                # endpoint's 422 -- across a twelve-property portfolio,
                # "shares sum to 50" without a property id says a return
                # cannot be filed and nothing about how to fix it.
                raise InvalidOwnershipError(
                    f"property {txn.property_id}: {exc}", exc.shares
                ) from exc

        totals[txn.hmrc_category] = (
            totals.get(txn.hmrc_category, Decimal("0.00")) + contribution
        )

    return totals
