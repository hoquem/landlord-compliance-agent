"""Tests for ``src.core.export_pack`` -- building a quarter's export.

The two things worth reading before changing anything here:

**The sign rule is derived, never copied.** A ``transactions`` row carries a
*magnitude* and a *direction*; it never carries a signed amount. The sign an
export needs depends on the **category** as well as the direction, because
money coming *in* against an expense category is a refund and must reduce
that expense. Copying ``amount`` straight through looks right for the common
cases and is wrong for exactly the ones that matter.

**A blocked export is the point, not an obstacle.** The spec refuses to
generate anything if a single line in the period is still unclassified or
merely proposed. Exporting around it would silently understate a return.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from src.core.categories import HmrcCategory
from src.core.export_pack import (
    CATEGORY_COLUMNS,
    DROPPED_FROM_EXPORT,
    CumulativeDecreaseError,
    ExportBlockedError,
    ExportEntity,
    MortgageSplitRequiredError,
    SimplePnlPack,
    TxnRow,
    assert_history_intact,
    assert_mortgage_interest_separated,
    build_export_pack,
    quarter_label,
    quarter_number,
    signed_amount,
    totals_from_columns,
    totals_to_columns,
)

ENTITY = uuid.uuid4()
OTHER_ENTITY = uuid.uuid4()
PROPERTY = uuid.uuid4()
PROPERTY_2 = uuid.uuid4()

INDIVIDUAL = ExportEntity(id=ENTITY, name="Mahmudul Hoque", tax_regime="mtd_itsa")
COMPANY = ExportEntity(id=ENTITY, name="Sample Properties Ltd", tax_regime="corporation_tax")

OWNERSHIPS = {
    PROPERTY: {ENTITY: Decimal("100.00")},
    PROPERTY_2: {ENTITY: Decimal("50.00"), OTHER_ENTITY: Decimal("50.00")},
}


def row(
    *,
    category: HmrcCategory | None = HmrcCategory.RENT_INCOME,
    direction: str = "in",
    amount: str = "1200.00",
    status: str = "confirmed",
    when: date = date(2026, 5, 1),
    property_id: uuid.UUID | None = PROPERTY,
) -> TxnRow:
    """Build one database-shaped transaction row."""
    return TxnRow(
        id=uuid.uuid4(),
        date=when,
        amount=Decimal(amount),
        direction=direction,
        hmrc_category=category,
        status=status,
        entity_id=ENTITY,
        property_id=property_id,
    )


# ---------------------------------------------------------------------------
# The sign rule.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("category", "direction", "expected", "why"),
    [
        (HmrcCategory.RENT_INCOME, "in", "1200.00", "rent received is income"),
        (HmrcCategory.RENT_INCOME, "out", "-1200.00", "a tenant refund reduces income"),
        (HmrcCategory.REPAIRS_MAINTENANCE, "out", "1200.00", "a repair paid is an expense"),
        (
            HmrcCategory.REPAIRS_MAINTENANCE,
            "in",
            "-1200.00",
            "a contractor refund reduces the expense",
        ),
    ],
    ids=["rent-in", "rent-refund", "repair-paid", "contractor-refund"],
)
def test_sign_is_derived_from_category_and_direction(
    category: HmrcCategory, direction: str, expected: str, why: str
) -> None:
    """Positive iff ``(category is income) == (direction is in)``.

    The contractor-refund case is the one that a straight copy of ``amount``
    gets wrong: money arrives, so a naive reading calls it income, when it is
    a repair that partly did not happen.
    """
    assert signed_amount(Decimal("1200.00"), direction, category) == Decimal(expected), why


def test_contractor_refund_reduces_the_repairs_total() -> None:
    """The rule, exercised end to end rather than in isolation."""
    pack = build_export_pack(
        INDIVIDUAL,
        tax_year=2026,
        quarter=1,
        transactions=[
            row(category=HmrcCategory.REPAIRS_MAINTENANCE, direction="out", amount="500.00"),
            row(category=HmrcCategory.REPAIRS_MAINTENANCE, direction="in", amount="200.00"),
        ],
        ownerships=OWNERSHIPS,
    )
    assert pack.totals[HmrcCategory.REPAIRS_MAINTENANCE] == Decimal("300.00")


# ---------------------------------------------------------------------------
# Blocking.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("blocking_status", ["unclassified", "proposed"])
def test_export_is_refused_while_any_line_is_unreviewed(blocking_status: str) -> None:
    """Both pre-confirmation states block, and the blockers are named.

    ``proposed`` blocks as firmly as ``unclassified``: an agent's suggestion
    that no human has accepted is not a decision, and the spec allows no
    auto-confirmation at any confidence.
    """
    blocker = row(status=blocking_status)
    with pytest.raises(ExportBlockedError) as exc:
        build_export_pack(
            INDIVIDUAL,
            tax_year=2026,
            quarter=1,
            transactions=[row(), blocker],
            ownerships=OWNERSHIPS,
        )
    assert exc.value.blockers == [blocker.id], "the export must say which lines to go and fix"


def test_excluded_lines_do_not_block() -> None:
    """A line the user deliberately excluded is a decision, not an omission."""
    pack = build_export_pack(
        INDIVIDUAL,
        tax_year=2026,
        quarter=1,
        transactions=[row(), row(status="excluded", category=None)],
        ownerships=OWNERSHIPS,
    )
    assert pack.totals[HmrcCategory.RENT_INCOME] == Decimal("1200.00")


def test_unreviewed_lines_outside_the_window_do_not_block() -> None:
    """Only the period being exported has to be clean.

    Otherwise a single unreviewed line from a later quarter would block every
    earlier export indefinitely.
    """
    pack = build_export_pack(
        INDIVIDUAL,
        tax_year=2026,
        quarter=1,
        transactions=[row(), row(status="unclassified", when=date(2026, 11, 1))],
        ownerships=OWNERSHIPS,
    )
    assert pack.totals[HmrcCategory.RENT_INCOME] == Decimal("1200.00")


# ---------------------------------------------------------------------------
# Shape of the pack.
# ---------------------------------------------------------------------------
def test_category_csv_has_one_row_per_category_with_ytd_totals() -> None:
    pack = build_export_pack(
        INDIVIDUAL,
        tax_year=2026,
        quarter=1,
        transactions=[
            row(category=HmrcCategory.RENT_INCOME, direction="in", amount="1200.00"),
            row(category=HmrcCategory.REPAIRS_MAINTENANCE, direction="out", amount="300.00"),
        ],
        ownerships=OWNERSHIPS,
    )
    lines = pack.category_csv.strip().splitlines()
    assert lines[0] == "hmrc_category,cumulative_total"
    assert "rent_income,1200.00" in lines
    assert "repairs_maintenance,300.00" in lines


def test_property_csv_is_supplementary_detail_not_the_return() -> None:
    """HMRC pools all of one owner's UK property into a single business.

    Per-property figures are for the accountant, so they are a separate
    sheet -- confirmed against the HMRC developer hub in Step 1.
    """
    pack = build_export_pack(
        INDIVIDUAL,
        tax_year=2026,
        quarter=1,
        transactions=[
            row(property_id=PROPERTY, amount="1200.00"),
            row(property_id=PROPERTY_2, amount="800.00"),
        ],
        ownerships=OWNERSHIPS,
    )
    lines = pack.property_csv.strip().splitlines()
    assert lines[0] == "property_id,hmrc_category,cumulative_total"
    assert any(str(PROPERTY) in line and "1200.00" in line for line in lines)
    # PROPERTY_2 is owned 50/50, so this entity's share of 800 is 400.
    assert any(str(PROPERTY_2) in line and "400.00" in line for line in lines)


def test_ownership_split_applies_to_the_entity_share() -> None:
    pack = build_export_pack(
        INDIVIDUAL,
        tax_year=2026,
        quarter=1,
        transactions=[row(property_id=PROPERTY_2, amount="800.00")],
        ownerships=OWNERSHIPS,
    )
    assert pack.totals[HmrcCategory.RENT_INCOME] == Decimal("400.00"), "50% share of 800"


def test_a_company_gets_a_simple_pnl_and_no_mtd_quarter() -> None:
    """Ltd entities are outside MTD ITSA; the spec gives them a P&L instead."""
    pack = build_export_pack(
        COMPANY,
        tax_year=2026,
        quarter=1,
        transactions=[row()],
        ownerships=OWNERSHIPS,
    )
    assert isinstance(pack, SimplePnlPack)
    assert pack.writes_mtd_quarter is False


def test_an_individual_gets_an_mtd_quarter_row() -> None:
    pack = build_export_pack(
        INDIVIDUAL, tax_year=2026, quarter=1, transactions=[row()], ownerships=OWNERSHIPS
    )
    assert pack.writes_mtd_quarter is True


# ---------------------------------------------------------------------------
# Quarter labels -- the mtd_quarters key.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("number", "label"), [(1, "Q1"), (2, "Q2"), (3, "Q3"), (4, "Q4")])
def test_quarter_label_round_trips(number: int, label: str) -> None:
    assert quarter_label(number) == label
    assert quarter_number(label) == number


@pytest.mark.parametrize("bad", [0, 5, -1])
def test_quarter_label_rejects_out_of_range(bad: int) -> None:
    """A silently clamped quarter would file a return against the wrong period."""
    with pytest.raises(ValueError, match="quarter"):
        quarter_label(bad)


def test_quarter_number_rejects_an_unknown_label() -> None:
    with pytest.raises(ValueError, match="quarter"):
        quarter_number("Q5")


# ---------------------------------------------------------------------------
# The cumulative-decrease guard.
#
# The spec originally said cumulative totals must never decrease. Task 10's
# review found that self-contradictory: a lawful in-window refund -- a
# contractor repaying part of a Q1 repair in Q2 -- makes a cumulative total
# fall, and blocking it would refuse a correct return.
#
# The discriminator is therefore not "did it fall?" but "is the history still
# what we exported?". Recompute each earlier quarter from today's confirmed
# data and compare against what was stored at the time. Intact history means
# any decrease is a real refund; changed history means transactions were
# edited or deleted after being filed, which is a human problem and not
# something an override flag should paper over.
# ---------------------------------------------------------------------------
def test_column_map_covers_every_reportable_category() -> None:
    """``mtd_quarters`` has a column per category except the dropped one.

    A category added to the enum without a column here would silently vanish
    from every export -- the worst kind of failure, because the numbers still
    look plausible.
    """
    assert set(CATEGORY_COLUMNS) == set(HmrcCategory) - DROPPED_FROM_EXPORT
    assert all(col.endswith("_total") for col in CATEGORY_COLUMNS.values())


def test_a_refund_decrease_with_intact_history_is_allowed() -> None:
    """Q2 total below Q1 is lawful when Q1 still recomputes to what was filed."""
    q1_txns = [row(category=HmrcCategory.REPAIRS_MAINTENANCE, direction="out", amount="500.00")]
    stored = {1: {HmrcCategory.REPAIRS_MAINTENANCE: Decimal("500.00")}}

    # The Q2 refund reduces the cumulative repairs figure to 300.
    all_txns = [
        *q1_txns,
        row(
            category=HmrcCategory.REPAIRS_MAINTENANCE,
            direction="in",
            amount="200.00",
            when=date(2026, 8, 1),
        ),
    ]
    assert_history_intact(
        entity_id=ENTITY,
        tax_year=2026,
        quarter=2,
        transactions=all_txns,
        ownerships=OWNERSHIPS,
        stored_by_quarter=stored,
    )


def test_history_changed_after_export_raises_and_names_the_difference() -> None:
    """A Q1 transaction deleted after filing must not pass silently.

    This is the case the guard exists for: the cumulative figure still looks
    internally consistent, and only comparing against what was *filed*
    reveals that the basis moved.
    """
    stored = {1: {HmrcCategory.REPAIRS_MAINTENANCE: Decimal("500.00")}}
    # The Q1 repair is gone from today's data -- deleted after it was filed.
    with pytest.raises(CumulativeDecreaseError) as exc:
        assert_history_intact(
            entity_id=ENTITY,
            tax_year=2026,
            quarter=2,
            transactions=[],
            ownerships=OWNERSHIPS,
            stored_by_quarter=stored,
        )
    message = str(exc.value)
    assert "Q1" in message, "name the quarter whose basis moved"
    assert "repairs_maintenance" in message, "name the category"
    assert "500.00" in message, "name what was filed"
    assert "0.00" in message, "and what it recomputes to now"
    assert exc.value.quarter == 1
    assert exc.value.category is HmrcCategory.REPAIRS_MAINTENANCE


def test_re_exporting_a_quarter_with_higher_totals_is_allowed() -> None:
    """Adding a missed transaction and re-filing is normal, not an anomaly.

    The guard only compares *earlier* quarters, so re-exporting the same
    quarter with a larger figure is untouched by it -- that becomes a new
    version row.
    """
    stored = {1: {HmrcCategory.RENT_INCOME: Decimal("1200.00")}}
    assert_history_intact(
        entity_id=ENTITY,
        tax_year=2026,
        quarter=1,
        transactions=[row(amount="1800.00")],
        ownerships=OWNERSHIPS,
        stored_by_quarter=stored,
    )


def test_a_quarter_never_exported_is_not_compared() -> None:
    """Nothing was filed, so there is no basis to have moved."""
    assert_history_intact(
        entity_id=ENTITY,
        tax_year=2026,
        quarter=3,
        transactions=[row()],
        ownerships=OWNERSHIPS,
        stored_by_quarter={},
    )


def test_a_transaction_added_to_a_filed_quarter_also_raises() -> None:
    """Divergence is divergence, in either direction.

    This test exists because mutation testing found the suite could not tell
    ``was != now`` from the naive ``now < was``: every other case here moves
    a filed total *down*, so both rules behaved identically and the central
    decision of Step 2b was unpinned.

    Adding a missed transaction to an already-filed quarter is just as much a
    divergence from what HMRC holds as deleting one. It is not a smaller
    problem for being in the taxpayer's disfavour, and the resolution is the
    same human reconciliation.
    """
    stored = {1: {HmrcCategory.RENT_INCOME: Decimal("1200.00")}}
    with pytest.raises(CumulativeDecreaseError) as exc:
        assert_history_intact(
            entity_id=ENTITY,
            tax_year=2026,
            quarter=2,
            # Q1 now holds more rent than was filed for it.
            transactions=[row(amount="1200.00"), row(amount="300.00", when=date(2026, 5, 2))],
            ownerships=OWNERSHIPS,
            stored_by_quarter=stored,
        )
    assert exc.value.stored == Decimal("1200.00")
    assert exc.value.recomputed == Decimal("1500.00")


# ---------------------------------------------------------------------------
# The `mtd_quarters` column mapping, in both directions.
#
# This is the seam between the pure core and the database row, and it is the
# only place a category can silently fall out of a filed return. Both
# directions live next to `CATEGORY_COLUMNS` so a category added to the enum
# breaks them together rather than one at a time.
# ---------------------------------------------------------------------------
def test_totals_to_columns_states_every_reportable_category() -> None:
    """A category absent from the totals is stored as 0.00, not omitted.

    An `mtd_quarters` row is a full statement of what was filed. Leaving a
    column to its DB default would make "we filed nothing under repairs"
    indistinguishable from "we forgot repairs", and only on insert.
    """
    columns = totals_to_columns({HmrcCategory.RENT_INCOME: Decimal("1200.00")})

    assert set(columns) == set(CATEGORY_COLUMNS.values())
    assert columns["rent_income_total"] == Decimal("1200.00")
    assert columns["repairs_maintenance_total"] == Decimal("0.00")


def test_totals_to_columns_refuses_a_category_with_no_column() -> None:
    """`personal_non_business` reaching an export means something upstream broke.

    `cumulative_totals` drops it by definition, so its presence here is not a
    number to store -- it is evidence the totals did not come from there.
    """
    with pytest.raises(ValueError, match="personal_non_business"):
        totals_to_columns({HmrcCategory.PERSONAL_NON_BUSINESS: Decimal("50.00")})


def test_totals_from_columns_reads_a_stored_row_back() -> None:
    """A stored row maps back to the category totals the guard compares."""
    stored = totals_from_columns(
        {col: Decimal("0.00") for col in CATEGORY_COLUMNS.values()}
        | {"rent_income_total": Decimal("1200.00")}
    )

    assert set(stored) == set(CATEGORY_COLUMNS)
    assert stored[HmrcCategory.RENT_INCOME] == Decimal("1200.00")


def test_column_mapping_round_trips() -> None:
    """What was written is what is read back -- the guard depends on it.

    If these two drifted, `assert_history_intact` would compare today's
    figures against a mis-read row and raise on an export that is fine.
    """
    totals = {
        HmrcCategory.RENT_INCOME: Decimal("1200.00"),
        HmrcCategory.REPAIRS_MAINTENANCE: Decimal("-84.99"),
    }

    read_back = totals_from_columns(totals_to_columns(totals))

    assert read_back[HmrcCategory.RENT_INCOME] == Decimal("1200.00")
    assert read_back[HmrcCategory.REPAIRS_MAINTENANCE] == Decimal("-84.99")
    assert all(read_back[c] == Decimal("0.00") for c in CATEGORY_COLUMNS if c not in totals)


# ---------------------------------------------------------------------------
# Mortgage interest: separating the allowable part from the capital.
#
# A repayment mortgage arrives as one direct debit and only the interest is
# deductible. Until 2026-08-06 the whole payment was counted, which overstates
# costs and understates profit. Found on real data; the synthetic golden set
# had no case like it, because it was invented by someone who had not met one.
# ---------------------------------------------------------------------------
JOINT = {PROPERTY_2: {ENTITY: Decimal("60.00"), OTHER_ENTITY: Decimal("40.00")}}


def mortgage_row(
    *,
    amount: str = "1250.00",
    allowable: str | None = "412.55",
    direction: str = "out",
    property_id: uuid.UUID | None = PROPERTY,
    when: date = date(2026, 5, 28),
) -> TxnRow:
    """One repayment-mortgage direct debit, with its interest portion."""
    return TxnRow(
        id=uuid.uuid4(),
        date=when,
        amount=Decimal(amount),
        allowable_amount=None if allowable is None else Decimal(allowable),
        direction=direction,
        hmrc_category=HmrcCategory.FINANCE_COSTS_RESIDENTIAL,
        status="confirmed",
        entity_id=ENTITY,
        property_id=property_id,
    )


def test_only_the_allowable_part_of_a_payment_reaches_the_totals() -> None:
    """The whole point: £1,250.00 leaves the account, £412.55 is deductible."""
    pack = build_export_pack(
        INDIVIDUAL, tax_year=2026, quarter=1,
        transactions=[mortgage_row()], ownerships=OWNERSHIPS,
    )

    assert pack.totals[HmrcCategory.FINANCE_COSTS_RESIDENTIAL] == Decimal("412.55")


def test_a_payment_with_no_allowable_amount_still_counts_in_full() -> None:
    """``None`` means "all of it" -- the ordinary case, and every existing row."""
    pack = build_export_pack(
        INDIVIDUAL, tax_year=2026, quarter=1,
        transactions=[mortgage_row(allowable=None)], ownerships=OWNERSHIPS,
    )

    assert pack.totals[HmrcCategory.FINANCE_COSTS_RESIDENTIAL] == Decimal("1250.00")


def test_the_interest_is_apportioned_by_ownership_not_the_payment() -> None:
    """**A jointly-owned repayment mortgage splits the interest, not the debit.**

    The two figures are far enough apart to tell which one was split: 60% of
    the interest is 247.53, while 60% of the payment would be 794.61. An
    implementation that substituted after apportioning, rather than before,
    would produce the second.
    """
    pack = build_export_pack(
        INDIVIDUAL, tax_year=2026, quarter=1,
        transactions=[mortgage_row(property_id=PROPERTY_2)], ownerships=JOINT,
    )

    assert pack.totals[HmrcCategory.FINANCE_COSTS_RESIDENTIAL] == Decimal("247.53")


def test_the_allowable_part_is_signed_by_the_same_rule_as_the_amount() -> None:
    """A refund of overpaid interest must reduce the expense, not add income.

    ``allowable_amount`` is a magnitude like ``amount``, so it goes through
    :func:`signed_amount` identically. Storing it pre-signed would make this
    case silently wrong in the one direction nobody checks by hand.
    """
    pack = build_export_pack(
        INDIVIDUAL, tax_year=2026, quarter=1,
        transactions=[
            mortgage_row(),
            mortgage_row(amount="100.00", allowable="40.00", direction="in",
                         when=date(2026, 6, 1)),
        ],
        ownerships=OWNERSHIPS,
    )

    assert pack.totals[HmrcCategory.FINANCE_COSTS_RESIDENTIAL] == Decimal("372.55")


def test_an_unsplit_repayment_mortgage_payment_blocks_the_export() -> None:
    """Silence is the failure mode this replaces.

    The category is right and the confidence is high; only the amount is
    wrong. Nothing else in the pipeline notices, so the export has to.
    """
    txn = mortgage_row(allowable=None)

    with pytest.raises(MortgageSplitRequiredError) as caught:
        assert_mortgage_interest_separated(
            transactions=[txn],
            window=(date(2026, 4, 6), date(2026, 7, 5)),
            repayment_properties={PROPERTY},
        )

    assert caught.value.blockers == [txn.id]


def test_a_split_payment_passes() -> None:
    """Supplying the interest figure is what clears it."""
    assert_mortgage_interest_separated(
        transactions=[mortgage_row(allowable="412.55")],
        window=(date(2026, 4, 6), date(2026, 7, 5)),
        repayment_properties={PROPERTY},
    )


def test_an_interest_only_mortgage_needs_no_split() -> None:
    """The whole payment is interest, so there is nothing to separate."""
    assert_mortgage_interest_separated(
        transactions=[mortgage_row(allowable=None)],
        window=(date(2026, 4, 6), date(2026, 7, 5)),
        repayment_properties=set(),
    )


def test_marking_the_mortgage_repayment_after_confirming_still_blocks() -> None:
    """**Why the guard is at export and not at confirmation.**

    A line confirmed while the property looked interest-only, and the property
    corrected afterwards, is exactly the ordering a confirm-time check misses.
    The property's state is read when the figures are produced, not when the
    line was agreed.
    """
    already_confirmed = mortgage_row(allowable=None)

    with pytest.raises(MortgageSplitRequiredError):
        assert_mortgage_interest_separated(
            transactions=[already_confirmed],
            window=(date(2026, 4, 6), date(2026, 7, 5)),
            repayment_properties={PROPERTY},  # set only now
        )


def test_a_payment_outside_the_window_does_not_block() -> None:
    """A later quarter's unsplit payment must not hold up this one."""
    assert_mortgage_interest_separated(
        transactions=[mortgage_row(allowable=None, when=date(2026, 9, 1))],
        window=(date(2026, 4, 6), date(2026, 7, 5)),
        repayment_properties={PROPERTY},
    )


def test_a_finance_cost_with_no_property_is_not_checked() -> None:
    """**A known hole, recorded rather than hidden.**

    With no ``property_id`` there is no mortgage type to consult, so this
    passes. Borrowing is inherently per-property so it should be rare, but if
    it ever stops being rare this test is where the decision was made.
    """
    assert_mortgage_interest_separated(
        transactions=[mortgage_row(allowable=None, property_id=None)],
        window=(date(2026, 4, 6), date(2026, 7, 5)),
        repayment_properties={PROPERTY},
    )
