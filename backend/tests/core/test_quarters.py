"""Tests for UK tax-year quarters, cumulative totals, and MTD deadlines.

:seealso: ``backend/src/core/quarters.py``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest

from src.core.categories import EXCLUDED_FROM_EXPORT, HmrcCategory
from src.core.quarters import (
    TxnForTotals,
    cumulative_totals,
    format_tax_year,
    next_update_deadline,
    quarter_for,
)

# Deterministic, fixed UUIDs.
ENTITY_A = UUID("00000000-0000-0000-0000-0000000000a1")
ENTITY_B = UUID("00000000-0000-0000-0000-0000000000a2")
PROPERTY_1 = UUID("00000000-0000-0000-0000-0000000000b1")
PROPERTY_2 = UUID("00000000-0000-0000-0000-0000000000b2")


# ---------------------------------------------------------------------------
# quarter_for
# ---------------------------------------------------------------------------


def test_quarter_for_q1_start() -> None:
    assert quarter_for(date(2026, 4, 6)) == (2026, 1)


def test_quarter_for_day_before_tax_year_start_is_prior_q4() -> None:
    assert quarter_for(date(2026, 4, 5)) == (2025, 4)


def test_quarter_for_q1_last_day() -> None:
    assert quarter_for(date(2026, 7, 5)) == (2026, 1)


def test_quarter_for_q2_first_day() -> None:
    assert quarter_for(date(2026, 7, 6)) == (2026, 2)


def test_quarter_for_q2_last_day() -> None:
    assert quarter_for(date(2026, 10, 5)) == (2026, 2)


def test_quarter_for_q3_first_day() -> None:
    assert quarter_for(date(2026, 10, 6)) == (2026, 3)


def test_quarter_for_q3_last_day_crosses_calendar_year() -> None:
    assert quarter_for(date(2027, 1, 5)) == (2026, 3)


def test_quarter_for_q4_first_day_crosses_calendar_year() -> None:
    assert quarter_for(date(2027, 1, 6)) == (2026, 4)


def test_quarter_for_q4_last_day() -> None:
    assert quarter_for(date(2027, 4, 5)) == (2026, 4)


def test_quarter_for_q4_date_belongs_to_previous_tax_year_start() -> None:
    assert quarter_for(date(2027, 2, 1)) == (2026, 4)


# ---------------------------------------------------------------------------
# format_tax_year
# ---------------------------------------------------------------------------


def test_format_tax_year_ordinary() -> None:
    assert format_tax_year(2026) == "2026-27"


def test_format_tax_year_century_boundary() -> None:
    assert format_tax_year(2099) == "2099-00"


def test_format_tax_year_rejects_before_mtd_existed() -> None:
    with pytest.raises(ValueError, match="2019"):
        format_tax_year(2019)


def test_format_tax_year_rejects_far_future() -> None:
    with pytest.raises(ValueError, match="2101"):
        format_tax_year(2101)


def test_format_tax_year_accepts_lower_boundary() -> None:
    assert format_tax_year(2020) == "2020-21"


def test_format_tax_year_accepts_upper_boundary() -> None:
    assert format_tax_year(2100) == "2100-01"


# ---------------------------------------------------------------------------
# next_update_deadline
# ---------------------------------------------------------------------------


def test_next_update_deadline_before_q1_deadline() -> None:
    assert next_update_deadline(date(2026, 7, 20)) == date(2026, 8, 7)


def test_next_update_deadline_before_q2_deadline() -> None:
    assert next_update_deadline(date(2026, 10, 20)) == date(2026, 11, 7)


def test_next_update_deadline_before_q3_deadline() -> None:
    assert next_update_deadline(date(2027, 1, 20)) == date(2027, 2, 7)


def test_next_update_deadline_before_q4_deadline() -> None:
    assert next_update_deadline(date(2026, 4, 20)) == date(2026, 5, 7)


def test_next_update_deadline_on_deadline_day_returns_same_day() -> None:
    # Deadlines are inclusive-today: you can still file on the deadline
    # itself, so it counts as "next".
    assert next_update_deadline(date(2026, 8, 7)) == date(2026, 8, 7)


def test_next_update_deadline_day_after_deadline_rolls_to_next() -> None:
    assert next_update_deadline(date(2026, 8, 8)) == date(2026, 11, 7)


def test_next_update_deadline_wraps_year_after_november() -> None:
    assert next_update_deadline(date(2026, 11, 8)) == date(2027, 2, 7)


# ---------------------------------------------------------------------------
# cumulative_totals
# ---------------------------------------------------------------------------


def _txn(
    d: date,
    amount: str,
    category: HmrcCategory,
    entity_id: UUID = ENTITY_A,
    property_id: UUID | None = PROPERTY_1,
) -> TxnForTotals:
    return TxnForTotals(
        date=d,
        amount=Decimal(amount),
        hmrc_category=category,
        entity_id=entity_id,
        property_id=property_id,
    )


SOLE_OWNERSHIP = {PROPERTY_1: {ENTITY_A: Decimal("100.00")}}
JOINT_OWNERSHIP = {PROPERTY_1: {ENTITY_A: Decimal("50.00"), ENTITY_B: Decimal("50.00")}}


def test_cumulative_totals_sums_within_quarter() -> None:
    txns = [
        _txn(date(2026, 4, 10), "500.00", HmrcCategory.RENT_INCOME),
        _txn(date(2026, 5, 10), "500.00", HmrcCategory.RENT_INCOME),
    ]
    result = cumulative_totals(txns, ENTITY_A, 2026, 1, ownerships=SOLE_OWNERSHIP)
    assert result[HmrcCategory.RENT_INCOME] == Decimal("1000.00")


def test_cumulative_totals_excludes_transactions_outside_window() -> None:
    txns = [
        _txn(date(2026, 4, 10), "500.00", HmrcCategory.RENT_INCOME),
        # Falls in Q2, must not count towards Q1 YTD total.
        _txn(date(2026, 8, 1), "500.00", HmrcCategory.RENT_INCOME),
        # Falls before the tax year starts.
        _txn(date(2026, 4, 1), "999.00", HmrcCategory.RENT_INCOME),
    ]
    result = cumulative_totals(txns, ENTITY_A, 2026, 1, ownerships=SOLE_OWNERSHIP)
    assert result[HmrcCategory.RENT_INCOME] == Decimal("500.00")


def test_cumulative_totals_is_ytd_through_quarter_end_not_just_quarter() -> None:
    # A Q2 request must include the Q1 transaction too (cumulative YTD).
    txns = [
        _txn(date(2026, 5, 1), "500.00", HmrcCategory.RENT_INCOME),
        _txn(date(2026, 8, 1), "500.00", HmrcCategory.RENT_INCOME),
    ]
    result = cumulative_totals(txns, ENTITY_A, 2026, 2, ownerships=SOLE_OWNERSHIP)
    assert result[HmrcCategory.RENT_INCOME] == Decimal("1000.00")


def test_cumulative_totals_q2_is_superset_of_q1_for_same_data() -> None:
    txns = [
        _txn(date(2026, 4, 10), "500.00", HmrcCategory.RENT_INCOME),
        _txn(date(2026, 5, 10), "50.00", HmrcCategory.REPAIRS_MAINTENANCE),
        _txn(date(2026, 8, 1), "300.00", HmrcCategory.RENT_INCOME),
    ]
    q1 = cumulative_totals(txns, ENTITY_A, 2026, 1, ownerships=SOLE_OWNERSHIP)
    q2 = cumulative_totals(txns, ENTITY_A, 2026, 2, ownerships=SOLE_OWNERSHIP)
    # Every category total present in Q1 must be present, and no smaller,
    # in Q2 -- Q2 is a cumulative YTD superset of Q1 for the same data. This
    # monotonicity holds here because all fixture amounts are non-negative
    # (the house convention -- see TxnForTotals.amount); it is a property of
    # this test's data, not a guarantee cumulative_totals itself makes -- a
    # refund/reversal landing in Q2 could legitimately push a category's Q2
    # total below its Q1 total.
    for category, q1_total in q1.items():
        assert category in q2
        assert q2[category] >= q1_total
    assert q2[HmrcCategory.RENT_INCOME] == Decimal("800.00")


def test_cumulative_totals_q3_includes_january_dates_year_crossing() -> None:
    txns = [
        _txn(date(2026, 4, 10), "100.00", HmrcCategory.RENT_INCOME),
        _txn(date(2027, 1, 3), "50.00", HmrcCategory.RENT_INCOME),
        # Just past the Q3 window (Q4 territory) -- must be excluded.
        _txn(date(2027, 1, 6), "999.00", HmrcCategory.RENT_INCOME),
    ]
    result = cumulative_totals(txns, ENTITY_A, 2026, 3, ownerships=SOLE_OWNERSHIP)
    assert result[HmrcCategory.RENT_INCOME] == Decimal("150.00")


def test_cumulative_totals_joint_ownership_splits_penny_exactly() -> None:
    txns = [
        _txn(
            date(2026, 4, 10),
            "1000.01",
            HmrcCategory.RENT_INCOME,
            entity_id=ENTITY_A,
            property_id=PROPERTY_1,
        )
    ]
    result_a = cumulative_totals(txns, ENTITY_A, 2026, 1, ownerships=JOINT_OWNERSHIP)
    result_b = cumulative_totals(txns, ENTITY_B, 2026, 1, ownerships=JOINT_OWNERSHIP)
    assert result_a[HmrcCategory.RENT_INCOME] + result_b[
        HmrcCategory.RENT_INCOME
    ] == Decimal("1000.01")


def test_cumulative_totals_null_property_attributes_to_transaction_entity() -> None:
    # travel_vehicle / use_of_home_allowance style lines: no property, no
    # ownership split -- 100% to transactions.entity_id.
    txns = [
        _txn(
            date(2026, 4, 10),
            "45.00",
            HmrcCategory.USE_OF_HOME_ALLOWANCE,
            entity_id=ENTITY_A,
            property_id=None,
        ),
        _txn(
            date(2026, 4, 15),
            "20.00",
            HmrcCategory.TRAVEL_VEHICLE,
            entity_id=ENTITY_B,
            property_id=None,
        ),
    ]
    result_a = cumulative_totals(txns, ENTITY_A, 2026, 1, ownerships={})
    result_b = cumulative_totals(txns, ENTITY_B, 2026, 1, ownerships={})
    assert result_a[HmrcCategory.USE_OF_HOME_ALLOWANCE] == Decimal("45.00")
    assert HmrcCategory.TRAVEL_VEHICLE not in result_a
    assert result_b[HmrcCategory.TRAVEL_VEHICLE] == Decimal("20.00")
    assert HmrcCategory.USE_OF_HOME_ALLOWANCE not in result_b


def test_cumulative_totals_personal_non_business_never_appears() -> None:
    txns = [
        _txn(date(2026, 4, 10), "100.00", HmrcCategory.PERSONAL_NON_BUSINESS),
        _txn(date(2026, 4, 10), "50.00", HmrcCategory.RENT_INCOME),
    ]
    result = cumulative_totals(txns, ENTITY_A, 2026, 1, ownerships=SOLE_OWNERSHIP)
    assert HmrcCategory.PERSONAL_NON_BUSINESS not in result
    assert result[HmrcCategory.RENT_INCOME] == Decimal("50.00")


def test_cumulative_totals_capital_expense_appears_as_own_category() -> None:
    # capital_expense is excluded from *allowable* totals but is not
    # dropped like personal_non_business -- it's reported separately (the
    # mtd_quarters table has its own capital_expense_total column).
    txns = [
        _txn(date(2026, 4, 10), "2000.00", HmrcCategory.CAPITAL_EXPENSE),
    ]
    result = cumulative_totals(txns, ENTITY_A, 2026, 1, ownerships=SOLE_OWNERSHIP)
    assert result[HmrcCategory.CAPITAL_EXPENSE] == Decimal("2000.00")


def test_cumulative_totals_only_capital_expense_survives_from_excluded_set() -> None:
    # Drift guard: categories.EXCLUDED_FROM_EXPORT is the source of truth
    # for "never on an HMRC export". If a future category is added to that
    # set, cumulative_totals must drop it by default (like
    # personal_non_business) unless someone deliberately carves it out
    # (like capital_expense) -- this pins that default so the carve-out
    # can't silently widen.
    for category in EXCLUDED_FROM_EXPORT:
        txns = [_txn(date(2026, 4, 10), "10.00", category)]
        result = cumulative_totals(txns, ENTITY_A, 2026, 1, ownerships=SOLE_OWNERSHIP)
        if category == HmrcCategory.CAPITAL_EXPENSE:
            assert category in result
        else:
            assert category not in result


def test_cumulative_totals_out_of_range_tax_year_raises() -> None:
    txns = [
        _txn(date(1999, 4, 10), "100.00", HmrcCategory.RENT_INCOME),
    ]
    with pytest.raises(ValueError, match="1999"):
        cumulative_totals(txns, ENTITY_A, 1999, 1, ownerships=SOLE_OWNERSHIP)


def test_cumulative_totals_missing_ownership_map_raises() -> None:
    txns = [
        _txn(date(2026, 4, 10), "100.00", HmrcCategory.RENT_INCOME),
    ]
    with pytest.raises(KeyError):
        cumulative_totals(txns, ENTITY_A, 2026, 1, ownerships={})


def test_cumulative_totals_entity_not_an_owner_gets_nothing_from_that_property() -> (
    None
):
    txns = [
        _txn(
            date(2026, 4, 10),
            "100.00",
            HmrcCategory.RENT_INCOME,
            entity_id=ENTITY_A,
            property_id=PROPERTY_2,
        ),
    ]
    ownerships = {PROPERTY_2: {ENTITY_B: Decimal("100.00")}}
    result = cumulative_totals(txns, ENTITY_A, 2026, 1, ownerships=ownerships)
    assert HmrcCategory.RENT_INCOME not in result
