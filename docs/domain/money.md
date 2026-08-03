# Domain — Money context

The context that turns bank statement lines into per-entity HMRC figures.
Everything here exists to answer: *whose income or expense was this, in which
category, in which quarter, and for how much?*

**Sibling context:** `compliance.md`. They share the word `property` and mean
different things by it — see *Context map*.

## Glossary

| Term | Definition | Is NOT | Synonyms in use | Related |
|---|---|---|---|---|
| **Org** | The tenant boundary. One customer's whole world; every table is scoped to it. | A company, a legal entity, or a taxpayer. An org is a *login boundary*, nothing more. | "account", "workspace", "tenant" — none of these appear in code | Entity, User |
| **Entity** | An **ownership entity**: the person or Ltd company that files a return. The unit HMRC aggregates to. | An org. Also not "any addressable resource" — that generic sense leaks in from `scoping.not_found(what=...)` and must not spread. | "owner", "taxpayer", "filer" | Org, Ownership share |
| **Property** *(money face)* | A rental asset whose income and expenses are apportioned between entities. Here it is essentially **an apportionment key with a Section 24 flag**. | A dwelling with certificates — that is `compliance.md`'s property. | "unit", "house", "BTL" | Ownership share, Finance cost classification |
| **Ownership share** | One entity's percentage of one property, `numeric(5,2)`, `0 < share ≤ 100`. | A legal title share, a mortgage share, or a beneficial-interest declaration. It is the **tax attribution key** (HMRC PIM1035) and nothing else. | "split", "percentage" | Apportionment |
| **Ownership set** | *All* of one property's shares. Valid only as a whole: non-empty, each share > 0, summing to **exactly 100**. | A collection of independent rows. Individually valid rows can form an invalid set. | "the split" | Apportionment |
| **Apportionment** | Dividing one amount across an ownership set so every penny lands on exactly one entity and the parts sum back to the original. Largest-remainder. | Rounding each share independently — that drifts, and drift in money is a defect. | "split", "allocation" | `src/core/splits.py` |
| **Transaction** | One categorised, confirmed money movement attributable to a property and period. | A statement line. A line becomes a transaction only after review. | — | Statement line, Import |
| **Statement line** | One parsed row of a bank CSV: date, description, amount. Uncategorised, unattributed. | A transaction. | "row", "entry" | Import, Parsed line |
| **Import** | One statement file's journey: uploaded → parsed → proposed → reviewed. Fails loudly and wholly; never partially. | A sync or a feed. There is no live bank connection. | "upload" | Statement line |
| **HMRC category** | One of the **15** SA105 categories. The single source of truth is `src/core/categories.py`; the SQL enum mirrors it and drift is tested both ways. | A user-defined tag or a chart-of-accounts code. The set is fixed by HMRC, not by us. | "category" | Finance cost classification |
| **Finance cost classification** | Per property: `residential` or `non_residential`. Routes a finance cost to `finance_costs_residential` or `finance_costs_nonresidential`. | A property type or a mortgage type. It is a **tax-treatment switch**: residential finance costs are restricted to basic-rate relief under Section 24, non-residential are not. | "Section 24 flag" | HMRC category |
| **Tax year** | 6 April to 5 April, written `2025-26`. | A calendar year or an accounting period. | — | Quarter |
| **Quarter** | One of the four MTD update periods within a tax year. | A calendar quarter, unless `quarter_basis` says `calendar_election`. | "update period" | Quarter basis, Cumulative totals |
| **Quarter basis** | Per entity: `tax_year` (6 Apr aligned) or `calendar_election`. | A filing frequency. It changes period *boundaries*, not how often you file. | — | Quarter |
| **Cumulative totals** | Year-to-date figures submitted per quarter. Each submission **replaces** the prior one rather than adding to it (2025-26 onward). | Per-quarter deltas. Submitting deltas would overstate the year. | "YTD" | Quarter |

## Invariants

- **An ownership set sums to exactly 100.** Enforced at `backend/src/core/splits.py:113` (`_validate_shares`) and independently at `backend/src/api/routers/portfolio.py:650`; kept in step by `test_an_api_accepted_ownership_set_is_usable_by_split_amount`. **Deliberately not a database constraint** — `supabase/migrations/0001_core.sql:228-234` explains why: ownership is edited row-by-row through transiently-invalid totals.
- **Apportioned parts sum back to the original amount, to the penny.** Enforced by a postcondition in `split_amount`; proven by a 20,000-trial stress test.
- **Money is `Decimal`, never `float`.** Pydantic builds each `Decimal` from raw JSON text with no float intermediate; pinned by `test_put_ownership_sums_json_numbers_exactly` rather than assumed.
- **Sign convention: an amount is positive iff the category is income.** Pinned in `src/core/quarters.py`.
- **Cumulative totals never decrease quarter-over-quarter** without an explicit recompute — the guard against data deleted after an export.
- **Every row is scoped to one org, filtered in the API.** RLS is inert on API paths; see `ENGINEERING.md`.

## Aggregates

| Aggregate root | Owns | Entry point | Invariant it protects |
|---|---|---|---|
| **Ownership set** *(proposed, not yet built)* | all `property_ownership` rows for one property | `PUT /properties/{id}/ownership` | shares sum to exactly 100 |

This is the audit's top finding: the invariant is real and enforced twice, but
**no root owns it**, so nothing stops a future router writing
`property_ownership` directly. See `docs/engineering-audit-2026-08-03.md`, fix 1.

## Context map

| Neighbour | Relationship | Translation at the seam |
|---|---|---|
| **Compliance** (`compliance.md`) | Shared kernel on `Org`, `Entity`, `Property` *identity* only | The two contexts share a property's **id**, never its meaning. Money reads `finance_cost_classification`; compliance reads `epc_*` and `licensing_flag`. Neither should read the other's fields. |
| **HMRC** (external) | Conformist | We adopt HMRC's categories and periods wholesale. `HmrcCategory` is the anti-corruption boundary: nothing upstream of it invents a category. |
| **Banks** (external) | Anti-corruption layer | `src/core/parser.py` translates each bank's CSV into `ParsedLine`. No bank's column names reach beyond it. |

## Terms deliberately excluded

- **`entity` in the generic "any resource" sense.** It survives in
  `scoping.get_owned_or_404(what="entity")` and `not_found`, where it means
  *resource kind*. That is a collision, recorded in the audit. Do not spread
  it — in this context `entity` always means the tax filer.
- **Certificate, EPC, licensing, tenancy** — compliance terms. A money-context
  module reading `epc_expiry` is a signal the seam has been crossed.
- **"Landlord"** — ambiguous between org, user and entity. Never used in code.
