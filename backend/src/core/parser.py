"""Bank statement CSV parser.

Pure, dependency-free core logic that turns a bank-exported CSV file into
:class:`ParsedLine` records.

**The caller names the bank; this module never guesses.** ``_FORMATS`` is
keyed by bank name, and :func:`parse_statement` takes a required ``bank``
argument matching ``imports.source_bank`` (NOT NULL in
``supabase/migrations/0001_core.sql:293``, so it is always known at import
time). Adding a bank means adding a :class:`StatementFormat` entry plus a
fixture; the parsing loop does not change.

That choice is load-bearing rather than stylistic. A real HSBC export has
**no header row at all**, so a registry keyed by header signature -- which
this module used until Task 8a -- could not match it even in principle, and
HSBC is the largest single source in the portfolio. Content-sniffing was the
obvious alternative and is the worse one: it must guess, while a caller that
already knows cannot. The header keeps a job, but a different one --
*verifying* the caller's claim, so that uploading a Nationwide export under
``bank="hsbc"`` raises :class:`StatementFormatMismatchError` instead of
being fed to the wrong row parser and producing plausible, wrong money.

Formats vary in more than their columns, and each variation here was
measured from a real export rather than anticipated: Nationwide is
``iso-8859-1`` (the pound sign) and puts three account-summary rows and a
blank one *before* its header; HSBC has no header and quotes thousands
separators inside amounts; Nationwide splits money across ``Paid out`` and
``Paid in`` columns instead of signing one. See
``docs/planning/bank-formats.md`` for the survey these came from, including
the formats deliberately not supported and why.

:seealso: ``docs/planning/bank-formats.md``; ``backend/src/core/quarters.py``
    for the sign convention a two-column format must honour (positive iff
    income), since getting it backwards inverts every export total.
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
    """Raised when no :class:`StatementFormat` is registered under a bank name.

    :ivar bank: the unregistered name the caller asked for.
    """

    def __init__(self, bank: str) -> None:
        self.bank = bank
        # `_FORMATS` is defined below this class; resolved at call time, not
        # at class-definition time, so the forward reference is fine. Naming
        # the known banks turns a typo into a self-answering error.
        known = ", ".join(sorted(_FORMATS)) if _FORMATS else "(none)"
        super().__init__(f"no statement format registered for bank {bank!r}; known: {known}")


class StatementFormatMismatchError(ParserError):
    """Raised when a file's header contradicts the bank the caller named.

    The caller asserts the bank (``imports.source_bank`` is NOT NULL, so it
    is always known); this is the check on that assertion. Uploading a
    Nationwide export under ``bank="hsbc"`` must fail here rather than be
    parsed by the wrong row parser into plausible, wrong money.

    :ivar bank: the bank the caller named.
    :ivar expected: the header that format expects.
    :ivar actual: the normalised header actually found.
    """

    def __init__(self, bank: str, expected: tuple[str, ...], actual: tuple[str, ...]) -> None:
        self.bank = bank
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"file does not match the format for bank {bank!r}: "
            f"expected header {expected!r}, found {actual!r}"
        )


class StatementDecodeError(ParserError):
    """Raised when a file cannot be decoded with its format's encoding.

    Never recovered from by re-reading with ``errors="replace"``: a mangled
    description is silent corruption, and this module's whole contract is
    that bad input is loud.

    :ivar bank: the bank whose format supplied the encoding.
    :ivar encoding: the encoding that failed.
    """

    def __init__(self, bank: str, encoding: str, message: str) -> None:
        self.bank = bank
        self.encoding = encoding
        super().__init__(f"cannot decode statement for bank {bank!r} as {encoding}: {message}")


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

    :ivar name: registry key. Matches ``imports.source_bank``, which the
        caller supplies -- this format is *selected by name*, not sniffed.
    :ivar parse_row: parses one raw CSV row (plus its physical row number)
        into a :class:`ParsedLine`, raising :class:`StatementParseError` on
        bad data.
    :ivar header: the normalised header this format expects (each cell
        stripped and casefolded), or ``None`` for exports with **no header
        row at all** (HSBC). When present it verifies the caller's ``bank``
        claim; when ``None`` only ``min_columns`` remains as a check.
    :ivar header_row: 0-based index of the header row, for exports that
        precede it with a preamble block. Nationwide emits three account
        summary rows and a blank one before its header, so 4.
    :ivar encoding: text encoding. Nationwide is ``iso-8859-1`` because of
        the pound sign; decoding it as UTF-8 raises.
    :ivar min_columns: minimum cells a data row must have. The only
        structural check available when ``header`` is ``None``.
    """

    name: str
    parse_row: Callable[[list[str], int], ParsedLine]
    header: tuple[str, ...] | None = None
    header_row: int = 0
    encoding: str = "utf-8-sig"
    min_columns: int = 1


GENERIC_FORMAT = StatementFormat(
    name="generic",
    parse_row=_parse_generic_row,
    header=("date", "description", "amount", "balance"),
    min_columns=4,
)

#: Registered statement formats, keyed by **bank name**.
#:
#: Keyed by name rather than by header signature because the caller always
#: knows which bank a file came from -- ``imports.source_bank`` is NOT NULL
#: (``0001_core.sql:293``). That is not a convenience: HSBC's export has no
#: header row at all, so a header-keyed registry could not match it even in
#: principle, and sniffing would have to guess where being told cannot. The
#: header still earns its place as *verification*, turning "wrong file for
#: this bank" into :class:`StatementFormatMismatchError` rather than a quiet
#: mis-parse by the wrong row parser.
_FORMATS: dict[str, StatementFormat] = {GENERIC_FORMAT.name: GENERIC_FORMAT}


def _normalise_header(header: list[str]) -> tuple[str, ...]:
    """Normalise a raw header row for format matching: strip + casefold each cell."""
    return tuple(cell.strip().casefold() for cell in header)


def parse_statement(path: Path, *, bank: str) -> list[ParsedLine]:
    """Parse one bank's statement CSV into :class:`ParsedLine` records.

    Never silently skips a bad row: the first unparseable row raises
    immediately, carrying its physical row number. A single blank line at
    the very end of the file is tolerated (a common CSV-export artefact);
    a blank row anywhere else is treated as data corruption and raises.

    :param path: path to the CSV file.
    :param bank: which bank exported it -- a key of :data:`_FORMATS`, and
        the same value stored in ``imports.source_bank``. Keyword-only, and
        required: the format is chosen by name, never guessed from content.
    :return: parsed lines in file order; ``[]`` for a file with no data rows.
    :raises UnknownStatementFormatError: no format is registered under
        ``bank``.
    :raises StatementDecodeError: the file is not decodable with the
        format's encoding.
    :raises StatementFormatMismatchError: the format expects a header and
        the file's does not match -- i.e. this file is not from ``bank``.
    :raises StatementParseError: a data row is malformed (too few columns,
        unparseable date, or unparseable amount).
    """
    fmt = _FORMATS.get(bank)
    if fmt is None:
        raise UnknownStatementFormatError(bank)

    # The default utf-8-sig strips a leading UTF-8 BOM if present (common in
    # exports from UK bank web portals / Excel) and otherwise decodes plain
    # UTF-8 unchanged. Formats override it where the bank differs.
    try:
        with path.open(newline="", encoding=fmt.encoding) as f:
            reader = csv.reader(f)
            # Discard the preamble, then the header, by explicit index. NOT by
            # scanning for the first row that looks like a header: a scan
            # silently picks the wrong row in a file whose preamble resembles
            # data, and silently-wrong is the failure this module refuses.
            if fmt.header is not None:
                for _ in range(fmt.header_row):
                    next(reader, None)
                header = next(reader, None)
                if header is None:
                    raise StatementFormatMismatchError(bank, fmt.header, ())
                normalised = _normalise_header(header)
                if normalised != fmt.header:
                    raise StatementFormatMismatchError(bank, fmt.header, normalised)

            # reader.line_num is the CSV module's count of physical lines
            # consumed so far -- captured per-row so a quoted multi-line field
            # doesn't throw off the row numbers reported in errors (a plain
            # enumerate() would).
            rows = [(reader.line_num, row) for row in reader]
    except UnicodeDecodeError as exc:
        raise StatementDecodeError(bank, fmt.encoding, str(exc)) from exc

    # Tolerate exactly one trailing blank line at EOF; anything else blank is loud.
    if rows and not rows[-1][1]:
        rows = rows[:-1]

    lines: list[ParsedLine] = []
    for row_number, row in rows:
        if not row:
            raise StatementParseError(row_number, "blank row")
        # Only for headerless formats. A format with a header has already had
        # its shape verified, and its own parse_row checks the exact column
        # count with a better message; applying this too would duplicate that
        # check and mask it.
        if fmt.header is None and len(row) < fmt.min_columns:
            raise StatementParseError(
                row_number, f"expected at least {fmt.min_columns} columns, got {len(row)}"
            )
        lines.append(fmt.parse_row(row, row_number))
    return lines
