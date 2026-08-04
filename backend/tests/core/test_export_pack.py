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
    ExportBlockedError,
    ExportEntity,
    SimplePnlPack,
    TxnRow,
    build_export_pack,
    quarter_label,
    quarter_number,
    signed_amount,
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
