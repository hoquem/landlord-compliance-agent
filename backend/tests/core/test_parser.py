"""Tests for the bank statement CSV parser.

:seealso: ``backend/src/core/parser.py``.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from src.core.parser import (
    ParsedLine,
    StatementParseError,
    UnknownStatementFormatError,
    parse_statement,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "statements"


def test_parses_generic_csv() -> None:
    lines = parse_statement(FIXTURES / "generic.csv")
    assert len(lines) == 4
    assert lines[0] == ParsedLine(
        date=date(2026, 7, 1), description="B&Q LUTON", amount=Decimal("-84.99")
    )


def test_malformed_row_fails_loudly() -> None:
    with pytest.raises(StatementParseError) as exc:
        parse_statement(FIXTURES / "malformed.csv")
    assert exc.value.row_number == 3  # exact failing row surfaced
    assert "unparseable date" in str(exc.value)


def test_unknown_format_fails_loudly() -> None:
    with pytest.raises(UnknownStatementFormatError):
        parse_statement(FIXTURES / "unknown_headers.csv")


def test_yyyy_mm_dd_date_accepted_for_generic_format() -> None:
    lines = parse_statement(FIXTURES / "generic.csv")
    assert lines[1].date == date(2026, 7, 3)


def test_amount_with_thousands_separator_parses() -> None:
    lines = parse_statement(FIXTURES / "generic.csv")
    assert lines[3].amount == Decimal("1234.56")


def test_unparseable_amount_raises_with_row_number(tmp_path: Path) -> None:
    p = tmp_path / "bad_amount.csv"
    p.write_text(
        "Date,Description,Amount,Balance\n"
        "01/07/2026,GOOD ROW,-10.00,900.00\n"
        "02/07/2026,BAD AMOUNT ROW,not-a-number,890.00\n"
    )
    with pytest.raises(StatementParseError) as exc:
        parse_statement(p)
    assert exc.value.row_number == 3
    assert "unparseable amount" in str(exc.value)


def test_wrong_column_count_raises(tmp_path: Path) -> None:
    p = tmp_path / "bad_columns.csv"
    p.write_text("Date,Description,Amount,Balance\n01/07/2026,MISSING BALANCE,-10.00\n")
    with pytest.raises(StatementParseError) as exc:
        parse_statement(p)
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
        parse_statement(p)
    assert exc.value.row_number == 3


def test_trailing_blank_line_at_eof_is_tolerated(tmp_path: Path) -> None:
    p = tmp_path / "trailing_blank.csv"
    p.write_text(
        "Date,Description,Amount,Balance\n01/07/2026,GOOD ROW,-10.00,900.00\n\n"
    )
    lines = parse_statement(p)
    assert len(lines) == 1


def test_empty_file_header_only_returns_empty_list(tmp_path: Path) -> None:
    p = tmp_path / "header_only.csv"
    p.write_text("Date,Description,Amount,Balance\n")
    assert parse_statement(p) == []


def test_file_with_no_rows_at_all_raises_unknown_format(tmp_path: Path) -> None:
    p = tmp_path / "empty.csv"
    p.write_text("")
    with pytest.raises(UnknownStatementFormatError):
        parse_statement(p)


def test_utf8_bom_header_still_matches_generic_format(tmp_path: Path) -> None:
    """UK bank web-portal/Excel exports routinely prefix a UTF-8 BOM."""
    p = tmp_path / "bom.csv"
    p.write_bytes(
        "Date,Description,Amount,Balance\n01/07/2026,BOM ROW,-1.00,2.00\n".encode(
            "utf-8-sig"
        )
    )
    lines = parse_statement(p)
    assert lines == [
        ParsedLine(
            date=date(2026, 7, 1), description="BOM ROW", amount=Decimal("-1.00")
        )
    ]


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
        parse_statement(p)
    # header=1, first record spans physical lines 2-3, bad row is physical line 4.
    assert exc.value.row_number == 4
