# Bank statement formats — survey 2026-08-03, redacted 2026-08-05

Closes spec open question 3 ("the list of banks/accounts feeding the
portfolio"). Derived from real exports plus a previously-allocated
transactions sheet, **not** from memory.

> **This file was rewritten out of git history on 2026-08-05 and replaced with
> what you are reading.** The original carried the portfolio itself: twelve
> real addresses with postcodes and their owning companies, one flagged as a
> residential home, two real account balances, a real sort code and account
> number, a partial foreign account reference, and a third party's name. It
> was written when the repo had no remote and said so; that assumption broke
> when the repo was pushed to GitHub.
>
> **Do not put any of it back.** What follows is the engineering content only
> — which formats exist and how they differ — because that is the part the
> parser needs and the only part that was ever load-bearing. Everything
> removed lives in Mahmud's own records, off this repo.

## The banks — 7, after normalising spelling variants

| Bank | Real export in hand? | Fits the parser design? |
|---|---|---|
| HSBC | ✅ | ❌ headerless — the reason the registry is keyed by name |
| Starling Bank | ✅ | ✅ |
| Nationwide Building Society | ✅ | ❌ encoding + preamble |
| Barclays | ✅ | ✅ registered 2026-08-04 |
| Monzo | ✅ | ✅ |
| Mettle | ✅ | ✅ |
| first direct | ❌ none | unknown — lowest priority |

Sanitised samples for every registered format live in
`backend/tests/fixtures/statements/`. **Sanitised means fake account numbers,
balances and counterparty names, with real dates, amounts and column layout**
— the layout is the only part the parser cares about. That rule was stated in
the original of this file and was *not* followed for Nationwide: real balances
sat in `nationwide.csv` until the 2026-08-05 clean. Check a new fixture
against this rule before committing it, and do not assume an existing one is
clean because a sibling is.

## The quirks that shaped `core/parser.py`

- **HSBC has no header row at all.** This is why `_FORMATS` is keyed by bank
  name and `parse_statement` takes `bank=` as a required keyword — a registry
  keyed by header signature could not represent HSBC, and sniffing content
  would silently pick the wrong format for a file whose preamble resembles
  data.
- **Nationwide emits three account-summary rows and a blank one before its
  header**, hence `StatementFormat.header_row = 4`. It is also
  `iso-8859-1`, because of the pound sign inside its amount cells; decoding it
  as UTF-8 raises. Its amounts are split across `Paid out` / `Paid in`
  columns rather than signed, so `_parse_nationwide_row` collapses the pair —
  and that direction is load-bearing, since a statement arriving with the
  wrong sign fails nothing and quietly proposes income for every expense.
- **Barclays embeds unquoted tabs inside description fields**, so its row
  parser opts into `collapse_whitespace`. Its exports also carry a trailing
  blank line, which `parse_statement` tolerates at EOF only.
- **Monzo and Mettle are ordinary** single-signed-amount-column exports and
  use `_single_amount_row`.

## Scope decisions still in force

- **A residential home in the data is out of scope.** Its transactions are
  `personal_non_business`, never a property expense. It accounted for roughly
  a third of the allocation rows.
- **An overseas holiday let is out of scope for the MVP.** It is a separate
  income source from a UK property business, and furnished-holiday-letting
  treatment was abolished from April 2025. Its income arrives through a
  foreign account whose format is therefore not needed either.
- Together these remove about **35%** of the allocation rows from scope, which
  materially changes what the golden set can be drawn from.
- A number of rows have no property or no owner attributed; those need
  attributing before they can be used as labelled data.

:seealso: `backend/src/core/parser.py` (the registry and every format);
    `README.md` "Adding a bank format".
