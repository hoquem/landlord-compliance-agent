"""Building one quarter's export pack for one entity.

Pure, dependency-free core logic: database rows in, CSV text out. It does not
read, write, or know about the database -- so the refusal rules below are
testable with plain data.

**The sign rule, which is the single most dangerous thing in this module.**
A ``transactions`` row carries a *magnitude* plus a *direction*, and never a
signed amount. The sign an export needs depends on the **category** as well:
money arriving against an *expense* category is a refund and must **reduce**
that expense, not add to income. So

    positive iff (category is income) == (direction is "in")

Copying ``amount`` straight through is right for rent received and repairs
paid -- the two cases anyone tests by hand -- and wrong for the two that
matter: a tenant refund and a contractor refund. See :func:`signed_amount`,
and ``src/core/quarters.py``'s :class:`~src.core.quarters.TxnForTotals`
docstring, which states the same rule for the same reason.

**Refusing to export is a feature.** If any transaction inside the period is
still ``unclassified`` or ``proposed``, nothing is generated and
:class:`ExportBlockedError` names the offending ids. ``proposed`` blocks as
firmly as ``unclassified``: an agent's suggestion that no human accepted is
not a decision, and the spec permits no auto-confirmation at any confidence.
Exporting around an unreviewed line would silently understate a return.

**Per-entity, not per-property.** HMRC treats all of one owner's UK rental
property as a single UK property business, so quarterly figures pool across
properties (verified against the HMRC developer hub -- Task 16 Step 1).
Per-property figures are supplementary detail for the accountant, and are a
separate sheet for that reason.

:seealso: backend/src/core/quarters.py (the YTD window and attribution);
    backend/src/core/splits.py (the ownership apportionment).
"""

from __future__ import annotations

import csv
import datetime
import io
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from src.core.categories import EXCLUDED_FROM_EXPORT, INCOME_CATEGORIES, HmrcCategory
from src.core.quarters import TxnForTotals, cumulative_totals
from src.core.splits import split_amount

#: Statuses that must be resolved before a period can be exported. Both mean
#: "no human has decided about this line yet".
BLOCKING_STATUSES = frozenset({"unclassified", "proposed"})

#: Quarter numbers as stored in ``mtd_quarters.quarter``.
_QUARTER_LABELS = {1: "Q1", 2: "Q2", 3: "Q3", 4: "Q4"}
_QUARTER_NUMBERS = {label: number for number, label in _QUARTER_LABELS.items()}


class ExportBlockedError(Exception):
    """Raised when a period still contains unreviewed transactions.

    :ivar blockers: ids of the transactions that must be confirmed or
        excluded first -- the point being that the user is told *which*
        lines to go and fix, not merely that something is wrong.
    """

    def __init__(self, blockers: list[uuid.UUID]) -> None:
        self.blockers = blockers
        super().__init__(
            f"{len(blockers)} transaction(s) in this period are not yet reviewed: "
            f"{', '.join(str(b) for b in blockers)}"
        )


@dataclass(frozen=True)
class ExportEntity:
    """The entity an export is being built for.

    A plain record rather than the SQLAlchemy model, to keep this module
    database-free.

    :ivar id: the entity's id.
    :ivar name: for the pack's headings.
    :ivar tax_regime: ``mtd_itsa`` or ``corporation_tax``; decides which
        kind of pack is produced.
    """

    id: uuid.UUID
    name: str
    tax_regime: Literal["mtd_itsa", "corporation_tax"]


@dataclass(frozen=True)
class TxnRow:
    """One ``transactions`` row, as the database holds it.

    Note what this deliberately mirrors: ``amount`` is a **magnitude** and
    ``direction`` is separate, exactly as the column pair is. The signing
    happens in :func:`signed_amount`, in one place, rather than at each
    call site.

    :ivar id: the transaction's id, reported when it blocks an export.
    :ivar date: transaction date.
    :ivar amount: magnitude, always positive.
    :ivar direction: ``in`` or ``out``.
    :ivar hmrc_category: the confirmed category, or ``None`` if unreviewed
        or excluded.
    :ivar status: ``unclassified``, ``proposed``, ``confirmed`` or
        ``excluded``.
    :ivar entity_id: the bank-account owner; used only for lines with no
        property.
    :ivar property_id: the property the line is allocated to, or ``None``.
    """

    id: uuid.UUID
    date: datetime.date
    amount: Decimal
    direction: Literal["in", "out"]
    hmrc_category: HmrcCategory | None
    status: str
    entity_id: uuid.UUID
    property_id: uuid.UUID | None


@dataclass(frozen=True)
class QuarterlyPack:
    """An MTD ITSA quarterly pack for one individual entity.

    :ivar entity: who it is for.
    :ivar tax_year: calendar year the tax year starts in.
    :ivar quarter: 1-4.
    :ivar totals: cumulative year-to-date total per category.
    :ivar category_csv: the return figures, one row per category.
    :ivar property_csv: supplementary per-property detail.
    :ivar writes_mtd_quarter: always ``True``; an individual's quarter is
        recorded in ``mtd_quarters``.
    """

    entity: ExportEntity
    tax_year: int
    quarter: int
    totals: dict[HmrcCategory, Decimal]
    category_csv: str
    property_csv: str
    writes_mtd_quarter: bool = True


@dataclass(frozen=True)
class SimplePnlPack:
    """A simple profit-and-loss pack for a company entity.

    Companies are outside MTD ITSA, so no ``mtd_quarters`` row is written
    and no quarterly obligation is implied -- the spec's Ltd-side scope is
    "the same ledger plus a simple P&L export", with the accountant still in
    the loop.

    :ivar writes_mtd_quarter: always ``False``.
    """

    entity: ExportEntity
    tax_year: int
    quarter: int
    totals: dict[HmrcCategory, Decimal]
    category_csv: str
    property_csv: str
    writes_mtd_quarter: bool = False


def quarter_label(quarter: int) -> str:
    """Map ``1..4`` to the ``mtd_quarters.quarter`` label.

    :param quarter: quarter number.
    :raises ValueError: if outside 1-4. Clamping instead would file a
        return against the wrong period.
    :returns: ``"Q1"``..``"Q4"``.
    """
    if quarter not in _QUARTER_LABELS:
        raise ValueError(f"quarter must be 1-4, got {quarter!r}")
    return _QUARTER_LABELS[quarter]


def quarter_number(label: str) -> int:
    """Map a ``mtd_quarters.quarter`` label back to ``1..4``.

    :param label: ``"Q1"``..``"Q4"``.
    :raises ValueError: if not a known label.
    :returns: the quarter number.
    """
    if label not in _QUARTER_NUMBERS:
        raise ValueError(f"unknown quarter label {label!r}; expected Q1-Q4")
    return _QUARTER_NUMBERS[label]


def signed_amount(magnitude: Decimal, direction: str, category: HmrcCategory) -> Decimal:
    """Give a stored magnitude the sign an export needs.

    Positive iff ``(category is income) == (direction is "in")``:

    ==================================  =========  ========
    case                                direction  sign
    ==================================  =========  ========
    rent received (income)              in         positive
    tenant refund (income)              out        negative
    repair paid (expense)               out        positive
    contractor refund (expense)         in         negative
    ==================================  =========  ========

    The last row is the one a straight copy of ``amount`` gets wrong: money
    arrives, so a naive reading treats it as income, when in fact it is a
    repair that partly did not happen and the expense must fall.

    :param magnitude: the stored, always-positive amount.
    :param direction: ``in`` or ``out``.
    :param category: the confirmed HMRC category.
    :returns: the signed amount for totalling.
    """
    is_income = category in INCOME_CATEGORIES
    return magnitude if is_income == (direction == "in") else -magnitude


def _blockers(transactions: Sequence[TxnRow], window: tuple[datetime.date, datetime.date]
              ) -> list[uuid.UUID]:
    """Ids of unreviewed transactions inside the export window.

    Scoped to the window on purpose: a single unreviewed line in a later
    quarter must not block every earlier export forever.
    """
    start, end = window
    return [
        txn.id
        for txn in transactions
        if start <= txn.date <= end and txn.status in BLOCKING_STATUSES
    ]


def _to_csv(header: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    """Render rows as CSV text with a trailing newline."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue()


def _property_breakdown(
    transactions: Sequence[TxnForTotals],
    entity_id: uuid.UUID,
    window: tuple[datetime.date, datetime.date],
    ownerships: Mapping[uuid.UUID, Mapping[uuid.UUID, Decimal]],
) -> dict[tuple[uuid.UUID, HmrcCategory], Decimal]:
    """Per-property, per-category totals for this entity's share.

    Supplementary detail only -- HMRC pools these into one property
    business. Uses the same :func:`~src.core.splits.split_amount` as the
    headline totals, so the sheets cannot disagree about a share.
    """
    start, end = window
    out: dict[tuple[uuid.UUID, HmrcCategory], Decimal] = {}
    for txn in transactions:
        if txn.property_id is None or not (start <= txn.date <= end):
            continue
        shares = ownerships[txn.property_id]
        if entity_id not in shares:
            continue
        key = (txn.property_id, txn.hmrc_category)
        out[key] = out.get(key, Decimal("0.00")) + split_amount(txn.amount, shares)[entity_id]
    return out


def build_export_pack(
    entity: ExportEntity,
    *,
    tax_year: int,
    quarter: int,
    transactions: Sequence[TxnRow],
    ownerships: Mapping[uuid.UUID, Mapping[uuid.UUID, Decimal]],
) -> QuarterlyPack | SimplePnlPack:
    """Build one entity's export pack for a quarter, or refuse.

    :param entity: the entity to export for; its ``tax_regime`` decides
        whether an MTD quarterly pack or a simple P&L is produced.
    :param tax_year: calendar year the tax year starts in (6 April).
    :param quarter: 1-4.
    :param transactions: candidate rows -- typically every transaction in
        the org. Rows outside the window, or belonging to another entity's
        unallocated ledger, are ignored.
    :param ownerships: ``property_id -> {entity_id: percentage}`` for every
        property referenced.
    :raises ExportBlockedError: if any row inside the window is still
        ``unclassified`` or ``proposed``.
    :raises KeyError: if a property-allocated row's property is missing
        from ``ownerships`` -- treating it as wholly owned would misstate
        tax figures, so it is loud.
    :returns: a :class:`QuarterlyPack` or :class:`SimplePnlPack`.
    """
    # Imported here rather than at module scope: `_quarter_end_date` is
    # private to quarters.py, and reaching for it at import time would make
    # this module's dependency on that privacy invisible at the call site.
    from src.core.quarters import _quarter_end_date  # noqa: PLC0415

    window = (datetime.date(tax_year, 4, 6), _quarter_end_date(tax_year, quarter))

    blocked = _blockers(transactions, window)
    if blocked:
        raise ExportBlockedError(blocked)

    # Only confirmed rows carry a category, and only they reach the totals.
    # Excluded rows are a decision the user already made, so they are
    # dropped rather than treated as missing.
    for_totals = [
        TxnForTotals(
            date=txn.date,
            amount=signed_amount(txn.amount, txn.direction, txn.hmrc_category),
            hmrc_category=txn.hmrc_category,
            entity_id=txn.entity_id,
            property_id=txn.property_id,
        )
        for txn in transactions
        if txn.status == "confirmed" and txn.hmrc_category is not None
    ]

    totals = cumulative_totals(for_totals, entity.id, tax_year, quarter, ownerships)

    category_csv = _to_csv(
        ("hmrc_category", "cumulative_total"),
        [(category.value, f"{total:.2f}") for category, total in sorted(totals.items())],
    )
    breakdown = _property_breakdown(for_totals, entity.id, window, ownerships)
    property_csv = _to_csv(
        ("property_id", "hmrc_category", "cumulative_total"),
        [
            (str(property_id), category.value, f"{total:.2f}")
            for (property_id, category), total in sorted(
                breakdown.items(), key=lambda item: (str(item[0][0]), item[0][1].value)
            )
        ],
    )

    pack_type = SimplePnlPack if entity.tax_regime == "corporation_tax" else QuarterlyPack
    return pack_type(
        entity=entity,
        tax_year=tax_year,
        quarter=quarter,
        totals=totals,
        category_csv=category_csv,
        property_csv=property_csv,
    )
