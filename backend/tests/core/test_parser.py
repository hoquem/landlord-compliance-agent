"""Tests for the bank statement CSV parser.

:seealso: ``backend/src/core/parser.py``.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from src.core.parser import (
    ParsedLine,
    StatementDecodeError,
    StatementFormatMismatchError,
    StatementParseError,
    UnknownStatementFormatError,
    parse_statement,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "statements"


# ---------------------------------------------------------------------------
# Dispatch: the caller names the bank, the header verifies the claim.
#
# `imports.source_bank` is NOT NULL (0001_core.sql:293), so the bank is always
# known at import time and the parser never has to guess. That is what makes a
# headerless export (HSBC) parseable at all, and what turns "wrong file for
# this bank" into a named error instead of a silent mis-parse.
# ---------------------------------------------------------------------------
def test_parse_statement_dispatches_on_the_named_bank() -> None:
    lines = parse_statement(FIXTURES / "generic.csv", bank="generic")
    assert len(lines) == 4


def test_unknown_bank_name_fails_loudly() -> None:
    with pytest.raises(UnknownStatementFormatError) as exc:
        parse_statement(FIXTURES / "generic.csv", bank="not-a-bank")
    assert "not-a-bank" in str(exc.value)


def test_header_that_contradicts_the_named_bank_fails_loudly() -> None:
    """Uploading one bank's file under another bank's name must not mis-parse.

    This is the guard that replaces format sniffing: the caller asserts the
    bank, and the header is what checks the assertion.
    """
    with pytest.raises(StatementFormatMismatchError) as exc:
        parse_statement(FIXTURES / "starling.csv", bank="generic")
    assert "generic" in str(exc.value)


def test_parses_generic_csv() -> None:
    lines = parse_statement(FIXTURES / "generic.csv", bank="generic")
    assert len(lines) == 4
    assert lines[0] == ParsedLine(
        date=date(2026, 7, 1), description="B&Q LUTON", amount=Decimal("-84.99")
    )


def test_malformed_row_fails_loudly() -> None:
    with pytest.raises(StatementParseError) as exc:
        parse_statement(FIXTURES / "malformed.csv", bank="generic")
    assert exc.value.row_number == 3  # exact failing row surfaced
    assert "unparseable date" in str(exc.value)


def test_unrecognised_header_is_a_mismatch_not_an_unknown_format() -> None:
    """Renamed in Task 8a, because the two failures are now distinct.

    Before the registry was keyed by bank name, an unrecognised header meant
    "no format matches this file" -- there was nothing else it could mean.
    Now the caller names the bank, so an unrecognised header means something
    sharper: *this file is not from the bank you said it was*. The old name
    would describe the wrong failure.
    """
    with pytest.raises(StatementFormatMismatchError):
        parse_statement(FIXTURES / "unknown_headers.csv", bank="generic")


def test_undecodable_bytes_fail_loudly_rather_than_mangling(tmp_path: Path) -> None:
    """A decode failure raises; it is never papered over with replacement chars.

    Real UK bank exports are not all UTF-8 -- Nationwide emits iso-8859-1
    because of the pound sign -- so reading one with the wrong encoding is a
    live scenario, not a hypothetical. Recovering by re-reading with
    ``errors="replace"`` would silently mangle a counterparty name, which is
    exactly the quiet corruption this module refuses.

    The failing byte is in a *data* row rather than the header, because the
    header is verified first: a latin-1 header would raise
    :class:`StatementFormatMismatchError` before any decode was attempted,
    and this test would then pass for the wrong reason.
    """
    p = tmp_path / "latin1.csv"
    p.write_bytes(
        "Date,Description,Amount,Balance\n01/07/2026,CAF\xc9 \xa35 NOTE,-5.00,10.00\n".encode(
            "iso-8859-1"
        )
    )
    with pytest.raises(StatementDecodeError) as exc:
        parse_statement(p, bank="generic")
    assert "utf-8-sig" in str(exc.value)


def test_yyyy_mm_dd_date_accepted_for_generic_format() -> None:
    lines = parse_statement(FIXTURES / "generic.csv", bank="generic")
    assert lines[1].date == date(2026, 7, 3)


def test_amount_with_thousands_separator_parses() -> None:
    lines = parse_statement(FIXTURES / "generic.csv", bank="generic")
    assert lines[3].amount == Decimal("1234.56")


def test_unparseable_amount_raises_with_row_number(tmp_path: Path) -> None:
    p = tmp_path / "bad_amount.csv"
    p.write_text(
        "Date,Description,Amount,Balance\n"
        "01/07/2026,GOOD ROW,-10.00,900.00\n"
        "02/07/2026,BAD AMOUNT ROW,not-a-number,890.00\n"
    )
    with pytest.raises(StatementParseError) as exc:
        parse_statement(p, bank="generic")
    assert exc.value.row_number == 3
    assert "unparseable amount" in str(exc.value)


def test_wrong_column_count_raises(tmp_path: Path) -> None:
    p = tmp_path / "bad_columns.csv"
    p.write_text("Date,Description,Amount,Balance\n01/07/2026,MISSING BALANCE,-10.00\n")
    with pytest.raises(StatementParseError) as exc:
        parse_statement(p, bank="generic")
    assert exc.value.row_number == 2
    assert "expected 4 columns" in str(exc.value)


def test_blank_row_mid_file_raises_loudly(tmp_path: Path) -> None:
    p = tmp_path / "blank_mid.csv"
    p.write_text(
        "Date,Description,Amount,Balance\n"
        "01/07/2026,GOOD ROW,-10.00,900.00\n"
        "\n"
        "02/07/2026,AFTER BLANK,-5.00,895.00\n"
    )
    with pytest.raises(StatementParseError) as exc:
        parse_statement(p, bank="generic")
    assert exc.value.row_number == 3


def test_trailing_blank_line_at_eof_is_tolerated(tmp_path: Path) -> None:
    p = tmp_path / "trailing_blank.csv"
    p.write_text(
        "Date,Description,Amount,Balance\n01/07/2026,GOOD ROW,-10.00,900.00\n\n"
    )
    lines = parse_statement(p, bank="generic")
    assert len(lines) == 1


def test_empty_file_header_only_returns_empty_list(tmp_path: Path) -> None:
    p = tmp_path / "header_only.csv"
    p.write_text("Date,Description,Amount,Balance\n")
    assert parse_statement(p, bank="generic") == []


def test_completely_empty_file_is_a_mismatch(tmp_path: Path) -> None:
    """A file with no rows has no header, so it cannot be the named bank's.

    Reclassified in Task 8a from ``UnknownStatementFormatError`` for the same
    reason as the test above: "unknown format" now means "unknown *bank
    name*", which an empty file says nothing about.
    """
    p = tmp_path / "empty.csv"
    p.write_text("")
    with pytest.raises(StatementFormatMismatchError):
        parse_statement(p, bank="generic")


def test_utf8_bom_header_still_matches_generic_format(tmp_path: Path) -> None:
    """UK bank web-portal/Excel exports routinely prefix a UTF-8 BOM."""
    p = tmp_path / "bom.csv"
    p.write_bytes(
        "Date,Description,Amount,Balance\n01/07/2026,BOM ROW,-1.00,2.00\n".encode(
            "utf-8-sig"
        )
    )
    lines = parse_statement(p, bank="generic")
    assert lines == [
        ParsedLine(
            date=date(2026, 7, 1), description="BOM ROW", amount=Decimal("-1.00")
        )
    ]


# ---------------------------------------------------------------------------
# Nationwide -- the format that motivated Task 8a. Every awkwardness below was
# measured from a real export, not anticipated: iso-8859-1 encoding, three
# account-summary rows plus a blank one before the header, money split across
# Paid out/Paid in, a pound sign inside each amount, and `dd Mon yyyy` dates.
# ---------------------------------------------------------------------------
def test_nationwide_parses_past_its_preamble_and_encoding() -> None:
    lines = parse_statement(FIXTURES / "nationwide.csv", bank="nationwide")
    assert len(lines) == 3, "three data rows below a four-row preamble"
    assert lines[0].date == date(2024, 10, 28)


def test_nationwide_paid_in_is_positive_and_paid_out_is_negative() -> None:
    """The sign convention is load-bearing, not cosmetic.

    ``src/core/quarters.py`` derives direction as *positive iff income*, so a
    two-column format that collapsed the pair the wrong way round would
    invert every export total while every row still looked plausible.
    """
    lines = parse_statement(FIXTURES / "nationwide.csv", bank="nationwide")
    assert lines[0].amount == Decimal("1000.00"), "Paid in -> positive"
    assert lines[1].amount == Decimal("-63.43"), "Paid out -> negative"


def test_nationwide_strips_the_pound_sign() -> None:
    lines = parse_statement(FIXTURES / "nationwide.csv", bank="nationwide")
    assert lines[2].amount == Decimal("-79.04")


def test_nationwide_row_with_neither_paid_in_nor_paid_out_raises(tmp_path: Path) -> None:
    """A row that moves no money is malformed, not a zero.

    Silently coercing it to zero would drop a real transaction from an export
    with no trace.
    """
    p = tmp_path / "nw_empty_amounts.csv"
    p.write_bytes(
        (
            "Account Name:,X\nAccount Balance:,\xa31\nAvailable Balance: ,\xa31\n\n"
            "Date,Transaction type,Description,Paid out,Paid in,Balance\n"
            "28 Oct 2024,Bank credit,NO AMOUNT,,,\xa34242.42\n"
        ).encode("iso-8859-1")
    )
    with pytest.raises(StatementParseError) as exc:
        parse_statement(p, bank="nationwide")
    assert exc.value.row_number == 6
    assert "neither" in str(exc.value).lower()


def test_nationwide_row_with_both_paid_in_and_paid_out_raises(tmp_path: Path) -> None:
    """Both columns populated is ambiguous; guessing which wins would be wrong."""
    p = tmp_path / "nw_both_amounts.csv"
    p.write_bytes(
        (
            "Account Name:,X\nAccount Balance:,\xa31\nAvailable Balance: ,\xa31\n\n"
            "Date,Transaction type,Description,Paid out,Paid in,Balance\n"
            "28 Oct 2024,Bank credit,BOTH,\xa310.00,\xa320.00,\xa34242.42\n"
        ).encode("iso-8859-1")
    )
    with pytest.raises(StatementParseError) as exc:
        parse_statement(p, bank="nationwide")
    assert "both" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# HSBC -- the format a header-keyed registry could not match even in principle,
# and the largest single source in the portfolio. Its export has no header row
# at all: line 1 is data.
# ---------------------------------------------------------------------------
def test_hsbc_has_no_header_row_and_parses_from_line_one() -> None:
    lines = parse_statement(FIXTURES / "hsbc.csv", bank="hsbc")
    assert len(lines) == 5, "all five rows are data; none is a header"
    assert lines[0].date == date(2025, 3, 30)
    assert lines[0].amount == Decimal("-150.00")


def test_hsbc_thousands_separators_parse_exactly() -> None:
    """`"7,422.28"` must be Decimal("7422.28") -- money, so exact, never float."""
    lines = parse_statement(FIXTURES / "hsbc.csv", bank="hsbc")
    assert lines[2].amount == Decimal("7422.28")
    assert lines[4].amount == Decimal("1350.00")


def test_hsbc_short_row_raises_on_the_column_count(tmp_path: Path) -> None:
    """With no header to verify against, the column count is the only check left.

    A headerless format cannot confirm the caller named the right bank, so
    this is the one structural signal that a wrong file was uploaded. It has
    to be loud.
    """
    p = tmp_path / "hsbc_short.csv"
    p.write_text("30/03/2025,ONLY TWO COLUMNS\n")
    with pytest.raises(StatementParseError) as exc:
        parse_statement(p, bank="hsbc")
    assert exc.value.row_number == 1, "no header, so the first data row is row 1"


# ---------------------------------------------------------------------------
# The three formats that already fitted the original design: one signed amount
# column, a header, UTF-8. They share a row parser, so what needs pinning per
# format is which column is which -- an index off by one is silent, and would
# put a reference where an amount belongs.
#
# Written after the formats were registered rather than before, contrary to
# this project's test-first rule. Each assertion below was therefore checked
# by mutating its format's column indices and confirming the test dies, which
# is the evidence test-first would otherwise have produced.
# ---------------------------------------------------------------------------
def test_starling_columns_map_to_the_right_fields() -> None:
    lines = parse_statement(FIXTURES / "starling.csv", bank="starling")
    assert len(lines) == 2
    assert lines[0].date == date(2025, 2, 3)
    assert lines[0].amount == Decimal("1700.00"), "rent in, positive"
    assert "Sample Lettings Ltd" in lines[0].description
    assert "59SAMPLERISE" in lines[0].description, "the reference identifies the property"
    assert lines[1].amount == Decimal("-84.20"), "direct debit out, negative"


def test_monzo_reads_amount_in_gbp_not_the_local_currency_amount() -> None:
    """Monzo has four money columns; only one is the right one.

    ``Amount`` (GBP) is what the ledger needs. ``Local amount`` is the
    foreign-currency figure, and ``Money Out``/``Money In`` are a redundant
    split of the same value. Reading ``Local amount`` would book a EUR figure
    as pounds -- plausible-looking and wrong.

    The fixture's first row is deliberately a **foreign** transaction, with
    ``Amount`` -13.20 GBP against ``Local amount`` -15.00 EUR. An earlier
    version used equal values, which made the two columns indistinguishable:
    a mutation swapping the index left all 28 tests green. That is the whole
    reason this row exists in this shape.
    """
    lines = parse_statement(FIXTURES / "monzo.csv", bank="monzo")
    assert lines[0].date == date(2025, 6, 1)
    assert lines[0].amount == Decimal("-13.20"), "GBP Amount, not the EUR Local amount"
    assert lines[1].amount == Decimal("1200.00")
    assert "Sample Tenant" in lines[1].description


def test_mettle_columns_map_to_the_right_fields() -> None:
    lines = parse_statement(FIXTURES / "mettle.csv", bank="mettle")
    assert lines[0].date == date(2025, 12, 29)
    assert lines[0].amount == Decimal("-75.00")
    assert "Sample Accountants Limited" in lines[0].description


def test_row_number_survives_embedded_newline_in_earlier_row(tmp_path: Path) -> None:
    """Row numbers must reflect physical CSV lines, not record count.

    A quoted description containing a newline spans two physical lines for
    one record, so a later bad row's *physical* row number is higher than
    its record index.
    """
    p = tmp_path / "embedded_newline.csv"
    p.write_text(
        "Date,Description,Amount,Balance\n"
        '01/07/2026,"MULTI\nLINE DESC",-1.00,2.00\n'
        "not-a-date,BAD ROW,-2.00,3.00\n"
    )
    with pytest.raises(StatementParseError) as exc:
        parse_statement(p, bank="generic")
    # header=1, first record spans physical lines 2-3, bad row is physical line 4.
    assert exc.value.row_number == 4
