"""Tests for VAT split logic."""

from decimal import Decimal

import pytest

from src.core.vat import maybe_split_vat, split_vat


class TestSplitVat:
    """The core arithmetic of gross → net + VAT."""

    def test_standard_20pct_round_amount(self):
        """£2,000 gross at 20% → £1,666.67 net + £333.33 VAT."""
        net, vat = split_vat(Decimal("2000.00"), Decimal("20.00"))
        assert net == Decimal("1666.67")
        assert vat == Decimal("333.33")
        assert net + vat == Decimal("2000.00")

    def test_standard_20pct_odd_amount(self):
        """£1,200 gross at 20% → £1,000.00 net + £200.00 VAT."""
        net, vat = split_vat(Decimal("1200.00"), Decimal("20.00"))
        assert net == Decimal("1000.00")
        assert vat == Decimal("200.00")

    def test_standard_20pct_pennies(self):
        """£99.99 gross at 20% → £83.33 net + £16.66 VAT (rounded)."""
        net, vat = split_vat(Decimal("99.99"), Decimal("20.00"))
        assert net == Decimal("83.33")
        assert vat == Decimal("16.66")
        assert net + vat == Decimal("99.99")

    def test_reduced_5pct(self):
        """£1,050 gross at 5% → £1,000.00 net + £50.00 VAT."""
        net, vat = split_vat(Decimal("1050.00"), Decimal("5.00"))
        assert net == Decimal("1000.00")
        assert vat == Decimal("50.00")

    def test_zero_rate(self):
        """£1,000 gross at 0% → £1,000 net + £0 VAT (zero-rated supply)."""
        net, vat = split_vat(Decimal("1000.00"), Decimal("0.00"))
        assert net == Decimal("1000.00")
        assert vat == Decimal("0.00")

    def test_net_plus_vat_always_equals_gross(self):
        """Invariant: net + vat == gross for any valid input."""
        for gross_pence in range(100, 100000, 137):
            gross = Decimal(gross_pence) / Decimal(100)
            net, vat = split_vat(gross, Decimal("20.00"))
            assert net + vat == gross, f"failed for gross={gross}"

    def test_negative_gross_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            split_vat(Decimal("-100.00"), Decimal("20.00"))

    def test_negative_rate_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            split_vat(Decimal("100.00"), Decimal("-5.00"))

    def test_subpenny_input_raises(self):
        """Gross with more than 2 decimal places should be rejected."""
        with pytest.raises(ValueError, match="penny precision"):
            split_vat(Decimal("100.005"), Decimal("20.00"))

    def test_three_decimal_places_raises(self):
        with pytest.raises(ValueError, match="penny precision"):
            split_vat(Decimal("99.999"), Decimal("20.00"))


class TestMaybeSplitVat:
    """The conditional logic: when does a split actually happen?"""

    def test_splits_income_for_vat_registered_vat_inclusive_import(self):
        """The happy path: VAT-registered entity, VAT-inclusive import, income."""
        result = maybe_split_vat(
            Decimal("2000.00"),
            "in",
            vat_registered=True,
            vat_rate=Decimal("20.00"),
            vat_inclusive=True,
        )
        assert result is not None
        assert result.amount == Decimal("1666.67")
        assert result.vat_amount == Decimal("333.33")
        assert result.gross_amount == Decimal("2000.00")

    def test_no_split_when_entity_not_vat_registered(self):
        """Non-VAT-registered entity → no split, even if import says inclusive."""
        result = maybe_split_vat(
            Decimal("2000.00"),
            "in",
            vat_registered=False,
            vat_rate=Decimal("20.00"),
            vat_inclusive=True,
        )
        assert result is None

    def test_no_split_when_import_not_vat_inclusive(self):
        """VAT-registered entity but import not flagged → no split."""
        result = maybe_split_vat(
            Decimal("2000.00"),
            "in",
            vat_registered=True,
            vat_rate=Decimal("20.00"),
            vat_inclusive=False,
        )
        assert result is None

    def test_no_split_for_expenses(self):
        """Expenses are never auto-split — input VAT is a manual review action."""
        result = maybe_split_vat(
            Decimal("500.00"),
            "out",
            vat_registered=True,
            vat_rate=Decimal("20.00"),
            vat_inclusive=True,
        )
        assert result is None

    def test_no_split_when_both_flags_false(self):
        result = maybe_split_vat(
            Decimal("2000.00"),
            "in",
            vat_registered=False,
            vat_rate=Decimal("20.00"),
            vat_inclusive=False,
        )
        assert result is None

    def test_split_result_fields_sum_correctly(self):
        """VatSplitResult: amount + vat_amount == gross_amount."""
        result = maybe_split_vat(
            Decimal("2400.00"),
            "in",
            vat_registered=True,
            vat_rate=Decimal("20.00"),
            vat_inclusive=True,
        )
        assert result is not None
        assert result.amount + result.vat_amount == result.gross_amount