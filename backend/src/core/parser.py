"""Bank statement CSV parser.

Pure, dependency-free core logic that turns a bank-exported CSV file into
:class:`ParsedLine` records. Adding support for a new bank means adding a
:class:`StatementFormat` entry to ``_FORMATS`` plus a fixture -- the parsing
loop in :func:`parse_statement` never needs to change.

:seealso: spec open question 3 -- real bank fixtures will be swapped in once
    Mahmud supplies statement exports. Until then only one format is
    registered: a generic ``Date,Description,Amount,Balance`` CSV, with
    dates accepted as either ``dd/mm/yyyy`` or ``yyyy-mm-dd`` (UK bank
    exports vary between the two).
"""

from __future__ import annotations

import csv
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


class ParserError(Exception):
    """Base class for all statement-parsing failures."""


class UnknownStatementFormatError(ParserError):
    """Raised when a CSV's header row matches no registered :class:`StatementFormat`.

    :ivar header: the raw header row as read from the file (empty list if
        the file had no rows at all).
    """

    def __init__(self, header: list[str]) -> None:
        self.header = header
        super().__init__(f"unrecognised statement header: {header!r}")


class StatementParseError(ParserError):
    """Raised when a data row cannot be parsed.

    :ivar row_number: 1-based *physical* row number in the CSV file -- the
        header is row 1, so the first data row is row 2.
    :ivar message: human-readable reason, e.g. ``"unparseable date"``.
    """

    def __init__(self, row_number: int, message: str) -> None:
        self.row_number = row_number
        self.message = message
        super().__init__(f"row {row_number}: {message}")


@dataclass(frozen=True)
class ParsedLine:
    """A single parsed statement line.

    :ivar date: transaction date.
    :ivar description: raw description/narrative text from the statement.
    :ivar amount: signed amount; negative means money out.

    The statement's running ``Balance`` column (where present) is
    deliberately read-but-discarded and has no field here: it is
    bank-derived display data, and recomputing/storing our own running
    balance from it would invite false mismatches against the bank's
    rounding or ordering rather than telling us anything useful.
    """

    date: date
    description: str
    amount: Decimal


_GENERIC_DATE_FORMATS = ("%d/%m/%Y", "%Y-%m-%d")


def _parse_generic_date(raw: str) -> date:
    """Parse a date accepted by the generic format.

    :param raw: raw date cell, e.g. ``"01/07/2026"`` or ``"2026-07-01"``.
    :raises ValueError: if ``raw`` matches neither accepted format.
    """
    candidate = raw.strip()
    for fmt in _GENERIC_DATE_FORMATS:
        try:
            # Bank statement dates are calendar dates with no timezone concept;
            # the naive datetime is an intermediate discarded by .date() below.
            return datetime.strptime(candidate, fmt).date()  # noqa: DTZ007
        except ValueError:
            continue
    raise ValueError(f"unparseable date: {raw!r}")


def _parse_generic_amount(raw: str) -> Decimal:
    """Parse an amount, tolerating thousands separators (``"1,234.56"``).

    :param raw: raw amount cell.
    :raises ValueError: if ``raw`` is not a valid decimal number.
    """
    candidate = raw.strip().replace(",", "")
    try:
        return Decimal(candidate)
    except InvalidOperation as exc:
        raise ValueError(f"unparseable amount: {raw!r}") from exc


def _parse_generic_row(row: list[str], row_number: int) -> ParsedLine:
    """Parse one data row of the generic ``Date,Description,Amount,Balance`` format.

    :param row: raw CSV cells for this row.
    :param row_number: 1-based physical row number, used in error messages.
    :raises StatementParseError: wrong column count, unparseable date, or
        unparseable amount.
    """
    if len(row) != 4:
        raise StatementParseError(row_number, f"expected 4 columns, got {len(row)}")
    raw_date, description, raw_amount, _raw_balance = row
    try:
        parsed_date = _parse_generic_date(raw_date)
    except ValueError as exc:
        raise StatementParseError(row_number, str(exc)) from exc
    try:
        amount = _parse_generic_amount(raw_amount)
    except ValueError as exc:
        raise StatementParseError(row_number, str(exc)) from exc
    return ParsedLine(date=parsed_date, description=description.strip(), amount=amount)


@dataclass(frozen=True)
class StatementFormat:
    """Registry entry describing one bank's CSV export shape.

    :ivar header: normalised header signature (each column stripped and
        casefolded) this format matches.
    :ivar parse_row: parses one raw CSV row (plus its physical row number)
        into a :class:`ParsedLine`, raising :class:`StatementParseError` on
        bad data.
    """

    header: tuple[str, ...]
    parse_row: Callable[[list[str], int], ParsedLine]


GENERIC_FORMAT = StatementFormat(
    header=("date", "description", "amount", "balance"),
    parse_row=_parse_generic_row,
)

#: Registered statement formats, keyed by exact normalised header signature.
#: Add a bank by adding a new :class:`StatementFormat` entry here (plus a
#: fixture under ``tests/fixtures/statements/``) -- nothing else in this
#: module needs to change.
_FORMATS: dict[tuple[str, ...], StatementFormat] = {
    GENERIC_FORMAT.header: GENERIC_FORMAT
}


def _normalise_header(header: list[str]) -> tuple[str, ...]:
    """Normalise a raw header row for format matching: strip + casefold each cell."""
    return tuple(cell.strip().casefold() for cell in header)


def parse_statement(path: Path) -> list[ParsedLine]:
    """Parse a bank statement CSV file into :class:`ParsedLine` records.

    Never silently skips a bad row: the first unparseable row raises
    immediately, carrying its physical row number. A single blank line at
    the very end of the file is tolerated (a common CSV-export artefact);
    a blank row anywhere else is treated as data corruption and raises.

    :param path: path to the CSV file.
    :return: parsed lines in file order; ``[]`` for a header-only file.
    :raises UnknownStatementFormatError: the header row matches no
        registered :class:`StatementFormat` (including the case of a
        completely empty file, which has no header at all).
    :raises StatementParseError: a data row is malformed (wrong column
        count, unparseable date, or unparseable amount).
    """
    # utf-8-sig strips a leading UTF-8 BOM if present (common in exports from
    # UK bank web portals / Excel) and otherwise decodes plain UTF-8 unchanged.
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            raise UnknownStatementFormatError([]) from None

        normalised = _normalise_header(header)
        fmt = _FORMATS.get(normalised)
        if fmt is None:
            raise UnknownStatementFormatError(header)

        # reader.line_num is the CSV module's count of physical lines consumed
        # so far -- captured per-row so a quoted multi-line field doesn't throw
        # off the row numbers reported in errors (a plain enumerate() would).
        rows = [(reader.line_num, row) for row in reader]

    # Tolerate exactly one trailing blank line at EOF; anything else blank is loud.
    if rows and not rows[-1][1]:
        rows = rows[:-1]

    lines: list[ParsedLine] = []
    for row_number, row in rows:
        if not row:
            raise StatementParseError(row_number, "blank row")
        lines.append(fmt.parse_row(row, row_number))
    return lines
