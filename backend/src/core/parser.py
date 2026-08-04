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


#: Currency symbols to strip before parsing an amount. Nationwide writes
#: ``£1000.00`` inside the cell rather than a bare number.
_CURRENCY_SYMBOLS = "£"


def _parse_pound_amount(raw: str) -> Decimal:
    """Parse an amount that may carry a pound sign and thousands separators.

    Kept separate from :func:`_parse_generic_amount` rather than folded into
    it: the generic format has never emitted a currency symbol, and quietly
    widening what it accepts would weaken a format whose strictness is
    tested. Formats opt in by using this parser.

    :param raw: raw amount cell, e.g. ``"£1,000.00"``.
    :raises ValueError: if what remains is not a valid decimal number.
    """
    candidate = raw.strip().lstrip(_CURRENCY_SYMBOLS).replace(",", "")
    try:
        return Decimal(candidate)
    except InvalidOperation as exc:
        raise ValueError(f"unparseable amount: {raw!r}") from exc


def _parse_nationwide_row(row: list[str], row_number: int) -> ParsedLine:
    """Parse one Nationwide ``Paid out``/``Paid in`` row.

    Nationwide splits money across two columns instead of signing one, so
    the pair is collapsed here. **Direction is the whole point:** ``Paid in``
    becomes positive and ``Paid out`` negative, matching the convention
    ``src/core/quarters.py`` derives totals from (positive iff income).
    Reversing it would invert every export while each row still looked
    perfectly plausible -- pinned by
    ``test_nationwide_paid_in_is_positive_and_paid_out_is_negative``.

    Exactly one of the two must be populated. Neither is a row that moves no
    money, which is malformed rather than a zero; both is ambiguous. Either
    raises rather than guessing, because both silent resolutions lose or
    invent money.

    :param row: raw CSV cells: date, type, description, paid out, paid in, balance.
    :param row_number: 1-based physical row number, for error messages.
    :raises StatementParseError: wrong column count, unparseable date or
        amount, or a Paid out/Paid in pair that is not exactly one value.
    """
    if len(row) != 6:
        raise StatementParseError(row_number, f"expected 6 columns, got {len(row)}")
    raw_date, txn_type, description, raw_out, raw_in, _raw_balance = row

    try:
        # `dd Mon yyyy`, e.g. "28 Oct 2024". Naive by design, as in
        # _parse_generic_date: a statement date is a calendar date with no
        # timezone concept, and the datetime is an intermediate discarded by
        # .date() on the same line.
        parsed_date = datetime.strptime(raw_date.strip(), "%d %b %Y").date()  # noqa: DTZ007
    except ValueError as exc:
        raise StatementParseError(row_number, f"unparseable date: {raw_date!r}") from exc

    paid_out, paid_in = raw_out.strip(), raw_in.strip()
    if paid_out and paid_in:
        raise StatementParseError(
            row_number, f"both Paid out ({paid_out!r}) and Paid in ({paid_in!r}) are set"
        )
    if not paid_out and not paid_in:
        raise StatementParseError(row_number, "neither Paid out nor Paid in is set")

    try:
        magnitude = _parse_pound_amount(paid_in or paid_out)
    except ValueError as exc:
        raise StatementParseError(row_number, str(exc)) from exc
    amount = magnitude if paid_in else -magnitude

    # Both cells carry signal the categoriser wants -- "Direct debit" is as
    # informative as the counterparty -- and neither is redundant, so keep
    # both rather than choosing.
    narrative = " ".join(part for part in (txn_type.strip(), description.strip()) if part)
    return ParsedLine(date=parsed_date, description=narrative, amount=amount)


def _parse_hsbc_row(row: list[str], row_number: int) -> ParsedLine:
    """Parse one HSBC ``date,description,amount`` row.

    HSBC's export has **no header row**, so ``row_number`` 1 is already data
    and there is no header to verify the caller's ``bank`` against. The
    column count is therefore the only structural evidence that the right
    file was uploaded, which is why a short row raises rather than being
    padded.

    Amounts arrive quoted with thousands separators (``"7,422.28"``);
    :func:`_parse_generic_amount` already strips those.

    :param row: raw CSV cells: date, description, signed amount.
    :param row_number: 1-based physical row number, for error messages.
    :raises StatementParseError: wrong column count, unparseable date, or
        unparseable amount.
    """
    if len(row) != 3:
        raise StatementParseError(row_number, f"expected 3 columns, got {len(row)}")
    raw_date, description, raw_amount = row
    try:
        parsed_date = _parse_generic_date(raw_date)
    except ValueError as exc:
        raise StatementParseError(row_number, str(exc)) from exc
    try:
        amount = _parse_generic_amount(raw_amount)
    except ValueError as exc:
        raise StatementParseError(row_number, str(exc)) from exc
    return ParsedLine(date=parsed_date, description=description.strip(), amount=amount)


def _single_amount_row(
    *,
    date_index: int,
    description_indices: tuple[int, ...],
    amount_index: int,
    columns: int,
    collapse_whitespace: bool = False,
) -> Callable[[list[str], int], ParsedLine]:
    """Build a row parser for a headered format with one signed amount column.

    Starling, Monzo, Mettle and Barclays differ only in which columns carry
    the date, the narrative and the amount, so they share one parser rather
    than four near-identical copies. Formats whose shape is genuinely
    different -- Nationwide's two-column money, HSBC's headerless rows --
    keep their own.

    :param date_index: column holding the transaction date.
    :param description_indices: columns to join into the narrative, in
        order; empty cells are skipped.
    :param amount_index: column holding the signed amount.
    :param columns: exact number of cells a valid row must have.
    :param collapse_whitespace: squeeze every run of whitespace in the
        narrative -- including embedded tabs -- down to one space. Opt-in
        rather than always-on: it is only right where the padding is a
        fixed-width layout artefact carrying no information, which is
        Barclays' ``Memo`` and no other format measured so far. Applying it
        everywhere would quietly change narratives whose spacing has never
        been shown to be meaningless.
    :returns: a ``parse_row`` callable for :class:`StatementFormat`.
    """

    def parse_row(row: list[str], row_number: int) -> ParsedLine:
        if len(row) != columns:
            raise StatementParseError(row_number, f"expected {columns} columns, got {len(row)}")
        try:
            parsed_date = _parse_generic_date(row[date_index])
        except ValueError as exc:
            raise StatementParseError(row_number, str(exc)) from exc
        try:
            amount = _parse_pound_amount(row[amount_index])
        except ValueError as exc:
            raise StatementParseError(row_number, str(exc)) from exc
        narrative = " ".join(
            part for part in (row[i].strip() for i in description_indices) if part
        )
        if collapse_whitespace:
            # `split()` with no argument splits on any run of whitespace,
            # so this removes padding runs and embedded tabs in one step.
            narrative = " ".join(narrative.split())
        return ParsedLine(date=parsed_date, description=narrative, amount=amount)

    return parse_row


GENERIC_FORMAT = StatementFormat(
    name="generic",
    parse_row=_parse_generic_row,
    header=("date", "description", "amount", "balance"),
    min_columns=4,
)

HSBC_FORMAT = StatementFormat(
    name="hsbc",
    parse_row=_parse_hsbc_row,
    # No header row at all -- see _parse_hsbc_row. This is the reason the
    # registry is keyed by bank name rather than header signature.
    header=None,
    min_columns=3,
)

STARLING_FORMAT = StatementFormat(
    name="starling",
    parse_row=_single_amount_row(
        date_index=0, description_indices=(1, 2, 3), amount_index=4, columns=8
    ),
    header=(
        "date",
        "counter party",
        "reference",
        "type",
        "amount (gbp)",
        "balance (gbp)",
        "spending category",
        "notes",
    ),
    min_columns=8,
)

MONZO_FORMAT = StatementFormat(
    name="monzo",
    # Monzo carries both a signed `Amount` and a redundant Money Out/Money In
    # pair. The signed column is used: one source, no pair to reconcile.
    parse_row=_single_amount_row(
        date_index=1, description_indices=(4, 14), amount_index=7, columns=18
    ),
    header=(
        "transaction id",
        "date",
        "time",
        "type",
        "name",
        "emoji",
        "category",
        "amount",
        "currency",
        "local amount",
        "local currency",
        "notes and #tags",
        "address",
        "receipt",
        "description",
        "category split",
        "money out",
        "money in",
    ),
    min_columns=18,
)

METTLE_FORMAT = StatementFormat(
    name="mettle",
    parse_row=_single_amount_row(
        date_index=0, description_indices=(4, 3), amount_index=1, columns=10
    ),
    header=(
        "date",
        "amount in gbp",
        "balance",
        "reference",
        "description",
        "transaction type",
        "invoices",
        "receipts",
        "note",
        "category",
    ),
    min_columns=10,
)

BARCLAYS_FORMAT = StatementFormat(
    name="barclays",
    # `Number` (a cheque number, 0 when there isn't one) and `Account` (sort
    # code + account number, identical on every row of a statement) carry
    # nothing a transaction needs, so neither reaches the narrative. The
    # leading tab that begins every row after the first lands in `Number`
    # and is discarded with it.
    parse_row=_single_amount_row(
        date_index=1,
        # Subcategory first: "Standing Order" is what distinguishes a
        # mortgage payment from a one-off transfer, and the categoriser
        # leans on it as much as on the counterparty.
        description_indices=(4, 5),
        amount_index=3,
        columns=6,
        # `Memo` is a fixed-width mainframe field: runs of padding spaces
        # and unquoted embedded tabs. Measured from a live export.
        collapse_whitespace=True,
    ),
    header=("number", "date", "account", "amount", "subcategory", "memo"),
    min_columns=6,
)

NATIONWIDE_FORMAT = StatementFormat(
    name="nationwide",
    parse_row=_parse_nationwide_row,
    header=("date", "transaction type", "description", "paid out", "paid in", "balance"),
    # Three account-summary rows and one blank row precede the header.
    header_row=4,
    # The pound sign makes this iso-8859-1; reading it as UTF-8 raises.
    encoding="iso-8859-1",
    min_columns=6,
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
_FORMATS: dict[str, StatementFormat] = {
    fmt.name: fmt
    for fmt in (
        GENERIC_FORMAT,
        HSBC_FORMAT,
        NATIONWIDE_FORMAT,
        STARLING_FORMAT,
        MONZO_FORMAT,
        METTLE_FORMAT,
        BARCLAYS_FORMAT,
    )
}


def is_registered_bank(bank: str) -> bool:
    """Whether a format is registered under ``bank``.

    Lets a caller reject an unknown bank *before* doing expensive or
    irreversible work -- the imports endpoint checks this before storing an
    uploaded file, so a file that could never have been read does not leave
    an orphaned object behind. Public so that callers need not reach into
    ``_FORMATS``.

    :param bank: the bank name to check.
    :returns: ``True`` if :func:`parse_statement` would accept it.
    """
    return bank in _FORMATS


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

    # Tolerate exactly one trailing blank line at EOF; anything else blank is
    # loud. "Blank" means every cell is empty once stripped, not just a row
    # with no cells: Barclays' export ends with a line containing a single
    # tab, which is the same artefact wearing different whitespace. No
    # format has a data row that is empty in every cell, so this can only
    # ever discard something that was never a transaction -- and only at
    # EOF, so a gap in the middle of a file still raises. Both halves pinned
    # by ``test_trailing_whitespace_only_line_at_eof_is_tolerated`` and
    # ``test_a_whitespace_only_row_mid_file_still_raises``.
    if rows and not any(cell.strip() for cell in rows[-1][1]):
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
