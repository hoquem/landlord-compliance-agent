# Landlord Compliance Agent — MVP (Iteration 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** MTD-ready digital records for Mahmud's portfolio: CSV statement ingest → AI-proposed HMRC categorisation → human review/confirm → cumulative quarterly export pack, plus a static compliance-certificates page.

**Architecture:** Deterministic Postgres (Supabase) core owns all money/compliance data; CrewAI flows run in a background worker and only ever write `proposed` rows; FastAPI is the single write path with validation and audit logging; Flutter (web) frontend. Spec: `docs/superpowers/specs/2026-07-28-landlord-compliance-agent-design.md` — read it before starting.

**Tech Stack:** Python 3.12 + uv, FastAPI, SQLAlchemy 2 (async, asyncpg), Supabase (Postgres/Auth/Storage, CLI migrations), CrewAI (flows + `Agent.kickoff` with Pydantic `response_format`), pytest, Flutter (Material 3 + flutter_animate), Anthropic Claude via CrewAI LLM config.

**Repo layout (monorepo):**

```
backend/            # FastAPI app + worker + agent flows (single uv project)
  src/api/          #   FastAPI: routers, deps, auth
  src/core/         #   pure domain logic (parser, splits, quarters, categories) — no I/O
  src/db/           #   SQLAlchemy models, session, repositories
  src/worker/       #   job-queue poller running CrewAI flows
  src/flows/        #   CrewAI flow package (scaffolded by crewai CLI, then moved here)
  tests/
supabase/           # supabase CLI project: migrations/, config.toml
frontend/           # Flutter app (created with `flutter create`)
  lib/theme/        #   design tokens: M3 Expressive theme, motion durations/curves
  lib/features/     #   auth/, upload/, review/, certificates/, dashboard/
docs/
```

**Conventions for every task:** TDD (test first, watch it fail, minimal code, watch it pass, commit). Docstrings in reStructuredText. Fail loudly — no silent catches, no default fallbacks. Never mark a step done without showing the passing command output. Commit after every green.

**Run every project command from `backend/`, never the repo root.** The repo root is not a Python project, so `uv run` there falls back to the ambient environment (here: Anaconda, carrying **ruff 0.12.0**) instead of the project venv (**ruff 0.16.0**, pinned in `backend/pyproject.toml`). The two disagree — 0.12.0's defaults still include `E402`, so `ruff check` from the root reports import-placement errors on `evals/run_eval.py` and `scripts/seed_org.py` that the project's own ruff does not. This cost a false "verification failed" alarm during Task 13b review. Canonical forms:

```
cd backend && uv run --env-file ../.env pytest -q     # 200 tests as of Task 13b
cd backend && uv run ruff check .
```

**Verification commands whose passing means little, and the ones that discriminate.** The full suite passed identically before and after the Task 13a event-loop fix, and both `tests/db tests/api` orderings passed before it too — so neither proves anything about engine/event-loop hygiene. When a fix targets a specific failure, record and re-run the *narrow repro*, not the suite. Likewise for org isolation: an isolation test only counts if it dies when you delete the `org_id` filter it guards, so probe it (see Task 13b's seven-site probe).

**Security convention (spec §Security):** statement content (descriptions, amounts) must never appear in application logs at any level; agent prompts include only the fields categorisation needs. Applies to Tasks 11, 14, 18 — reviewers check for stray `logger.*(line...)` calls.

**Capital-vs-revenue note:** the spec's "capital flag" is represented by the `CAPITAL_EXPENSE` enum value, not a separate boolean. Do not add a redundant flag column.

---

## Phase 0 — Scaffolding

### Task 1: Backend project scaffold

**Files:**
- Create: `backend/pyproject.toml`, `backend/src/api/main.py`, `backend/tests/test_health.py`

- [ ] **Step 1:** `cd backend && uv init --python 3.12 && uv add fastapi 'uvicorn[standard]' sqlalchemy asyncpg pydantic-settings httpx && uv add --dev pytest pytest-asyncio ruff`. Later tasks add their own deps as needed (`supabase` storage client in Task 14, `weasyprint` in Task 16, `crewai` in Task 3) — add at point of use, not up front.
- [ ] **Step 2:** Write failing test `backend/tests/test_health.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app


@pytest.mark.asyncio
async def test_health() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
```

Run: `uv run pytest tests/test_health.py -v` → Expected: FAIL (no `src.api.main`).
- [ ] **Step 3:** Minimal `src/api/main.py` with FastAPI app + `/health`. Add `[tool.pytest.ini_options] asyncio_mode = "auto"` and `pythonpath = ["."]` to pyproject.
- [ ] **Step 4:** `uv run pytest -v` → PASS.
- [ ] **Step 5:** Add `backend/.gitignore` (`.venv/`, `__pycache__/`, `.env`), commit `feat: backend scaffold with health endpoint`.

### Task 2: Supabase project + local stack

**Files:**
- Create: `supabase/config.toml` (generated), `.env.example`

- [ ] **Step 1:** `supabase init` at repo root (installs `supabase/`). If CLI missing: `brew install supabase/tap/supabase`.
- [ ] **Step 2:** `supabase start` — verify local Postgres/Auth/Storage come up; note anon/service keys printed.
- [ ] **Step 3:** Create `.env.example` documenting `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`, `ANTHROPIC_API_KEY`. Real `.env` stays untracked.
- [ ] **Step 4:** Commit `chore: supabase local stack + env template`.

### Task 3: CrewAI flow scaffold

**Files:**
- Create: `backend/src/flows/` (scaffolded)

- [ ] **Step 1:** `uv add crewai` in backend. In a temp dir run `uvx crewai create flow landlord_compliance` (underscores, per CrewAI CLI requirement), then move `src/landlord_compliance/` content into `backend/src/flows/` keeping the crews/config layout; delete the generated example crew content we don't need but keep the structure. Do NOT hand-write scaffold files from scratch.
- [ ] **Step 2:** Smoke: `uv run python -c "from src.flows.main import *"` imports cleanly.
- [ ] **Step 3:** Commit `feat: crewai flow scaffold`.

### Task 4: Flutter app + theme package

**Files:**
- Create: `frontend/` (via `flutter create`), `frontend/lib/theme/tokens.dart`, `frontend/lib/theme/app_theme.dart`, `frontend/test/theme_test.dart`

- [ ] **Step 1:** `flutter create frontend --platforms web --org uk.hoque --project-name landlord_compliance` then `cd frontend && flutter pub add flutter_animate supabase_flutter go_router`.
- [ ] **Step 2:** Failing test `frontend/test/theme_test.dart`: builds `MaterialApp` with `AppTheme.light()`/`AppTheme.dark()`; asserts `useMaterial3` true and motion tokens exposed (`Motion.fast == Duration(milliseconds: 150)`, `Motion.standard == Duration(milliseconds: 250)`, `Motion.emphasized == Duration(milliseconds: 350)`).
- [ ] **Step 3:** Implement `tokens.dart` (seed colour, spacing scale, radii, `Motion` durations + curves) and `app_theme.dart` (`ColorScheme.fromSeed`, both brightnesses, type scale). Respect `MediaQuery.disableAnimations` via a `Motion.of(context)` helper returning `Duration.zero` when disabled.
- [ ] **Step 4:** `flutter test` → PASS. Commit `feat: flutter scaffold with M3 theme + motion tokens`.

---

## Phase 1 — Schema & RLS

### Task 5: Core schema migration

**Files:**
- Create: `supabase/migrations/0001_core.sql`
- Test: `backend/tests/db/test_schema.py`

- [ ] **Step 1:** Write `0001_core.sql` exactly per spec data model: `orgs`, `users` (mirror of auth.users with org_id), `entities` (incl. `tax_regime` enum `mtd_itsa|corporation_tax`, `quarter_basis` enum default `tax_year`, PRS fields nullable), `properties` (incl. `finance_cost_classification`, `epc_rating`, `epc_expiry`, `bedroom_count`, `licensing_flag`), `property_ownership` (unique (property_id, entity_id), `ownership_percentage numeric(5,2)`), `tenancies` (schema-only in MVP — no API/UI until iteration 2's Section 13 work), `imports`, `transactions` (incl. `hmrc_category` enum with the 15 spec values, `status` enum `unclassified|proposed|confirmed|excluded`, `confidence numeric(3,2)`, `proposed_by`), `compliance_certificates` (type enum, `issue_date`, `expiry_date`, `certificate_ref`, `document_id`), `documents`, `mtd_quarters` (unique (entity_id, tax_year, quarter), one numeric column per HMRC category total, `version`, `generated_at`), `job_queue`, `audit_log`. Every table: `org_id uuid not null references orgs`, timestamps. Constraint: trigger enforcing `sum(ownership_percentage) = 100` per property on confirm-time validation is done in API (documented in SQL comment) — DB enforces `0 < pct <= 100`.
- [ ] **Step 2:** `supabase db reset` → applies cleanly.
- [ ] **Step 3:** Failing test `test_schema.py`: connects via `DATABASE_URL`, asserts all 13 tables exist and `transactions.hmrc_category` enum has exactly the 15 spec values (guards drift between SQL and Python enum).
- [ ] **Step 4:** PASS, commit `feat: core schema migration`.

### Task 6: RLS policies + cross-tenant test

**Files:**
- Create: `supabase/migrations/0002_rls.sql`
- Test: `backend/tests/db/test_rls.py`

- [ ] **Step 1:** `0002_rls.sql`: enable RLS on all tables; policy per table: `org_id = (select org_id from public.users where id = auth.uid())`; service role bypasses (worker/API use service connection but always filter by org_id in queries — belt and braces).
- [ ] **Step 1b (from Task 5 review — service-role writes bypass RLS, so constrain at the DB):** add `unique(id, org_id)` on parent tables and composite FKs (`(property_id, org_id)`, `(entity_id, org_id)`, `(import_id, org_id)` etc. referencing them) so a service-role write can never stitch rows across orgs. Also pin `search_path` on `set_updated_at()` (`set search_path = ''` style) to clear the Supabase `function_search_path_mutable` advisor lint. Test: inserting a property_ownership row whose property and entity belong to different orgs fails with an FK violation.
- [ ] **Step 2:** Failing test: create two orgs + two auth users via service role; as user A (anon key + JWT) insert a property; as user B select properties → must see zero rows; attempt update of A's row as B → 0 rows affected.
- [ ] **Step 3:** PASS, commit `feat: RLS policies with cross-tenant test`.

### Task 7: SQLAlchemy models + session

**Files:**
- Create: `backend/src/db/models.py`, `backend/src/db/session.py`, `backend/src/core/categories.py`
- Test: `backend/tests/db/test_models_roundtrip.py`

- [ ] **Step 1:** `src/core/categories.py` — single source of truth:

```python
"""HMRC category enum for MTD ITSA property submissions.

:seealso: spec §Data model; SA105 mapping validated 2026-07-28.
"""
from enum import StrEnum


class HmrcCategory(StrEnum):
    RENT_INCOME = "rent_income"
    OTHER_PROPERTY_INCOME = "other_property_income"
    RENT_PAID = "rent_paid"
    RATES_INSURANCE_GROUND = "rates_insurance_ground"
    REPAIRS_MAINTENANCE = "repairs_maintenance"
    FINANCE_COSTS_RESIDENTIAL = "finance_costs_residential"
    FINANCE_COSTS_NONRESIDENTIAL = "finance_costs_nonresidential"
    LEGAL_PROFESSIONAL = "legal_professional"
    SERVICE_COSTS = "service_costs"
    TRAVEL_VEHICLE = "travel_vehicle"
    OTHER_ALLOWABLE = "other_allowable"
    REPLACEMENT_DOMESTIC_ITEMS = "replacement_domestic_items"
    USE_OF_HOME_ALLOWANCE = "use_of_home_allowance"
    CAPITAL_EXPENSE = "capital_expense"
    PERSONAL_NON_BUSINESS = "personal_non_business"


INCOME_CATEGORIES = {HmrcCategory.RENT_INCOME, HmrcCategory.OTHER_PROPERTY_INCOME}
EXCLUDED_FROM_EXPORT = {HmrcCategory.CAPITAL_EXPENSE, HmrcCategory.PERSONAL_NON_BUSINESS}
```

- [ ] **Step 2:** Declarative models mirroring migration; async `session.py` from `DATABASE_URL`. Failing round-trip test: insert org → entity → property → ownership 100% → select back.
- [ ] **Step 3:** PASS, commit `feat: sqlalchemy models + hmrc category source of truth`.

---

## Phase 2 — CSV Parser (pure core logic)

### Task 8: Statement parser

**Files:**
- Create: `backend/src/core/parser.py`, `backend/tests/fixtures/statements/generic.csv`, `backend/tests/fixtures/statements/malformed.csv`, `backend/tests/fixtures/statements/unknown_headers.csv`
- Test: `backend/tests/core/test_parser.py`

**NOTE (spec open question 3):** real bank fixture files must be swapped in when Mahmud provides statement exports; start with a generic `Date,Description,Amount,Balance` format and a format-registry design so adding a bank = adding a `StatementFormat` entry + fixture.

- [ ] **Step 1:** Failing tests:

```python
def test_parses_generic_csv() -> None:
    lines = parse_statement(FIXTURES / "generic.csv")
    assert len(lines) == 4
    assert lines[0] == ParsedLine(
        date=date(2026, 7, 1), description="B&Q LUTON", amount=Decimal("-84.99")
    )

def test_malformed_row_fails_loudly() -> None:
    with pytest.raises(StatementParseError) as exc:
        parse_statement(FIXTURES / "malformed.csv")
    assert exc.value.row_number == 3          # exact failing row surfaced
    assert "unparseable date" in str(exc.value)

def test_unknown_format_fails_loudly() -> None:
    with pytest.raises(UnknownStatementFormatError):
        parse_statement(FIXTURES / "unknown_headers.csv")
```

- [ ] **Step 2:** FAIL → implement `parser.py`: `ParsedLine` (frozen dataclass), `StatementFormat` registry keyed by header signature, `parse_statement()` that never skips rows — first bad row raises with row number. → PASS.
- [ ] **Step 3:** Commit `feat: statement parser with loud failures`.

---

### Task 8a: Real bank formats — the registry redesign (PREREQUISITE for Task 14)

**Added 2026-08-04, after the bank survey in `docs/planning/bank-formats.md`.** Task 8's note says "adding a bank = adding a `StatementFormat` entry + fixture". **That is true for Starling, Monzo and Mettle and false for HSBC and Nationwide, which are 60% of the portfolio's rows.** Task 14 cannot ingest a real file until this lands, so do it before Task 14 rather than discovering it mid-task.

**Files:**
- Modify: `backend/src/core/parser.py`
- Modify: `backend/tests/core/test_parser.py` (13 existing `parse_statement` call sites)
- Create: `backend/tests/fixtures/statements/{hsbc,nationwide,starling,monzo,mettle}.csv`

**Blast radius is tests only.** `parse_statement` has no production caller yet — `flows/categorise.py` only names it in a docstring. Changing its signature is cheap now and expensive after Task 14. That is the whole reason this task exists as a prerequisite.

#### The design decision, and why it is not "sniff harder"

`_FORMATS` is keyed by **normalised header signature**, so a headerless file (HSBC — the largest single source) cannot be matched even in principle. The instinct is to add shape-sniffing. **Don't.** `supabase/migrations/0001_core.sql:293` declares `source_bank text not null` on `imports` — **the schema already requires the caller to name the bank at import time.** So the parser never needed to guess; it needs to be told.

Key the registry by **bank name**, and demote the header from *detection* to *verification*. This is strictly better than sniffing: it dissolves the headerless problem rather than solving it, it makes "you uploaded a Nationwide file and called it HSBC" a loud named error instead of a silent mis-parse, and it removes the ambiguity two banks sharing a header signature would create. Sniffing would have to guess; being told cannot.

- [x] **Step 1:** Write failing tests for name-keyed dispatch and mismatch detection.

```python
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
```

- [x] **Step 2:** Run them. Expect `TypeError: parse_statement() got an unexpected keyword argument 'bank'` for the first two and `NameError` for `StatementFormatMismatchError`.
- [x] **Step 3:** Implement. `StatementFormat` gains `name`, an optional `header`, a `header_row`, and an `encoding`; `_FORMATS` is re-keyed by name; `parse_statement` takes `bank`.

```python
@dataclass(frozen=True)
class StatementFormat:
    name: str                                   # registry key; matches imports.source_bank
    parse_row: Callable[[list[str], int], ParsedLine]
    #: Expected normalised header, or None for exports that have no header
    #: row at all (HSBC). None means the header cannot verify the caller's
    #: `bank` claim -- `min_columns` is the weaker check that remains.
    header: tuple[str, ...] | None = None
    #: 0-based index of the header row, for exports that precede it with a
    #: preamble block (Nationwide: 4 rows of account summary, then a blank).
    header_row: int = 0
    #: Per-format text encoding. Nationwide emits iso-8859-1 because of the
    #: pound sign; decoding it as UTF-8 raises. Never pass errors="replace" --
    #: a silently mangled description is exactly the quiet corruption this
    #: module exists to prevent.
    encoding: str = "utf-8-sig"
    #: Minimum column count for a data row; the only structural check
    #: available when `header` is None.
    min_columns: int = 1
```

- [x] **Step 4:** Run. Expect PASS. Update the 13 existing call sites in `test_parser.py` to pass `bank="generic"`.
- [x] **Step 5:** Commit `refactor: key the statement registry by bank name, not header`.

- [x] **Step 5a (CORRECTION 2026-08-04, made while executing):** build the sanitised fixtures **here**, not at Step 12. Steps 6, 8 and 10 all read them, so the original ordering could not run. Content rules unchanged — see Step 12, which now only registers formats.

- [x] **Step 6:** Failing test for per-format encoding, using a **real** sanitised Nationwide export.

```python
def test_nationwide_is_decoded_as_iso_8859_1() -> None:
    """The pound sign makes Nationwide's export iso-8859-1, not UTF-8."""
    lines = parse_statement(FIXTURES / "nationwide.csv", bank="nationwide")
    assert lines[0].amount == Decimal("1000.00")

def test_undecodable_bytes_fail_loudly_rather_than_mangling() -> None:
    """A decode failure must raise, never substitute replacement characters.

    The generic format is utf-8-sig, and the Nationwide fixture is
    iso-8859-1, so reading one as the other is a genuine decode failure --
    and it happens while reading, before header verification.
    """
    with pytest.raises(StatementDecodeError):
        parse_statement(FIXTURES / "nationwide.csv", bank="generic")
```

**CORRECTION 2026-08-04, found while executing:** an earlier draft tested this with a fake `nationwide_utf8_probe` registry entry. Wrong twice over. Test-only machinery does not belong in the production registry — and more importantly, **`iso-8859-1` cannot raise `UnicodeDecodeError` at all**: it is a single-byte encoding mapping all 256 values, so every byte sequence decodes. `StatementDecodeError` is reachable only for a **UTF-8** format meeting non-UTF-8 bytes, which is exactly the real-world failure (reading Nationwide's export with the default encoding) and needs no fake entry.

- [x] **Step 7:** Run (FAIL), implement `encoding` in `path.open(...)` plus a `StatementDecodeError` wrapping `UnicodeDecodeError`, run (PASS), commit.

- [x] **Step 8:** Failing test for the preamble skip. Nationwide precedes its header with `"Account Name:"`, `"Account Balance:"`, `"Available Balance: "` and a blank row, so the real header is row index 4.

```python
def test_nationwide_header_is_found_below_its_preamble() -> None:
    lines = parse_statement(FIXTURES / "nationwide.csv", bank="nationwide")
    assert len(lines) == 3
    assert lines[0].description == "Bank credit"
```

- [x] **Step 9:** Implement `header_row` by consuming exactly that many rows before reading the header. **Use an explicit index, not a scan for the first row that looks like a header** — a scan silently picks the wrong row in a file whose preamble happens to resemble data, and silently-wrong is the failure mode this module refuses. Run, PASS, commit.

- [x] **Step 10:** Failing test for the headerless case (HSBC). Its rows are `30/03/2025,<description>,-150.00` with no header at all.

```python
def test_hsbc_has_no_header_row_and_parses_from_line_one() -> None:
    lines = parse_statement(FIXTURES / "hsbc.csv", bank="hsbc")
    assert len(lines) == 5
    assert lines[0].date == date(2025, 3, 30)

def test_hsbc_thousands_separators_parse_exactly() -> None:
    """`"7,422.28"` must be Decimal("7422.28") -- money, so no float anywhere."""
    lines = parse_statement(FIXTURES / "hsbc.csv", bank="hsbc")
    assert lines[2].amount == Decimal("7422.28")
```

- [x] **Step 11:** Implement: when `header is None`, do not consume a header row, and verify with `min_columns` instead. **`_parse_generic_amount` already strips thousands separators (`parser.py:101`)** — the survey overstated this as a gap; add the test anyway, because it is money and the behaviour must stay pinned. Run, PASS, commit.

- [x] **Step 12 (fixture content rules — the build itself moved to Step 5a):** the five fixtures come from the real exports listed in `docs/planning/bank-formats.md`. **Keep real dates, amounts and column layout; replace account numbers, balances and counterparty names with plausible substitutes.** `backend/tests/fixtures/statements/` is tracked, and although the repo has no remote today it may gain one.
- [x] **Step 13:** Register the five formats with their row parsers. Starling, Monzo and Mettle are single signed-amount formats and need little. Nationwide needs a `paid_out`/`paid_in` pair collapsed to one signed amount, a `£` prefix stripped, and `dd Mon yyyy` dates.

**The sign convention is load-bearing and already pinned elsewhere:** `src/core/quarters.py` derives direction as *positive iff income*, so a two-column format must produce `paid_in` positive and `paid_out` negative. Getting this backwards inverts every export total. Add one test per two-column format asserting both signs.

- [x] **Step 14:** Verify the whole suite plus both directory orderings and `ruff check`, from `backend/`. Commit.

**Explicitly out of scope** — record rather than absorb:
- **PDF statements.** Barclays and first direct were supplied as PDFs; see `bank-formats.md` for why a PDF is a worse source than a CSV (no year on dates, sparse dates, inline summary rows), not merely a different one.
- **xlsx.** Mettle offers both; use its CSV.
- **Barclays and first direct formats.** No CSV sample exists yet — 12% of rows, one of the two accounts dormant. Do not invent a format from a PDF.
- **Historic backfill.** Banks generally offer CSV only for recent months, so backfilling old periods is a separate problem from ongoing ingestion and must not quietly expand this task.

---

## Phase 3 — Ownership splits & quarters (pure core logic)

### Task 9: Ownership split maths

**Files:**
- Create: `backend/src/core/splits.py`
- Test: `backend/tests/core/test_splits.py`

- [ ] **Step 1:** Failing tests: 50/50 split of £1000.01 allocates £500.01/£500.00 (largest-remainder rounding, penny-exact — sum of shares MUST equal original); 100% single owner; percentages not summing to 100 → `InvalidOwnershipError`.
- [ ] **Step 2:** Implement `split_amount(amount: Decimal, shares: dict[UUID, Decimal]) -> dict[UUID, Decimal]`. → PASS, commit.

### Task 10: Tax-year quarters + cumulative totals

**Files:**
- Create: `backend/src/core/quarters.py`
- Test: `backend/tests/core/test_quarters.py`

- [ ] **Step 1:** Failing tests: `quarter_for(date(2026, 7, 5)) == (2026, 1)` and `quarter_for(date(2026, 7, 6)) == (2026, 2)` (UK tax-year quarters from 6 April); `cumulative_totals(confirmed_txns, entity_id, tax_year, quarter)` returns YTD per-category totals **from 6 April through quarter end**, ownership-weighted via `split_amount`, excluding `EXCLUDED_FROM_EXPORT` categories from allowable totals but reporting capital separately; Q2 totals ⊇ Q1 totals for same data (cumulative property test).
- [ ] **Step 1a (from Task 5 review):** Failing tests for `format_tax_year(2026) == "2026-27"` (and century boundary `format_tax_year(2099) == "2099-00"`) — the single source of the DB's `mtd_quarters.tax_year` string format, which now carries a CHECK constraint (`^\d{4}-\d{2}$`).
- [ ] **Step 1b:** Also failing tests: `next_update_deadline(date)` returns the next statutory quarterly-update deadline — 7 Aug, 7 Nov, 7 Feb, 7 May (the 7th of the month following quarter-end); pin all four boundaries. Transactions with `property_id` null (e.g. `use_of_home_allowance`, `travel_vehicle`) attribute 100% to `transactions.entity_id` — no ownership split applies (spec: entity_id determines whose ledger unallocated lines sit on).
- [ ] **Step 2:** Implement; pure functions over passed-in data (no DB). → PASS, commit.

---

## Phase 4 — Categorisation flow (CrewAI)

### Task 11: Proposal models + flow

**Files:**
- Create: `backend/src/flows/categorise.py`
- Test: `backend/tests/flows/test_categorise_models.py`

- [ ] **Step 1:** Pydantic models:

```python
class LineProposal(BaseModel):
    line_index: int
    hmrc_category: HmrcCategory
    property_id: UUID | None
    confidence: float = Field(ge=0, le=1)
    rationale: str

class StatementProposals(BaseModel):
    proposals: list[LineProposal]
```

Failing test: `StatementProposals` rejects out-of-range confidence and unknown category (Pydantic validation is the contract the LLM is retried against).
- [ ] **Step 2:** Implement `CategoriseStatementFlow` (CrewAI `Flow` with typed state): input = parsed lines + org property list + up to 50 most recent confirmed transactions (few-shot); one step calls `Agent.kickoff(prompt, response_format=StatementProposals)`. Agent config in `src/flows/crews/config/` YAML per CrewAI convention: role "UK landlord bookkeeping specialist", explicit instruction that uncertain lines get low confidence and `PERSONAL_NON_BUSINESS` is for non-business lines, never a guess. **LLM (decision 2026-07-29): fully env-driven — `CATEGORISER_MODEL` env var (LiteLLM id, e.g. `ollama/glm-4.5-air` or a hosted model) + optional `OLLAMA_BASE_URL`; add both to `.env.example`, `ANTHROPIC_API_KEY` becomes optional.** Mahmud intends a local open-weight model via Ollama (Kimi/GLM-class) for bank-data privacy; the flow must not hard-code any provider. Fail loudly at flow start if `CATEGORISER_MODEL` is unset. Verify the exact LiteLLM model string against whichever provider is configured at implementation time.
- [ ] **Step 3:** Unit test with mocked `Agent.kickoff` returning a fixture `StatementProposals`: flow maps proposals onto line indices, errors if the LLM returns indices out of range (loud). → PASS, commit.

### Task 12: Golden-set eval harness

**Files:**
- Create: `backend/evals/golden_set.jsonl` (seed with 20 synthetic labelled lines until Mahmud's real confirmed data exists), `backend/evals/run_eval.py`
- Test: `backend/tests/evals/test_eval_harness.py`

- [ ] **Step 1:** `run_eval.py`: runs the real flow against the golden set, prints per-category precision/recall + overall accuracy, exits non-zero below threshold (start 0.7; raise as the set grows). **Accepts `--model <litellm-id>` (default: `CATEGORISER_MODEL` env) so the harness doubles as a model-selection tool — head-to-head runs of local Ollama candidates (GLM-class) vs hosted models on the same golden set decide the production model empirically.** Harness unit-tested with a stubbed flow; the live eval is run manually/CI-nightly (`uv run python evals/run_eval.py`), not in the default test suite.
- [ ] **Step 2:** Commit `feat: categorisation golden-set eval harness`.

---

## Phase 5 — API

**Two constraints established by Task 13 that every router task below inherits:**

1. **RLS does not protect API paths — filter `org_id` by hand, always.** `src/db/session.py` builds its engine from `DATABASE_URL`, which is the `postgres` superuser; that role **bypasses row-level security**, so Task 6's policies are inert on anything the API does. `require_auth`'s 403 plus explicit `WHERE org_id = auth.org_id` filtering in every query IS the entire tenant boundary here. A forgotten filter is a silent cross-org data leak that no test in `tests/db/test_rls.py` will catch, because that suite exercises PostgREST with real user JWTs, not this connection. Every router task must include a test proving org A cannot reach org B's rows *through the API*.
2. **Adding `backend/tests/api/conftest.py` creates an order-dependent collection break — fix the imports first.** *(Corrected 2026-07-29 after the Task 13 quality review disproved the original, over-broad wording here; the earlier claim that it "silently breaks the db suite" was wrong.)* pytest itself defends against the module-name collision — it clears the cached `conftest` module before loading the next one (`_pytest/config/__init__.py`) — so each conftest loads fine and plain `pytest` passes the full 137 even with an api-level conftest present. The real hazard is narrower: `tests/db/test_schema.py`, `tests/db/test_rls.py`, and `tests/db/test_models_roundtrip.py` (**three** files, not one) import their fixtures *by bare module name* (`from conftest import ...`), so `sys.modules["conftest"]` holds whichever loaded last. Verified empirically: `pytest tests/db tests/api` → 3 collection errors; `pytest tests/api tests/db` → 36 passed; plain `pytest` → 137 passed.
   **The unblock (verified end-to-end):** add `__init__.py` to `tests/` and every subdirectory, then rewrite those three imports package-qualified. Step 2 is mandatory — with `__init__.py` present and the imports left alone you get `ModuleNotFoundError: No module named 'conftest'`. Moving the shared names to `tests/db/helpers.py` instead just recreates the same landmine under a new name, since each test dir stays on `sys.path`. Land it as its own commit, run the whole suite, and expect ruff `I001` on the new first-party import placement.

### Task 13: Auth dependency

**Files:**
- Create: `backend/src/api/auth.py`
- Test: `backend/tests/api/test_auth.py`

- [x] **Step 1:** Failing tests: request without bearer → 401; with valid Supabase JWT (HS256, `SUPABASE_JWT_SECRET`) → dependency yields `(user_id, org_id)`; JWT for user with no org row → 403 (loud, not silent org-less access).
- [x] **Step 2:** Implement, PASS, commit.

**COMPLETE** (`3ea7cd6` + `55e619e` + `1b2db15`). 17 api tests, 141 suite-wide, ruff clean. Both review stages passed; a fix round pinned three security guards that had been correct but untested (the HS256 allowlist, the `exp` requirement, and the upstream `verify_sub` assumption that `except ValueError` leans on). Each new test verified to die under its target mutation and no other.

**What this task does NOT give you:** a correct `org_id`, and nothing more. It cannot pin that routes actually *filter* on it — see Phase 5 constraint #1. Do not read "auth is done" as "tenant isolation is proven".

**Residual risk to remember:** `pyjwt>=2.13.0` is a floor, and a future 3.x could flip the `verify_sub` default that `auth.py`'s narrowed `except ValueError` depends on. `test_token_with_non_string_subject_is_401` is the tripwire; if it ever fails after a dependency bump, widen the except rather than deleting the test.

### Task 13a: Test-package migration (PREREQUISITE — do before Task 13b, its own commit)

Tasks 13b–17 are all api test modules needing the same ~90 lines of fixtures (`org_user`, `mint_token`, `_env`, `call_whoami`, `_dispose_app_engine`). They cannot share them until the bare-name conftest imports are fixed (Phase 5 constraint #2 above). Do this once, here, rather than five times.

- [ ] **Step 1:** `touch tests/__init__.py` and the same in all six subdirs (`api`, `core`, `db`, `evals`, `flows`, plus `fixtures` if it holds Python).
- [ ] **Step 2 (mandatory — Step 1 alone breaks the suite):** rewrite the bare-name imports in **all three** db test files package-qualified — `tests/db/test_schema.py`, `tests/db/test_rls.py`, `tests/db/test_models_roundtrip.py`. With `__init__.py` present and these left alone you get `ModuleNotFoundError: No module named 'conftest'`. Expect ruff `I001` on the new first-party import placement.
- [ ] **Step 3:** move Task 13's api fixtures out of `tests/api/test_auth.py` into a new `tests/api/conftest.py`, including `_dispose_app_engine`. Update `test_auth.py`'s module docstring, which currently explains why the fixtures live inline — that reason is now gone.
- [x] **Step 4:** verify all three invocations: full `pytest` (137 + Task 13's new tests), `pytest tests/db tests/api`, AND `pytest tests/api tests/db`. Both directory orderings must pass — that ordering asymmetry is the whole bug. `ruff check` clean. Commit.

**COMPLETE** (`1deab82` + the engine-dispose fix). `__init__.py` in `tests/` and five subdirs (not `fixtures` — CSVs only); exactly three bare-name imports found and rewritten package-qualified; api fixtures + the test `app`/`/whoami` route moved to `tests/api/conftest.py`. 141 passed, both orderings 40, ruff clean.

- [x] **Step 5 (added after Step 4 — the implementer found a pre-existing bug while proving the migration worked):** `pytest tests/db tests/api` was **passing by ordering luck**, not because it was sound. `tests/db/test_models_roundtrip.py` uses the module-level `src.db.session` engine and never disposed it, leaving pooled connections bound to a closed event loop; the command only survived because the first `tests/api` test collected happens to 401 before touching the database. Five new api modules (Tasks 13b–17) meant any module sorting before `test_auth.py` with a DB-touching first test would have resurrected it. Fixed by mirroring `_dispose_app_engine` into `tests/db/conftest.py` (eager `engine` import is free there — that directory already fails loudly at import without `DATABASE_URL`).
  **The check that discriminates**, because full-suite and both-orderings all passed *before* the fix too: `pytest tests/db/test_models_roundtrip.py "tests/api/test_auth.py::test_valid_token_resolves_user_and_org"` — was `1 failed` (`RuntimeError: Event loop is closed`), now 6 passed. Also re-confirmed the env-free subset is undisturbed: `env -u DATABASE_URL … pytest tests/core tests/flows tests/evals` → 100 passed.
  **Rejected:** a root `tests/conftest.py` for this (now collision-safe post-migration, but autouse across the env-free 100 forces either a `DATABASE_URL` dependency or a cleanup that silently no-ops on `sys.modules` inspection) and any change to `src/db/session.py` (dispose-in-a-test-fixture is what that module's own docstring sanctions).

**Rejected alternatives, so nobody re-litigates them:** (a) a root `tests/conftest.py` holding the fixtures — it loads under the same `conftest` module name and so sits in the exact collision channel this step exists to close, and it was never verified; (b) `asyncio_default_test_loop_scope = "session"` in `pyproject.toml` to delete `_dispose_app_engine` — verified working by the Task 13 quality review, but it trades per-test event-loop isolation across all 137 tests for the removal of 12 lines, and the fixture is already proven correct. Note pytest-asyncio will eventually make the unset `asyncio_default_fixture_loop_scope` default an error, which will force a revisit of (b) on its own schedule.

### Task 13b: Portfolio setup — entities, properties, ownership (foundational reference data)

Without this the delivered MVP cannot be used: every later task assumes orgs/entities/properties exist.

**Files:**
- Create: `backend/src/api/routers/portfolio.py`, `backend/scripts/seed_org.py`
- Test: `backend/tests/api/test_portfolio.py`

- [x] **Step 1:** Failing tests: `POST/GET/PATCH /entities` (name, tax_regime, quarter_basis); `POST/GET/PATCH /properties` (address, finance_cost_classification, epc fields, bedroom_count); `PUT /properties/{id}/ownership` accepts a full list of `{entity_id, percentage}` replacing prior rows atomically — rejects with 422 if percentages don't sum to 100 (loud, names the sum it got); all scoped to caller's org.
- [x] **Step 2:** Implement router + repository, PASS, commit `feat: portfolio setup endpoints`. Ownership and entity mutations write `audit_log` rows (ownership percentages directly change money computation — spec: audit every state change to money data). **AMENDED 2026-07-30 by Step 4a — this enumeration was not exhaustive and was wrongly read as one:** property mutations write `property.created`/`property.updated` too, because spec:70 covers money **or compliance** data and properties carry both (`finance_cost_classification` routes a finance cost across the Section 24 boundary; `epc_rating`/`epc_expiry`/`licensing_flag` are compliance data). Treat this line as "audit every money-or-compliance state change", not as a list. Certificates CRUD (Task 17) likewise writes audit rows.
- [x] **Step 3:** `scripts/seed_org.py`: idempotent CLI (`uv run python scripts/seed_org.py --email m.hoque@gmail.com --org "Hoque Portfolio"`) that creates the org and links the auth user (created beforehand via Supabase Studio or `supabase auth` CLI — Task 19 builds sign-in only, not sign-up), and prints next steps (add entities/properties via the UI or API). Unit-test the idempotency (second run = no duplicates). Commit.

#### Step 4 — FIX ROUND (spec review returned ❌; code shipped at `de29829`/`c4bbc12`/`a2f48ef`, NOT yet ticked)

The router's runtime behaviour was correct at every probed point (28 single-point mutations; 8 of 10 `org_id` filter sites each killed exactly one named guard test). **Every gap below is in test *discriminating power*, not in shipped behaviour** — which is the more dangerous defect here, because Tasks 14–17 will copy this module as the template for "proved it".

- [x] **4a. Property mutations must write `audit_log` rows** (`property.created` / `property.updated`). Spec:70 requires auditing "every state change to money **or compliance** data": `finance_cost_classification` routes a finance cost between the `finance_costs_residential` and `finance_costs_nonresidential` categories (money), and `epc_rating`/`epc_expiry`/`licensing_flag` are compliance data — `epc` is itself a `compliance_certificates` type in the spec's data model. **Step 2 above enumerated two cases and was mistaken for an exhaustive list; the spec governs.** Implementation note: `tests/api/test_portfolio.py:512` reads `(await audit_rows(...))[-1]` under `order by created_at, action`; adding `property.created` breaks that assertion on a `created_at` tie, because the alphabetical secondary sort puts it last. Filter by action rather than relying on ordering.
- [x] **4b. `test_put_ownership_rejects_out_of_range_percentages` is a tautology — replace its params.** All four cases send a **single** `{entity_id, percentage}`, so each fails the sum-to-100 rule regardless of the per-row bound. Verified: dropping `decimal_places=2`, `gt=0`, *or* `le=100` from `portfolio.py:225` leaves all 76 `tests/api` tests green. Measured consequences of the unguarded code, using payloads that sum to exactly 100:
  - `decimal_places=2` dropped + `["33.333","66.667"]` → **HTTP 200**, stored `33.33`/`66.67`. A **silent money mutation** — `numeric(5,2)` rounds it away with no error anywhere. This is the worst of the three.
  - `gt=0` dropped + `["0.00","100.00"]` → `CheckViolationError` → unhandled **500**, precisely the trap the docstring claims to prevent.
  - `le=100` is **redundant by construction, not untested**: with `gt=0` in force and the sum pinned to exactly 100, no single share can exceed 100. A discriminating test cannot exist — stated here so nobody "fixes" it.

  Replacement params (each verified to pass against shipped code and fail when its guard is dropped):
  ```python
  @pytest.mark.parametrize("percentages", [
      ["0.00", "100.00"],            # pins gt=0
      ["-10.00", "55.00", "55.00"],  # pins gt=0 for negatives
      ["33.333", "66.667"],          # pins decimal_places=2 (the silent-rounding guard)
  ])
  ```
  Same tautology, lower severity, in `test_rejected_ownership_payloads_leave_the_prior_set_untouched:755` (uses `[{other: "0.00"}]`, also sums to 0) and `test_put_ownership_rejects_an_empty_list:683` (dropping `Body(min_length=1)` keeps all 76 green since `[]` sums to 0). For the latter, assert `"too_short" in resp.text` to pin which rule fires.
- [x] **4c. Correct a false factual claim in two places.** `tests/api/test_portfolio.py:515` (test + docstring) and the comment at `portfolio.py:25` both state that `33.33 + 33.33 + 33.34` is `99.99999999999999` in binary floating point. **It is exactly `100.0`** — controller-verified. The test therefore does not discriminate the hazard it is named for. Use the verified counterexample `0.01 + 64.04 + 35.95` → float `100.00000000000001`, `Decimal` `100.00`. (The *code* is correct regardless: pydantic 2.12.5 parses JSON numbers to `Decimal` straight from the raw JSON text with no float intermediate, so no float can enter via the HTTP path.) **Applied 2026-07-30:** a *third* false claim was found in the same shipped docstring during the fix — `portfolio.py:31-36` stated "Property mutations do not [write audit rows]", which 4a makes false; rewritten alongside. Also note the replacement test's discriminator is the **200**, not the sum: via a float intermediate `Decimal(0.01)` has ~19 decimal places, so `decimal_places=2` would 422 it.
- [x] **4d. Assert the cross-org 404s are textually identical.** Entity/property cross-org 404s are identical *by construction* — `_not_found` at `portfolio.py:263-277` is the single source — but no test asserts it, and it is the one unverified cell in the isolation story. The ownership 422 equivalent is already pinned at `:879`.

**Concurrency — DECIDED: pin and defer, not a spec violation.** Concurrent ownership `PUT`s on one property are not serialized, so two overlapping replacements under READ COMMITTED can leave a set summing to 200 (disjoint entities) or trip `uq_property_ownership_property_entity`. Spec §Error handling requires only that percentages sum to 100 and says nothing about serialization, and `0001_core.sql:228-234` documents that this invariant is API-validated *because* ownership is edited row-by-row against transiently-invalid totals. Decisively, **both race outcomes are loud, never silently wrong**: overlapping sets raise a unique violation, and a 200%-summing set is refused by `src/core/splits.py:126` (`ownership percentages must sum to 100, got {total}`) at the first apportionment. Money is never silently corrupted. **The one-line fix when it is wanted:** `.with_for_update()` on the property lookup at `portfolio.py:541-543` — house-sanctioned, since Task 18 already mandates `FOR UPDATE SKIP LOCKED`.

**Recorded so no future reviewer chases them:** the ownership `existing` select (`portfolio.py:591`) and the subsequent `delete` (`:600`) are the 2 of 10 `org_id` filter sites whose removal kills nothing. They are redundant defence-in-depth — the property is already proven in-org above them — so no test *can* catch them. Keep them; they cost nothing.

#### Step 5 — nullable property fields must be clearable (DECIDED by Mahmud 2026-07-30)

`_PatchBody`'s null-rejection (`portfolio.py:100-116`) currently means `epc_rating`, `epc_expiry`, `address_line2`, `prs_registration_number` and `prs_registered_at` can be corrected but **never cleared** through the API. **Decision: an explicit `null` clears the field; omitting the key still leaves it untouched** — JSON Merge Patch semantics (RFC 7386). Rationale: a mis-entered EPC expiry is a compliance problem and must be removable, not merely overwritable; the current 422 strands a wrong value short of direct database access.

- [x] **5a:** `_PatchBody` must distinguish three states per field — key absent, key present and `null`, key present with a value. `Optional[T]` alone cannot express this. **The mechanism is already present and correct**: `model_fields_set` (`portfolio.py:117,119`) makes exactly that distinction today, and both handlers already use `model_dump(exclude_unset=True)`, which is RFC 7386 merge semantics. So the change is localized — delete the null-rejection branch from `_reject_empty_and_null_updates`, keep the empty-patch branch, rename it.
  **The real wart is nullability information, not state detection.** Every field is uniformly typed `X | None`, so `EntityUpdate.name` and `EntityUpdate.prs_registration_number` are indistinguishable to the model — yet once null means "clear", `{"name": null}` must still 422 (NOT NULL in Postgres) while `{"prs_registration_number": null}` must succeed. Introduce that knowledge explicitly: a `ClassVar[frozenset[str]]` per model naming the non-nullable fields (`name`, `tax_regime`, `quarter_basis`, `address_line1`, `city`, `postcode`, `country`, `finance_cost_classification`, `licensing_flag`). Prefer the `ClassVar` over re-typing — greppable, and it reads as a statement about the schema rather than a pydantic trick.
- [x] **5b:** Test all three states for **each** of the **six** clearable fields: absent → unchanged, `null` → cleared, value → set. A single field tested three ways is not enough — the bug this guards against is per-field. **The six are `epc_rating`, `epc_expiry`, `address_line2`, `prs_registration_number`, `prs_registered_at`, and `bedroom_count`.** `bedroom_count` was missed when this step was first written (nullable at `0001_core.sql:213`, `models.py:174`, already `int | None` at `portfolio.py:195`) — precisely the per-field omission 5b exists to prevent, which is why it is named here rather than left to "the nullable ones".
- [x] **5c:** Clearing writes a `property.updated` `audit_log` row like any other mutation — these are compliance fields, same spec:70 clause that drove 4a. The `before`/`after` JSON must show the cleared field going to `null`, not omit it.
- [x] **5d:** Applies to `PATCH /entities` too if it has nullable fields (`prs_registration_number`, `prs_registered_at`) — check, don't assume the two bodies are symmetric.

**Step 5 outcome (implemented 2026-08-03, TDD).** 236 tests (was 208; +29 new, −1 superseded), ruff clean, both directory orderings 136, env-free subset 100. 5a's prediction held exactly: `model_fields_set` and `exclude_unset=True` were already right, so the whole behaviour change was narrowing one validator condition to `and field in self._NOT_NULLABLE`, plus a `ClassVar[frozenset[str]]` on each of `EntityUpdate` (3 names) and `PropertyUpdate` (6 names). Note the file moved in Step 6 — it is `src/api/routers/portfolio.py` now, so 5a's line references are stale.

RED was watched: the 8 clearing tests failed with the old blanket 422 before any code changed. **Three mutations run, each killing exactly its intended set and nothing else:**
- Emptying `PropertyUpdate._NOT_NULLABLE` fails exactly the 6 `test_patch_property_cannot_null_a_not_null_column` cases — and fails them with an **`IntegrityError`, i.e. a 500**. That is the concrete proof the set is load-bearing rather than decorative: without it a null on a NOT NULL column stops being a 422 and becomes a server error.
- Restoring the blanket refusal (dropping the `_NOT_NULLABLE` membership test) fails exactly the 8 clearing tests.
- Dropping `exclude_unset=True` from both handlers fails 24, including all six `..._leaves_a_nullable_field_alone_when_omitted` cases — the absent-key half of RFC 7386, which is only now load-bearing.

**One test removed, deliberately:** `test_patch_entity_cannot_null_a_field` sent `{"name": null}` and asserted 422. `test_patch_entity_cannot_null_a_not_null_column[name]` asserts the same thing, plus `tax_regime` and `quarter_basis`, plus that the 422 names the field. Keeping both would have left a duplicate whose docstring ("MVP has no 'clear this field' operation") Step 5 made false. Coverage strictly increased; a comment marks the spot.

**Step 5 REVIEW (independent agent, 2026-08-03): ⚠️ APPROVED WITH NITS — no shipped bug.** `_NOT_NULLABLE` verified correct and complete on both models against the live catalog; all three recorded mutations reproduced exactly; the deleted test confirmed strictly superseded; every invariant pinned by a mutation-killing test. All findings were about what happens when Tasks 14–17 copy this. Fixed in the same round:

- **The two hand-maintained lists could drift, and the failure was invisible.** `_NOT_NULLABLE` and the tests' `NOT_NULL_*_FIELDS` were independent copies, and nothing checked either against the schema or even that a name was a real field. The reviewer's decisive measurement: misspell `postcode` as `post_code` **in both lists at once** — the realistic copy-paste error, since they are edited together — and the suite is **fully green** while `PATCH {"postcode": null}` is a live 500. The test passes for the wrong reason, because `extra="forbid"` turns the unknown key into a 422 that still contains the field name. **Closed by `test_not_nullable_is_exactly_what_the_schema_says`**, which derives the expected set from `Model.__table__.columns` and so catches a missing name, a wrongly-included nullable one, and a name that is not a field at all, in one equality. Verified: that same double-typo mutation now fails exactly this test and nothing else.
- **A comment promised a guard it did not provide.** The base-class note claimed an empty `_NOT_NULLABLE` makes a forgetful subclass "get database errors in its own tests" — true only if those tests exist, and the realistic failure is the joint omission of both. Rewritten to say the empty default is a default, not a guard, and to name the test that is.
- **"It has a default, so null is safe" is false**, and three in-scope columns invite the mistake (`transactions.status`, `imports.status`, `documents.metadata`). An explicit `NULL` in an `UPDATE` does **not** fall back to the column default. Measured on `properties.country` (NOT NULL `default 'GB'`): dropping it from the set fails exactly `[country]` with an `IntegrityError`. Now stated in the `_PatchBody` docstring.

**CARRY INTO TASKS 14–17 — the tables where copying this bites** (measured by the reviewer against the live catalog):

| table | NOT NULL (excl. infra) | nullable |
|---|---|---|
| `compliance_certificates` | `property_id`, `type`, **`expiry_date`** | `issue_date`, `certificate_ref`, `document_id` |
| `imports` | `file_path`, `source_bank`, `status` | `entity_id`, `period_start`, `period_end`, `error_detail` |
| `transactions` | `entity_id`, `date`, **`amount`**, `direction`, `description`, `status` | `import_id`, `property_id`, `hmrc_category`, `confidence`, `proposed_by` |
| `documents` | `storage_path`, `kind`, `metadata` | — |

Two specific traps: **`compliance_certificates.issue_date` is nullable while `expiry_date` is NOT NULL** — the asymmetric pair where intuition fails, since both look like optional dates; and **`transactions.amount` is NOT NULL money**, so omitting it from `_NOT_NULLABLE` is a 500 on a money field. Every new PATCH body must add its `(body, table)` pair to `test_not_nullable_is_exactly_what_the_schema_says`'s parametrize — that is the one line that makes all of the above unnecessary to remember.

**Downstream consequence for Task 22** (portfolio settings screen): the Flutter form must send `null` when the user empties a field, not drop the key. Dropping it will silently do nothing, and the UI will appear to lose the edit on reload.

**Also untested-but-shipped, decide separately:** `seed_org.py:104-108`'s multi-org-ambiguity `RuntimeError`.

#### Step 6 — template hardening (DO BEFORE TASK 14; quality review, stage 2)

Stage 2 returned ⚠️ APPROVED WITH NITS. Nothing blocks ticking 13b, but these must land before Tasks 14–17 copy this module, because copying happens once and retrofitting happens five times.

- [x] **6a (the false directive — controller-verified false, and MY propagation error).** `portfolio.py:236-237` reads "**No test can discriminate `le=100`** -- do not spend time trying to write one." **That is false.** A single share of `100.01` returns `422` with `{"type":"less_than_equal","msg":"Input should be less than or equal to 100"}`; with `le=100` removed, pydantic accepts `Decimal("100.01")` (5 digits, 2dp — `max_digits`/`decimal_places` don't catch it) and the handler's sum rule fires instead with `"must sum to exactly 100, got 100.01"`. Both 422, **different bodies**, so `assert "less_than_equal" in resp.text` is a mutation-killing test. **The file already blesses this exact technique 550 lines away** — `test_portfolio.py:794` asserts `"too_short" in resp.text` for the same purpose. It contains its own counterexample.
  Keep the surrounding true claims (`le=100` is redundant at the level of which payloads are *accepted*; no share reaches 100.01 without the sum already exceeding 100). Delete the false jump from "acceptance-set identical" to "no test can discriminate", and add the discriminating test — it needs its own small test, not the existing parametrize, whose payloads must sum to 100. Also update `test_portfolio.py:761-764`, which is true as scoped but cites the false claim as its authority.
  **Provenance, recorded because the pattern is the point:** this originated with the stage-1 spec reviewer, I copied it into the plan *and* into the fix-round brief without checking, and the implementer faithfully wrote it into a docstring. Seventh confident-wrong claim in this project; the first to become a *directive telling the next reader not to verify*. See the memory note `verify-subagent-claims-before-pinning`.
- [x] **6b (the org-filtering seam — extract before it is copied twenty times).** Create `backend/src/api/scoping.py` (NOT in `portfolio.py` — Tasks 14–17 importing a helper out of a sibling router is the wrong dependency direction) with `get_owned_or_404(session, model, resource_id, auth, *, what)`. Two non-negotiables: it takes **`auth`**, never a bare `org_id`, so it cannot be called with a wrong or missing org — the parameter that must be right is the one you cannot omit; and it raises the 404 itself, keeping the 404-vs-403 decision and message shape in one place across six routers. The models have no shared base carrying `id`/`org_id` (`_uuid_pk()`/`_org_id_fk()` are column factories, not a mixin), so add a small `OrgScoped` Protocol declaring both columns to type the `model` parameter.
  **Leave explicit** (each is shaped differently and appears once or twice; reading them individually is the right cost): the list selects, the `delete()`, the ownership existence select, and the `Entity.id.in_()` set-membership filter. The point is not "helper vs visible" in general — it is that lookup-or-404 is identical four times here and roughly twenty times across 14–17, and that is where a silent copy-paste omission actually happens.
- [x] **6c:** Extend the 404-identity assertion beyond GET. `test_a_cross_org_404_is_identical_to_a_nonexistent_one` pins the oracle property for GET only; `test_org_a_cannot_patch_org_bs_entity`/`_property` and `test_org_a_cannot_set_ownership_on_org_bs_property` assert status without body-identity, same `_not_found` source and same existence-oracle channel. Extend the existing parametrize to `(kind, method)`.
- [x] **6d:** Add the missing `seed_org.py:104-108` multi-org test. `orgs.name` has **no** unique constraint (`0001_core.sql:150-155`), so two same-named orgs are insertable and the guard is genuinely reachable — verified. The argument is asymmetry: the module's other two `RuntimeError`s each got a dedicated test, and this one guards the script's *idempotency key*.
- [x] **6e:** `portfolio.py:593-597` re-implements `src/core/splits.py:105-127` (non-empty, each share > 0, sum exactly 100). **Do not** make the router call `_validate_shares` — the error shapes genuinely differ (the router must name duplicates and unusable entities, must produce 422s, and must validate before writing). Instead add a `:seealso:` both ways plus one guard test asserting that a share map the API accepted is acceptable to `split_amount`, so drift is caught without coupling the error paths.
- [x] **6f (prose accuracy, given this file's claim density is its main inheritance risk):** `portfolio.py:8-9`'s superuser claim is true *by configuration*, not by code — `session.py` reads whatever `DATABASE_URL` says — so add the clause "as `DATABASE_URL` is configured". `portfolio.py:103-106` goes stale the moment Step 5 lands ("an operation the MVP doesn't offer" — it will); rewrite deliberately rather than leaving it. Add a `:seealso:` to `0002_rls.sql`, where `fk_property_ownership_entity_org` actually lives (the module cites only `0001_core.sql`).
- [x] **6g — DECIDED: DEFER, and do not relitigate in Task 14.** The judgement call was whether to introduce `SessionDep = Annotated[AsyncSession, Depends(get_session)]` now. Deferred, for four reasons found while implementing rather than assumed:
  1. **The headline benefit is unreachable inside this round's fences.** The stated cost is two pooled connections and two transactions per request. `require_auth` (`auth.py:158`) opens one of them. A routers-only `SessionDep` leaves the count at **two, not one** — auth's session plus the request's. The connection collapse *requires* `auth.py` to share the request session, and `auth.py` was off-limits. So doing 6g inside the fences buys none of the thing 6g is for.
  2. **`get_session` has nowhere to live.** `src/db/session.py` — where `async_session_factory` is and where a `get_session` generator belongs — was explicitly fenced. Putting the dependency in `src/api/deps.py` or `src/api/scoping.py` instead splits session lifecycle across two layers to buy benefit (1) says isn't there.
  3. **Sharing a session into `require_auth` has a real cost that must be measured, not assumed.** Today an unauthenticated request costs **zero** pooled connections: `require_auth` raises `_unauthenticated("Missing bearer token.")` at `auth.py:128-129`, *before* its `async with`. Injecting a session makes every request check one out, including every scan and every 401. That is a regression at the edge, paid for a saving in the middle. Whoever does this must measure both.
  4. **What remains inside the fences is one indentation level and an override seam with no consumer.** `tests/api/conftest.py` is built around the real stack by design — real Postgres, real Supabase Auth users, real JWTs — and no test wants to substitute a session. An override seam nothing overrides is not a reason.
  **Why "copying happens once, retrofitting happens five times" does not carry here the way it does for 6b.** 6b's case is that a *silently omitted* `org_id` filter has no symptom — the retrofit risk is a leak nobody sees. `async with async_session_factory() as session:` is the opposite: uniform, greppable, correctness-neutral, and a later sweep is a dedent with 200+ tests behind it. Tasks 14–17 copying the current shape costs a mechanical retrofit, not a hidden defect.
  **Therefore, for whoever picks this up:** do it as one explicit step that touches `src/db/session.py` (add `get_session`), `src/api/auth.py` (take `SessionDep` in `require_auth`) and all six routers together — that is the only version that delivers the connection collapse — and pin the unauthenticated-request cost of (3) with a test before and after. Task 14 should copy the current `async with` shape without re-opening this.

**Step 6 outcome (implemented, `iteration-1-mvp`).** 6a–6f done, 6g decided-and-deferred. `src/api/scoping.py` is new and carries `get_owned_or_404`, the `OrgScoped` Protocol, and `not_found` — which **moved out of `portfolio.py`**, because a helper that raises the 404 itself cannot leave the 404's wording behind in a sibling router (that would be the same string in two files, drifting silently while 6c's body-identity tests still passed). Routers now import `not_found` from `scoping`. Three mutations were run and all three have teeth: removing `le=100` fails only the new `test_put_ownership_over_100_is_refused_by_the_field_bound`, on the `less_than_equal` assertion rather than the status code; making `get_owned_or_404` ignore `auth.org_id` fails **eight** tests (the four `test_org_a_cannot_read/patch_*` plus all four `(kind, method)` cases of the identity test) and correctly does **not** fail `test_org_a_cannot_set_ownership_on_org_bs_property`, whose 404 comes from the ownership handler's own existence probe; and giving the cross-org case a distinguishable 404 fails exactly the four identity cases while every status-only test survives — which is the concrete demonstration that 6c was needed. The ownership probe's own 404 was probed separately and also has teeth. Two prose claims that 6b invalidated and the brief did not list were also fixed: `portfolio.py`'s "**every** query in this module filters `org_id` explicitly" and `test_portfolio.py`'s "the explicit `org_id` filter in each statement" — four of those filters now live in `scoping.py`.

**Norm to carry into Tasks 14–17, and the reason this project keeps tripping:** this module's comment density is a strength *because* nearly every claim is pinned to a test — 27 verified true against 1 false. But density invites prose asserting things nobody checked. **The rule: if a comment asserts a behaviour, name the test that enforces it; if you cannot name one, say you did not check.** The 4c docstring is the model — it states the mechanism, then says "that is a property of the parser rather than of this module, so it is pinned by test rather than assumed", and names the test.

**Copy without hesitation into 14–17:** the route-table auth parametrization (`test_portfolio.py:159-185`, the only guard that catches an *added* route shipped without `auth: CurrentAuth`), the assert-which-rule-refused test style, the row-id-comparison atomicity check, `scoping.get_owned_or_404`/`scoping.not_found` and `portfolio._unprocessable`/`_audit`, and `conftest.py`'s `make_org_user`/`make_auth_user` factories.

### Task 14: Imports endpoint

**Files:**
- Create: `backend/src/api/routers/imports.py`
- Test: `backend/tests/api/test_imports.py`

- [x] **Step 0 (from Task 6 review — spec §Security "storage buckets namespaced per org"):** creating the statements bucket requires `storage.objects` RLS policies enforcing the `{org_id}/` path prefix (the `documents` table cannot enforce path isolation — `storage_path` is free text). Add policies restricting authenticated access to paths starting with their org id; service connection used by the API bypasses. Test: authenticated user cannot read an object under another org's prefix.

  **Baseline measured on the live local stack 2026-07-29 (controller prep, ahead of the task):** there are **zero buckets** and **zero `storage.objects` policies**, but RLS **is** already enabled on both `storage.objects` and `storage.buckets`. So storage is currently deny-by-default for authenticated users — a safe starting point — and this step must create both the bucket and its policies. Prefer creating the bucket **in a migration** (reproducible; `supabase start` replays it) over a one-off script.

  **Mirror the existing helper:** `public.current_org_id()` (`supabase/migrations/0002_rls.sql:35`) is `security definer`, `stable`, `set search_path = ''`, body `select org_id from public.users where id = auth.uid()`. All thirteen table policies use the uniform shape `org_id = (select public.current_org_id())`; storage policies should match it.

  **VERIFIED TRAP — do not write the natural form.** `storage.foldername('<uuid>/2026/stmt.csv')` returns `{<uuid>,2026}` (folder segments, filename stripped). The obvious predicate

  ```sql
  (storage.foldername(name))[1]::uuid = (select public.current_org_id())   -- WRONG
  ```

  **errors** on any object whose first segment is not a UUID (`ERROR: invalid input syntax for type uuid: "personal"`). Because this is a policy predicate, one mis-pathed object breaks access evaluation for every scan of the bucket, not just for that object. Compare as text instead:

  ```sql
  (storage.foldername(name))[1] = (select public.current_org_id())::text   -- safe
  ```

  Both forms were measured against the live database, not reasoned about.
- [x] **Step 1:** Failing tests: `POST /imports` (multipart CSV + entity_id) stores file to Supabase Storage (`statements/{org_id}/...`), parses; on success creates `imports` row (status `parsed`) + `transactions` rows (status `unclassified`) + `job_queue` row (`type=categorise`); on `StatementParseError` creates `imports` row status `failed` with row-level error detail in response and DB — and NO transaction rows; `GET /imports` lists with status.
- [x] **Step 1b (sign convention — from Task 10 review):** the parser emits SIGNED amounts (negative = money out); the DB stores MAGNITUDE + `direction`. Task 14 MUST store `amount = abs(ParsedLine.amount)` and `direction = 'out' if line.amount < 0 else 'in'`. Failing test: a -84.99 parsed line lands as amount 84.99 / direction 'out'. Manual claims (null import_id, e.g. use_of_home_allowance) get `direction = 'out'`.
- [x] **Step 2:** Implement (repository layer in `src/db/`), PASS, commit.


**Task 14 outcome (2026-08-04).** 270 tests (was 255), both orderings 154, env-free 115, ruff clean.

- **Storage** is `src/api/storage.py`, one function `upload_statement(org_id, filename, content)`. It takes an org id and a *filename*, never a path, and reduces the filename to a sanitised leaf — so a caller cannot ask for a path and therefore cannot ask for the wrong one. No Supabase SDK: the upload is one authenticated POST and `httpx` was already a dependency; a client can be swapped in behind the function without anything above it changing.
- **A parse failure is recorded, not rolled back.** 201 with `status='failed'` and the offending row number in `error_detail`; no `transactions`, no `job_queue` row. A *request* problem (unknown `source_bank`, another org's `entity_id`) is still a 4xx, because in that case no file was ever accepted — and the bank is checked **before** upload so a file that could never be read leaves no orphaned object.
- **DEVIATION from Step 2, deliberate:** the plan said "repository layer in `src/db/`". Queries live in the router instead, matching `portfolio.py`, which Tasks 15–17 also copy. Introducing a second persistence idiom for one endpoint would cost more in inconsistency than it buys; if a repository layer is wanted it should be a deliberate sweep across all routers, not a one-off here.
- **Five mutations, each killing exactly its target and nothing else:** inverting `direction`, dropping `abs()`, removing the entity org check, removing the `list_imports` org filter (each → one named test), and skipping filename sanitising (→ the two escape cases).
- Two rules of this repo's own were broken in the first draft and fixed before commit: a broad `except Exception` (narrowed to `ParserError`, since catching everything would report a bug in this module as the user's file being at fault) and reaching into the parser's private `_FORMATS` (now the public `is_registered_bank`).
- `call`/`as_user` moved from `test_portfolio.py` to `tests/api/conftest.py` rather than copied — two divergent copies of "how this suite talks to the app" is the drift the shared conftest exists to prevent.
### Task 15: Review & confirm endpoints

**Files:**
- Create: `backend/src/api/routers/transactions.py`
- Test: `backend/tests/api/test_transactions.py`

- [x] **Step 1:** Failing tests: `GET /transactions?import_id=&status=` returns lines with proposal fields; `POST /transactions/{id}/confirm` body `{hmrc_category, property_id}` — sets status `confirmed`, writes `audit_log` row (actor=user, before/after JSON); confirming with a property whose ownership doesn't sum to 100 → 422 with explanation; `POST /transactions/{id}/exclude` for personal lines (writes an `audit_log` row, same as confirm); bulk `POST /transactions/confirm-batch` (all-or-nothing transaction).
- [x] **Step 2:** Implement, PASS, commit.


**Task 15 outcome (2026-08-04).** 286 tests (was 270), both orderings 170, env-free 115, ruff clean.

- **The ownership guard runs the real apportionment**, `split_amount(txn.amount, shares)`, rather than re-checking "sums to 100" by hand. So "can this be confirmed?" and "can this be exported?" are the same question by construction and cannot drift. It is reachable in practice precisely because `0001_core.sql:228-234` deliberately leaves the sum rule out of the database: a 50%-only ownership set is insertable, and the test writes one directly to prove the guard is not theoretical.
- **Single and batch confirm share `_apply_confirm`.** The failure mode otherwise is a user who hits the guard confirming one line, then confirms the same lines in bulk and slips past a weaker batch path.
- **Batch is all-or-nothing** — one session, one commit at the end. Mutating it to commit per item fails the atomicity tests.
- `_audit` moved from `portfolio.py` to **`src/api/audit.py`** and is now shared. Copying it, as Step 6's note suggested, would have produced two definitions of what an audit row looks like; Step 4a already exists because a rule about auditing was restated once and read as exhaustive. Task 17 imports it too.
- `call`/`as_user` in `tests/api/conftest.py` gained `params` support for `?status=`.
- **Four mutations, each killing its target:** skipping the ownership guard (3 tests), committing per batch item (2), ignoring the transaction org filter (1), dropping the exclude audit row (1).
- **Naming note:** the query parameter is `?status=` but the handler argument is `status_filter`, because `status` shadows the imported `fastapi.status` module inside that scope. The alias is what the review screen sends, so the alias is the part that had to be right.
### Task 16: Quarterly export endpoint

**Files:**
- Create: `backend/src/api/routers/exports.py`, `backend/src/core/export_pack.py`
- Test: `backend/tests/api/test_exports.py`, `backend/tests/core/test_export_pack.py`

- [x] **Step 1 (BLOCKER CHECK — spec open question 1): DONE 2026-07-29, ahead of the task.** Verified against the HMRC developer hub (Property Business API v6.0, MTD ITSA end-to-end service guide, `income-tax-mtd-changelog` SA105 mapping): HMRC treats all of one owner's UK rental property as a **single UK property business**; quarterly figures pool across properties. **Per-entity aggregation confirmed — build as designed**, per-property detail as a supplementary sheet. Cumulative-YTD submission also confirmed (2025-26 onwards), matching `core/quarters.py`. Full answer + sourcing caveats + two flagged enum gaps recorded in spec open question 1. No contradiction with the design; nothing to surface to Mahmud as a blocker.
- [x] **Step 2:** Failing core tests: `build_export_pack(entity, tax_year, quarter, txns, ownerships)` → refuses (`ExportBlockedError` listing blocker transaction ids) if any txn in period is `unclassified`/`proposed`; produces CSV (one row per category, cumulative YTD) + per-property supplementary CSV; Ltd entities → `SimplePnlPack` instead (no mtd_quarters row). **Sign rule (from Task 10 review): when building `TxnForTotals` from a DB row, NEVER copy `amount` straight through — apply `signed = magnitude if (category in INCOME_CATEGORIES) == (direction == 'in') else -magnitude`. Failing test: a contractor-refund row (repairs, direction 'in') enters totals negative. Also needed: int-quarter ↔ `'Q1'..'Q4'` enum mapping helper for the mtd_quarters key.**
- [x] **Step 2b (spec §Error handling — decrease guard; AMENDED after Task 10 review found the original spec self-contradictory):** a legitimate in-window refund (e.g. contractor refund of a Q1 repair landing in Q2) can lawfully make a cumulative category total DECREASE — the guard must not block it. The discriminator is **recomputation of the earlier quarter from current data**: on export of quarter N, recompute quarter N-1's totals (and earlier exported quarters) from today's confirmed transactions and compare against their latest stored `mtd_quarters` rows. If stored == recomputed for all earlier quarters, history is intact and any decrease in quarter N is refund-legitimate → allowed (new version row). If any earlier quarter's recomputation differs from what was exported, history was deleted/edited after export → `CumulativeDecreaseError` naming the quarter, category, stored total, and recomputed total; resolution is a human decision, not an override flag. Failing tests: (a) refund-decrease with intact history exports fine, (b) deleted-transaction history change raises, (c) legitimate re-export same quarter higher totals → new version row.
- [x] **Step 3:** API tests: `POST /exports/quarter` creates/increments `mtd_quarters` version row, stores generated files as `documents`, returns download refs; PDF rendering = simple HTML→PDF (weasyprint) of the same numbers (snapshot test on HTML, not PDF bytes).
- [x] **Step 4:** Implement, PASS, commit.

### Task 17: Certificates CRUD

**Files:**
- Create: `backend/src/api/routers/certificates.py`
- Test: `backend/tests/api/test_certificates.py`

- [x] **Step 1:** Failing tests: CRUD for `compliance_certificates` incl. document upload ref; derived `status` in responses: `expired` (past), `expiring` (≤60 days), `valid`; list endpoint groups by property.
- [x] **Step 2:** Implement, PASS, commit.

---

## Phase 6 — Worker

### Task 18: Job queue poller

**Files:**
- Create: `backend/src/worker/main.py`, `backend/src/worker/jobs.py`
- Test: `backend/tests/worker/test_poller.py`

- [x] **Step 0 (from Task 11 review note):** the categorise handler must not enqueue/run the flow for an import with ZERO unclassified transactions — `CategoriseStatementFlow` accepts empty lines and would make a pointless LLM call; guard in the worker (skip + mark import parsed/complete) with a test. Worker responsibilities the flow deliberately delegates (from Task 11 review): the property list passed to the flow MUST be org-scoped by construction (query filtered by the import's org_id), few-shot selection = up to 50 MOST RECENT confirmed transactions for that org (the flow errors above 50 rather than truncating), and the worker env must set `CREWAI_TELEMETRY_OPT_OUT=true` / `OTEL_SDK_DISABLED=true` (bank-data privacy — see .env.example).
- [x] **Step 1:** Failing tests: poller claims queued jobs with `FOR UPDATE SKIP LOCKED`, marks `running`→`done`; a job whose handler raises marks `failed` with the exception string stored and NO retry loop (fail loudly, visible in UI); `categorise` handler: loads unclassified transactions for the import, runs `CategoriseStatementFlow` (mocked in test), writes proposals (status `proposed`, confidence, `proposed_by`=job id) + audit rows. **Failed-job visibility:** a failed `categorise` job also sets its import's status to `categorisation_failed` (add enum value in migration if not present), so `GET /imports` — and the imports screen (Task 20) — surface it; a stuck import must never look merely "pending".
- [x] **Step 2:** Implement (`asyncio` loop, `poll_interval=2s`, graceful SIGTERM), PASS, commit.
- [x] **Step 3:** Add `make dev` (or `justfile`): `supabase start`, API `uvicorn`, worker, `flutter run -d chrome`. Commit.

---

## Phase 7 — Flutter UI

**All UI tasks:** load the `impeccable` skill before layout/polish decisions; use theme tokens only (no inline colours/durations); every list/state change animates via `flutter_animate` with `Motion` tokens; widget tests per screen.

### Task 19: Auth + app shell

- Files: `frontend/lib/features/auth/`, `frontend/lib/app.dart`, `frontend/lib/main.dart`
- [x] **(Auth decision 2026-07-29: Google OAuth only — no email/password UI.)** Login screen is a single "Continue with Google" button calling `supabase.auth.signInWithOAuth(OAuthProvider.google)`; `[auth.external.google]` enabled in `supabase/config.toml` reading `GOOGLE_OAUTH_CLIENT_ID`/`GOOGLE_OAUTH_CLIENT_SECRET` from env (add to `.env.example`). **Input needed from Mahmud: Google Cloud OAuth client credentials** (redirect URI `http://127.0.0.1:54321/auth/v1/callback` for local). `go_router` guarded routes; app shell with nav rail (Dashboard, Imports, Review, Certificates); widget tests: unauthenticated → login screen shows the Google button (OAuth flow itself mocked — no live Google in tests). RLS-test plumbing keeps service-side password-grant users; that's internal only. Commit.

### Task 20: Upload & imports screen

- Files: `frontend/lib/features/upload/`
- [x] File-pick CSV → `POST /imports`; list imports with status chips; **failed imports show the row-level error prominently** (spec: import failure UX matters more than format coverage); staggered list entrance (`flutter_animate`, `Motion.standard`). Widget test with mocked API client. Commit.

### Task 21: Review screen (the core UX)

- Files: `frontend/lib/features/review/`
- [x] Transaction list for an import: category chip + property + confidence; lines below confidence threshold flagged "needs attention" (amber, subtle pulse once — not looping); tap → edit category/property (bottom sheet, enum-driven); multi-select → confirm batch; confirmed rows animate to settled state. Widget tests: proposal renders, low-confidence flag at <0.8, batch confirm calls API once. Commit.

### Task 22: Certificates + dashboard

- Files: `frontend/lib/features/certificates/`, `frontend/lib/features/dashboard/`, `frontend/lib/features/portfolio/`
- [x] **Prerequisite (from Task 4 review):** add RAG/status colours as a `ThemeExtension<StatusColors>` registered per-brightness inside `AppTheme._build()` — NOT bare `Color` constants in tokens.dart (bare constants have no light/dark variant). Screens read `Theme.of(context).extension<StatusColors>()`.
- [x] Certificates: per-property table, add/edit form, expiry status colours (RAG via theme tokens). Dashboard: cards — unreviewed transaction count, next quarterly-update deadline (**7 Aug / 7 Nov / 7 Feb / 7 May** — the 7th of the month after quarter-end; quarter-ends are 5 Jul/5 Oct/5 Jan/5 Apr), expiring certificates; export button → `POST /exports/quarter` → download. Deadline computation lives in `backend/src/core/quarters.py` (`next_update_deadline(today) -> date`) with unit tests pinning all four statutory dates — the frontend renders it, never computes it. Widget tests. Commit.
- [x] Portfolio settings screen (uses Task 13b endpoints): list/add/edit entities and properties; ownership editor with live sum-to-100 validation mirroring the API rule. Widget test: ownership form blocks save at ≠100%. Commit.

---

## Phase 8 — End-to-end & wrap-up

### Task 23: E2E smoke

- Test: `backend/tests/e2e/test_smoke.py`
- [x] Against local supabase + API + worker (real flow with **mocked LLM call only**): upload fixture CSV → poll until proposals exist → confirm all → export quarter → assert CSV totals match hand-computed fixture numbers penny-exact. Commit. **DONE 2026-08-04.** Only `crewai.Agent` is replaced, so the flow's validation, prompt and output-contract check all run. The waiter blocks on a *terminal* job state (`done` **or** `failed`) rather than on proposals appearing, so a handler failure reports `job_queue.error` instead of a bare timeout. Figures are read back through `GET /documents/{id}/download` → signed URL → real storage fetch, not out of the pack object. Two mutations proved the numbers load-bearing (reversed remainder ranking; category dropped from `signed_amount`) — see `docs/planning/progress.md`.
- **Deliberately not here:** the "no external origin is requested" assertion. A backend pytest cannot observe the Flutter web bundle; it needs a `make` target grepping `frontend/build/web` after a build. Carried into Task 24 rather than replaced with something weaker.

### Task 24: Docs & handoff

- [ ] `README.md`: setup (supabase start, .env, make dev), architecture sketch, how to add a bank format, how to run the golden eval. ~~Update spec open-question 1 with the verified HMRC answer~~ — already done 2026-07-29 (commit `f264112`); just cross-check it still reads correctly. Commit.
- [ ] **CI requirement (from Task 13 quality review):** there is no `.github/` in this repo yet, and `src/api/auth.py` + `src/db/models.py` both raise at *import* when `DATABASE_URL` / `SUPABASE_JWT_SECRET` are unset. A collection error aborts the entire run, so a bare `pytest` without `--env-file` executes **zero** tests — including the ~100 in `tests/core`, `tests/flows`, `tests/evals` that need no environment at all. Whoever adds CI must therefore either start the Supabase stack before the suite, or split it into an env-free fast job (`pytest tests/core tests/flows tests/evals` — verified 100 passed in isolation) plus an integration job. This is a consequence of the deliberate fail-loud-at-import design, not a bug to fix.
- [ ] **From Task 23:** a check that the built web app requests **no external origin**. Belongs here, not in the backend E2E, because it can only be observed on the build output: `flutter build web`, then grep `frontend/build/web` for `gstatic.com` / `googleapis.com` / `//` absolute hosts. The CanvasKit half is already fixed (`web/flutter_bootstrap.js` sets `canvasKitBaseUrl`); the **outstanding P0 residue is one request to `fonts.gstatic.com`** for the Roboto fallback, whose lever is the bootstrap's `fontFallbackBaseUrl`. Do not claim it is fixed without loading the page — that assertion was made wrongly once already (commit `009da2d`).
- [ ] **REQUIRED SUB-SKILL** at finish: superpowers:verification-before-completion — full `uv run pytest`, `flutter test`, `ruff check`, show outputs.

---

## Out of scope (do not build, per spec)

Section 13 flow, compliance-scan agent, email alerts, PDF statement parsing, deposits/PRS tables, HMRC direct filing, letting-agent reconciliation, HMO units, rent schedules, tenant contacts, chat surface.

## Known inputs still needed from Mahmud

1. Bank list (personal + Ltd accounts) → real parser fixtures (Task 8 swaps generic fixtures).
2. Anthropic API key for the worker env.
3. Supabase cloud project (local-only until iteration 1 works; then `supabase link` + `db push`).
