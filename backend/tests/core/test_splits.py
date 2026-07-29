"""Tests for penny-exact ownership split maths.

:seealso: ``backend/src/core/splits.py``.
"""

from __future__ import annotations

import itertools
from decimal import Decimal
from uuid import UUID

import pytest

from src.core.splits import InvalidOwnershipError, split_amount

# Deterministic, fixed UUIDs so remainder tie-breaks are reproducible.
OWNER_A = UUID("00000000-0000-0000-0000-000000000001")
OWNER_B = UUID("00000000-0000-0000-0000-000000000002")
OWNER_C = UUID("00000000-0000-0000-0000-000000000003")


def test_fifty_fifty_split_is_penny_exact_with_largest_remainder() -> None:
    result = split_amount(
        Decimal("1000.01"),
        {OWNER_A: Decimal("50.00"), OWNER_B: Decimal("50.00")},
    )
    # Raw shares are 500.005 / 500.005 -- equal remainders, lower UUID wins.
    assert result[OWNER_A] == Decimal("500.01")
    assert result[OWNER_B] == Decimal("500.00")
    assert sum(result.values()) == Decimal("1000.01")


def test_single_owner_gets_everything() -> None:
    result = split_amount(Decimal("1234.56"), {OWNER_A: Decimal("100.00")})
    assert result == {OWNER_A: Decimal("1234.56")}


def test_shares_not_summing_to_100_raises() -> None:
    with pytest.raises(InvalidOwnershipError):
        split_amount(
            Decimal("100.00"), {OWNER_A: Decimal("60.00"), OWNER_B: Decimal("30.00")}
        )


def test_error_message_names_actual_sum_received() -> None:
    with pytest.raises(InvalidOwnershipError) as exc:
        split_amount(Decimal("100.00"), {OWNER_A: Decimal("99.99")})
    assert "99.99" in str(exc.value)


def test_negative_amount_fifty_fifty_mirrors_positive_case() -> None:
    result = split_amount(
        Decimal("-1000.01"),
        {OWNER_A: Decimal("50.00"), OWNER_B: Decimal("50.00")},
    )
    assert result[OWNER_A] == Decimal("-500.01")
    assert result[OWNER_B] == Decimal("-500.00")
    assert sum(result.values()) == Decimal("-1000.01")


def test_zero_amount_gives_all_zeros() -> None:
    result = split_amount(
        Decimal("0.00"),
        {OWNER_A: Decimal("50.00"), OWNER_B: Decimal("50.00")},
    )
    assert result[OWNER_A] == Decimal("0.00")
    assert result[OWNER_B] == Decimal("0.00")
    assert sum(result.values()) == Decimal("0.00")


def test_three_way_split_thirty_three_thirty_three_thirty_four() -> None:
    result = split_amount(
        Decimal("100.00"),
        {
            OWNER_A: Decimal("33.33"),
            OWNER_B: Decimal("33.33"),
            OWNER_C: Decimal("33.34"),
        },
    )
    assert sum(result.values()) == Decimal("100.00")
    # Owner C has the largest raw share so should not be shorted relative
    # to A/B, but the exact penny each gets is an implementation detail --
    # only the sum invariant and per-owner non-negativity are asserted here.
    assert all(v >= Decimal("0.00") for v in result.values())


def test_largest_remainder_owner_gets_extra_penny_not_just_any_owner() -> None:
    # Floors are 3.33/3.33/3.33 (sum 9.99), leftover 0.01. Remainders are
    # 0.003/0.003/0.004 -- C has the strictly largest remainder, so C (not
    # a tied A/B) must receive the leftover penny. This distinguishes
    # "largest remainder wins" from an accidentally reversed sort key,
    # which the sum-only invariant tests cannot catch.
    result = split_amount(
        Decimal("10.00"),
        {
            OWNER_A: Decimal("33.33"),
            OWNER_B: Decimal("33.33"),
            OWNER_C: Decimal("33.34"),
        },
    )
    assert result == {
        OWNER_A: Decimal("3.33"),
        OWNER_B: Decimal("3.33"),
        OWNER_C: Decimal("3.34"),
    }
    assert sum(result.values()) == Decimal("10.00")


def test_sub_penny_amount_rejected_loudly() -> None:
    with pytest.raises(ValueError, match="penny"):
        split_amount(
            Decimal("100.005"),
            {OWNER_A: Decimal("50.00"), OWNER_B: Decimal("50.00")},
        )


def test_empty_shares_raises_invalid_ownership_error() -> None:
    with pytest.raises(InvalidOwnershipError):
        split_amount(Decimal("100.00"), {})


def test_zero_or_negative_share_percentage_raises() -> None:
    with pytest.raises(InvalidOwnershipError):
        split_amount(
            Decimal("100.00"),
            {OWNER_A: Decimal("100.00"), OWNER_B: Decimal("0.00")},
        )


def test_equal_remainder_tie_break_lower_uuid_gets_extra_penny() -> None:
    # Two owners, equal shares, an amount whose raw split lands exactly on
    # a .5 penny boundary so the remainders tie exactly.
    result = split_amount(
        Decimal("0.01"),
        {OWNER_A: Decimal("50.00"), OWNER_B: Decimal("50.00")},
    )
    assert result[OWNER_A] == Decimal("0.01")
    assert result[OWNER_B] == Decimal("0.00")
    assert sum(result.values()) == Decimal("0.01")


def test_sum_invariant_holds_across_many_deterministic_combinations() -> None:
    amounts = [
        Decimal("0.01"),
        Decimal("0.02"),
        Decimal("0.03"),
        Decimal("1.00"),
        Decimal("99.99"),
        Decimal("1000.01"),
        Decimal("-1000.01"),
        Decimal("-0.01"),
        Decimal("123456.78"),
        Decimal("0.00"),
    ]
    share_sets: list[dict[UUID, Decimal]] = [
        {OWNER_A: Decimal("50.00"), OWNER_B: Decimal("50.00")},
        {
            OWNER_A: Decimal("33.33"),
            OWNER_B: Decimal("33.33"),
            OWNER_C: Decimal("33.34"),
        },
        {OWNER_A: Decimal("100.00")},
        {OWNER_A: Decimal("70.00"), OWNER_B: Decimal("30.00")},
        {OWNER_A: Decimal("1.00"), OWNER_B: Decimal("99.00")},
        {
            OWNER_A: Decimal("25.00"),
            OWNER_B: Decimal("25.00"),
            OWNER_C: Decimal("50.00"),
        },
    ]
    for amount, shares in itertools.product(amounts, share_sets):
        result = split_amount(amount, shares)
        assert sum(result.values()) == amount
        assert set(result.keys()) == set(shares.keys())
